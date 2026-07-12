(function (root) {
    'use strict';

    const LLM_REFINE_MODES = Object.freeze(['off', 'refine', 'translate']);
    const TRANSLATION_UI_MODES = Object.freeze(['fast', 'accurate', 'hybrid']);
    const DEFAULT_TRANSLATION_UI_MODE = 'hybrid';
    const TRANSLATION_UI_MODE_TO_LLM = Object.freeze({
        fast: 'off',
        accurate: 'translate',
        hybrid: 'refine',
    });
    const TRANSLATION_UI_MODE_STORAGE_KEY = 'translationUiMode';

    function normalizeLlmRefineMode(mode) {
        const value = (mode || '').toString().trim().toLowerCase();
        return LLM_REFINE_MODES.includes(value) ? value : 'off';
    }

    function normalizeTranslationUiMode(mode) {
        return TRANSLATION_UI_MODES.includes(mode) ? mode : DEFAULT_TRANSLATION_UI_MODE;
    }

    function create(options = {}) {
        const fetchImpl = options.fetch || root.fetch;
        const storage = options.storage || root.localStorage;
        const settingsStore = options.settingsStore;
        const logger = options.console || root.console || { warn() {}, error() {} };
        const getSession = typeof options.getSession === 'function'
            ? options.getSession
            : () => options.session;
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const actions = options.actions || {};
        const enforceTranslateSegmentMode = typeof actions.enforceTranslateSegmentMode === 'function'
            ? actions.enforceTranslateSegmentMode
            : () => {};
        const updateSegmentModeButton = typeof actions.updateSegmentModeButton === 'function'
            ? actions.updateSegmentModeButton
            : () => {};
        const renderSubtitles = typeof actions.renderSubtitles === 'function'
            ? actions.renderSubtitles
            : () => {};
        const updateTranslationModeHint = typeof actions.updateTranslationModeHint === 'function'
            ? actions.updateTranslationModeHint
            : () => {};
        const renderTranslationModePicker = typeof actions.renderTranslationModePicker === 'function'
            ? actions.renderTranslationModePicker
            : () => {};
        const restartRecognition = typeof actions.restartRecognition === 'function'
            ? actions.restartRecognition
            : () => {};

        if (!settingsStore) {
            throw new TypeError('TranslationModeController.create requires settingsStore');
        }
        for (const method of [
            'loadLlmRefineMode',
            'saveLlmRefineMode',
            'readTranslationUiMode',
        ]) {
            if (typeof settingsStore[method] !== 'function') {
                throw new TypeError(`TranslationModeController settingsStore requires ${method}`);
            }
        }
        if (typeof fetchImpl !== 'function') {
            throw new TypeError('TranslationModeController.create requires fetch');
        }

        let llmRefineAvailable = false;
        let defaultLlmRefineMode = null;
        let llmRefineMode = normalizeLlmRefineMode(settingsStore.loadLlmRefineMode());
        let translationUiMode = normalizeTranslationUiMode(
            settingsStore.readTranslationUiMode() || DEFAULT_TRANSLATION_UI_MODE,
        );
        let translationModeSynced = false;

        function session() {
            const value = getSession();
            if (!value) {
                throw new TypeError('TranslationModeController session is not available');
            }
            return value;
        }

        function persistTranslationUiMode(mode) {
            try {
                storage.setItem(TRANSLATION_UI_MODE_STORAGE_KEY, mode);
            } catch (error) {
                // Preserve the existing best-effort localStorage behavior.
            }
        }

        function isTranslateMode() {
            return llmRefineMode === 'translate';
        }

        function applyLlmRefineMode(mode, applyOptions = {}) {
            const normalized = normalizeLlmRefineMode(mode);
            const previous = llmRefineMode;
            const wasTranslate = previous === 'translate';
            llmRefineMode = normalized;

            if (applyOptions.persist !== false) {
                if (!settingsStore.saveLlmRefineMode(llmRefineMode)) {
                    logger.warn('Unable to persist LLM refine mode');
                }
            }

            session().applyLlmMode(llmRefineMode, previous);
            if (llmRefineMode === 'translate') {
                enforceTranslateSegmentMode();
            }
            updateSegmentModeButton();
            if (wasTranslate && llmRefineMode !== 'translate') {
                renderSubtitles();
            }
            return llmRefineMode;
        }

        function forceUnavailable() {
            llmRefineMode = 'off';
            session().disableLlmBoundary();
        }

        async function setTranslationUiMode(mode, setOptions = {}) {
            const normalized = normalizeTranslationUiMode(mode);
            const previous = translationUiMode;
            translationUiMode = normalized;
            session().noteHybridBoundary(normalized, previous);
            persistTranslationUiMode(normalized);
            applyLlmRefineMode(TRANSLATION_UI_MODE_TO_LLM[normalized], { persist: true });
            updateTranslationModeHint();
            if (setOptions.silent) return true;

            try {
                const response = await fetchImpl('/translation-mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: normalized }),
                });
                if (!response.ok) {
                    throw new Error(`translation-mode returned ${response.status}`);
                }
                const data = await response.json().catch(() => ({}));
                if (data && data.needs_restart && setOptions.restartIfNeeded) {
                    void restartRecognition({ auto: true });
                }
                return true;
            } catch (error) {
                logger.warn('Failed to set translation mode:', error);
                translationUiMode = previous;
                session().noteHybridBoundary(previous, normalized);
                persistTranslationUiMode(previous);
                applyLlmRefineMode(TRANSLATION_UI_MODE_TO_LLM[previous], { persist: true });
                renderTranslationModePicker();
                return false;
            }
        }

        function applyBackendConfig(data = {}, configOptions = {}) {
            llmRefineAvailable = !!data.llm_refine_available;
            if (!llmRefineAvailable) {
                forceUnavailable();
            }

            translationUiMode = normalizeTranslationUiMode(
                settingsStore.readTranslationUiMode() || DEFAULT_TRANSLATION_UI_MODE,
            );
            renderTranslationModePicker();

            const nextBootId = typeof data.boot_id === 'string' ? data.boot_id : '';
            const currentBootId = typeof configOptions.currentBootId === 'string'
                ? configOptions.currentBootId
                : '';
            if (nextBootId && currentBootId && nextBootId !== currentBootId) {
                translationModeSynced = false;
            }

            let syncPromise = null;
            if (llmRefineAvailable && !translationModeSynced) {
                translationModeSynced = true;
                syncPromise = setTranslationUiMode(translationUiMode, { restartIfNeeded: true });
            }
            if (typeof data.llm_refine_default_mode === 'string') {
                defaultLlmRefineMode = normalizeLlmRefineMode(data.llm_refine_default_mode);
            }
            return syncPromise;
        }

        async function fetchLlmRefineStatus() {
            if (!llmRefineAvailable) {
                forceUnavailable();
                enforceTranslateSegmentMode();
                return false;
            }
            if (!getRuntimeState().lockManualControls) {
                return false;
            }

            try {
                const response = await fetchImpl('/llm-refine');
                if (!response.ok) return false;
                const data = await response.json();
                if (!data) return false;
                const serverMode = normalizeLlmRefineMode(
                    typeof data.mode === 'string'
                        ? data.mode
                        : (data.enabled ? 'refine' : 'off'),
                );
                const preferred = normalizeLlmRefineMode(defaultLlmRefineMode || serverMode);
                applyLlmRefineMode(preferred, { persist: false });
                return true;
            } catch (error) {
                logger.error('Error fetching LLM refine status:', error);
                return false;
            }
        }

        function setTranslationModeSynced(value) {
            translationModeSynced = !!value;
        }

        function getDebugState() {
            return {
                llmRefineAvailable,
                defaultLlmRefineMode,
                llmRefineMode,
                translationUiMode,
                translationModeSynced,
            };
        }

        return {
            applyBackendConfig,
            applyLlmRefineMode,
            fetchLlmRefineStatus,
            getAvailableTranslationModes: () => [...TRANSLATION_UI_MODES],
            getDebugState,
            getLlmRefineMode: () => llmRefineMode,
            getTranslationUiMode: () => translationUiMode,
            isAvailable: () => llmRefineAvailable,
            isTranslateMode,
            setTranslationModeSynced,
            setTranslationUiMode,
        };
    }

    const api = {
        DEFAULT_TRANSLATION_UI_MODE,
        LLM_REFINE_MODES,
        TRANSLATION_UI_MODES,
        TRANSLATION_UI_MODE_TO_LLM,
        create,
        normalizeLlmRefineMode,
        normalizeTranslationUiMode,
    };
    root.TranslationModeController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

(function (root) {
    'use strict';

    const SEGMENT_MODES = Object.freeze(['translation', 'endpoint', 'punctuation']);
    const TRANSLATE_SEGMENT_MODES = Object.freeze(['endpoint', 'punctuation']);

    function normalizeSegmentMode(mode) {
        const value = (mode || '').toString().trim();
        return SEGMENT_MODES.includes(value) ? value : null;
    }

    function create(options = {}) {
        const fetchImpl = options.fetch || root.fetch;
        const storage = options.storage || root.localStorage;
        const settingsStore = options.settingsStore;
        const button = options.button || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const logger = options.console || root.console || { error() {} };
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const isTranslateMode = typeof options.isTranslateMode === 'function'
            ? options.isTranslateMode
            : () => false;
        const renderPicker = typeof options.renderPicker === 'function'
            ? options.renderPicker
            : () => {};

        if (!settingsStore || typeof settingsStore.loadSegmentMode !== 'function') {
            throw new TypeError('SegmentModeController.create requires settingsStore');
        }
        if (typeof fetchImpl !== 'function') {
            throw new TypeError('SegmentModeController.create requires fetch');
        }
        if (!storage || typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') {
            throw new TypeError('SegmentModeController.create requires storage');
        }

        let segmentMode = normalizeSegmentMode(settingsStore.loadSegmentMode()) || 'punctuation';
        let initialized = false;

        function getAvailableModes() {
            return isTranslateMode() ? [...TRANSLATE_SEGMENT_MODES] : [...SEGMENT_MODES];
        }

        function updateButton() {
            if (!button) return false;
            const translateLocked = isTranslateMode();
            if (segmentMode === 'translation') {
                button.title = t('segment_translation');
            } else if (segmentMode === 'endpoint') {
                button.title = translateLocked
                    ? t('segment_endpoint_no_translation')
                    : t('segment_endpoint');
            } else {
                button.title = translateLocked
                    ? t('segment_punctuation_no_translation')
                    : t('segment_punctuation');
            }
            return true;
        }

        async function setMode(mode) {
            const runtime = getRuntimeState() || {};
            if (runtime.lockManualControls || runtime.segmentModeSupported === false) {
                return false;
            }
            if (isTranslateMode() && mode === 'translation') return false;

            try {
                const response = await fetchImpl('/segment-mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode }),
                });
                if (!response.ok) {
                    logger.error('Failed to set segment mode');
                    return false;
                }
                segmentMode = mode;
                storage.setItem('segmentMode', mode);
                updateButton();
                renderPicker();
                return true;
            } catch (error) {
                logger.error('Error setting segment mode:', error);
                return false;
            }
        }

        function enforceTranslateMode() {
            if (!isTranslateMode() || segmentMode !== 'translation') return false;
            segmentMode = 'punctuation';
            storage.setItem('segmentMode', segmentMode);
            updateButton();
            void setMode('punctuation');
            return true;
        }

        function applyBackendConfig(data = {}) {
            const backendMode = normalizeSegmentMode(data.segment_mode);
            const storedMode = normalizeSegmentMode(storage.getItem('segmentMode'));
            const runtime = getRuntimeState() || {};

            if (runtime.lockManualControls) {
                if (backendMode) {
                    segmentMode = backendMode;
                    storage.setItem('segmentMode', segmentMode);
                }
            } else if (storedMode) {
                segmentMode = storedMode;
                if (backendMode && backendMode !== storedMode) {
                    void setMode(storedMode);
                }
            } else if (backendMode) {
                segmentMode = backendMode;
                storage.setItem('segmentMode', segmentMode);
            }
            updateButton();
            return segmentMode;
        }

        function handleBackendChanged(data) {
            if (!data || typeof data.mode !== 'string') return false;
            segmentMode = data.mode;
            storage.setItem('segmentMode', data.mode);
            updateButton();
            renderPicker();
            enforceTranslateMode();
            return true;
        }

        function cycle() {
            const available = getAvailableModes();
            const currentIndex = available.indexOf(segmentMode);
            const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % available.length : 0;
            const nextMode = available[nextIndex];
            void setMode(nextMode);
            return nextMode;
        }

        function onButtonClick() {
            cycle();
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (button) button.addEventListener('click', onButtonClick);
            return true;
        }

        function destroy() {
            if (!initialized) return false;
            if (button) button.removeEventListener('click', onButtonClick);
            initialized = false;
            return true;
        }

        function getDebugState() {
            return { segmentMode, initialized };
        }

        return {
            applyBackendConfig,
            cycle,
            destroy,
            enforceTranslateMode,
            getAvailableModes,
            getDebugState,
            getMode: () => segmentMode,
            handleBackendChanged,
            init,
            setMode,
            updateButton,
        };
    }

    const api = { SEGMENT_MODES, TRANSLATE_SEGMENT_MODES, create, normalizeSegmentMode };
    root.SegmentModeController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

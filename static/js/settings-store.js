(function (root) {
    'use strict';

    const KEYS = Object.freeze({
        server: 'subtitleServer.v1',
        provider: 'providerSettings.v1',
        uiTranslationMode: 'uiTranslationMode',
        translationUiMode: 'translationUiMode',
        llmTranslationMode: 'llmTranslationMode',
        llmRefineMode: 'llmRefineMode',
        llmRefineEnabled: 'llmRefineEnabled',
        segmentMode: 'segmentMode',
        displayMode: 'displayMode',
        subtitleFlowDirection: 'subtitleFlowDirection',
        autoRestartEnabled: 'autoRestartEnabled',
        sleepOnSilenceEnabled: 'sleepOnSilenceEnabled',
        bottomSafeAreaEnabled: 'bottomSafeAreaEnabled',
        bundledCjkFontEnabled: 'useBundledCjkFont',
        theme: 'theme',
        audioSource: 'audioSource',
        clientUpdateReminder: 'clientUpdateReminderLastShown',
    });

    const UI_TRANSLATION_MODES = ['none', 'one_way', 'two_way'];
    const TRANSLATION_UI_MODES = ['fast', 'accurate', 'hybrid'];
    const LLM_REFINE_MODES = ['off', 'refine', 'translate'];
    const SEGMENT_MODES = ['translation', 'endpoint', 'punctuation'];
    const AUDIO_SOURCES = ['system', 'microphone', 'mix'];
    const SUBTITLE_FLOW_DIRECTIONS = ['up', 'down'];

    function safeHttpUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        try {
            const url = new URL(raw);
            return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
        } catch (error) {
            return '';
        }
    }

    function normalizeServerUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        try {
            const url = new URL(raw);
            const pathname = url.pathname.replace(/\/+$/, '');
            return `${url.protocol}//${url.host.toLowerCase()}${pathname}`;
        } catch (error) {
            return raw.replace(/\/+$/, '');
        }
    }

    function create(options = {}) {
        let storage = options.storage;
        if (!Object.prototype.hasOwnProperty.call(options, 'storage')) {
            try {
                storage = root.localStorage;
            } catch (error) {
                storage = null;
            }
        }
        const getRelayServerUrl = typeof options.getRelayServerUrl === 'function'
            ? options.getRelayServerUrl
            : () => '';

        function read(key) {
            try {
                return storage && typeof storage.getItem === 'function' ? storage.getItem(key) : null;
            } catch (error) {
                return null;
            }
        }

        function write(key, value) {
            try {
                if (!storage || typeof storage.setItem !== 'function') return false;
                storage.setItem(key, String(value));
                return true;
            } catch (error) {
                return false;
            }
        }

        function remove(key) {
            try {
                if (!storage || typeof storage.removeItem !== 'function') return false;
                storage.removeItem(key);
                return true;
            } catch (error) {
                return false;
            }
        }

        function clear() {
            try {
                if (!storage || typeof storage.clear !== 'function') return false;
                storage.clear();
                return true;
            } catch (error) {
                return false;
            }
        }

        function currentServerKey() {
            try {
                return normalizeServerUrl(getRelayServerUrl());
            } catch (error) {
                return '';
            }
        }

        function loadServerSettingsRaw() {
            try {
                const raw = read(KEYS.server);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        const result = Object.assign({ mode: null, modeChosen: false }, parsed);
                        if (!result.servers || typeof result.servers !== 'object') result.servers = {};
                        return result;
                    }
                }
            } catch (error) {
                // Preserve the current frontend's bad-data fallback.
            }
            return { mode: null, modeChosen: false, servers: {} };
        }

        function loadServerSettings() {
            const raw = loadServerSettingsRaw();
            const key = currentServerKey();
            let credentials = key ? raw.servers[key] : null;
            if (!credentials && (raw.token || raw.displayName || raw.trustRank)) {
                credentials = {
                    token: raw.token || '',
                    displayName: raw.displayName || '',
                    trustRank: raw.trustRank || '',
                };
            }
            credentials = credentials || { token: '', displayName: '', trustRank: '' };
            return {
                mode: raw.mode,
                modeChosen: raw.modeChosen,
                token: credentials.token || '',
                displayName: credentials.displayName || '',
                trustRank: credentials.trustRank || '',
                servers: raw.servers,
            };
        }

        function saveServerSettings(settings) {
            try {
                const raw = loadServerSettingsRaw();
                raw.mode = settings.mode;
                raw.modeChosen = settings.modeChosen;
                const key = currentServerKey();
                if (key) {
                    raw.servers[key] = {
                        token: settings.token || '',
                        displayName: settings.displayName || '',
                        trustRank: settings.trustRank || '',
                    };
                    delete raw.token;
                    delete raw.displayName;
                    delete raw.trustRank;
                }
                return write(KEYS.server, JSON.stringify(raw));
            } catch (error) {
                return false;
            }
        }

        function loadProviderSettings() {
            try {
                const raw = read(KEYS.provider);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        if (!parsed.keys || typeof parsed.keys !== 'object') parsed.keys = {};
                        return parsed;
                    }
                }
            } catch (error) {
                // Preserve the current frontend's bad-data fallback.
            }
            return { providerOverride: null, keys: {} };
        }

        function saveProviderSettings(settings) {
            try {
                return write(KEYS.provider, JSON.stringify(settings));
            } catch (error) {
                return false;
            }
        }

        function loadUiTranslationMode() {
            const value = read(KEYS.uiTranslationMode);
            return UI_TRANSLATION_MODES.includes(value) ? value : null;
        }

        function saveUiTranslationMode(mode) {
            return UI_TRANSLATION_MODES.includes(mode) && write(KEYS.uiTranslationMode, mode);
        }

        function readTranslationUiMode() {
            const value = read(KEYS.translationUiMode);
            if (value === 'refine') return 'hybrid';
            return TRANSLATION_UI_MODES.includes(value) ? value : null;
        }

        function loadTranslationUiMode() {
            return readTranslationUiMode() || 'hybrid';
        }

        function saveTranslationUiMode(mode) {
            const normalized = TRANSLATION_UI_MODES.includes(mode) ? mode : 'hybrid';
            return write(KEYS.translationUiMode, normalized);
        }

        function loadLlmRefineMode() {
            let value = read(KEYS.llmTranslationMode);
            if (!LLM_REFINE_MODES.includes(String(value || '').trim().toLowerCase())) {
                value = read(KEYS.llmRefineMode);
            }
            const normalized = String(value || '').trim().toLowerCase();
            if (LLM_REFINE_MODES.includes(normalized)) return normalized;
            const legacy = read(KEYS.llmRefineEnabled);
            return legacy === null ? null : (legacy === 'true' ? 'refine' : 'off');
        }

        function saveLlmRefineMode(mode) {
            const normalized = LLM_REFINE_MODES.includes(String(mode || '').trim().toLowerCase())
                ? String(mode).trim().toLowerCase()
                : 'off';
            const first = write(KEYS.llmRefineMode, normalized);
            const second = write(KEYS.llmTranslationMode, normalized);
            const third = write(KEYS.llmRefineEnabled, normalized === 'off' ? 'false' : 'true');
            return first && second && third;
        }

        function loadSegmentMode() {
            const value = read(KEYS.segmentMode) || 'punctuation';
            return SEGMENT_MODES.includes(value) ? value : 'punctuation';
        }

        function saveSegmentMode(mode) {
            return SEGMENT_MODES.includes(mode) && write(KEYS.segmentMode, mode);
        }

        function loadDisplayMode() {
            return read(KEYS.displayMode) || 'both';
        }

        function saveDisplayMode(mode) {
            return write(KEYS.displayMode, mode);
        }

        function loadSubtitleFlowDirection() {
            const value = String(read(KEYS.subtitleFlowDirection) || '').trim().toLowerCase();
            return SUBTITLE_FLOW_DIRECTIONS.includes(value) ? value : 'up';
        }

        function saveSubtitleFlowDirection(direction) {
            const normalized = String(direction || '').trim().toLowerCase();
            return SUBTITLE_FLOW_DIRECTIONS.includes(normalized)
                && write(KEYS.subtitleFlowDirection, normalized);
        }

        function loadBoolean(key, defaultValue = false) {
            const value = read(key);
            return value === null ? !!defaultValue : value === 'true';
        }

        function saveBoolean(key, enabled) {
            return write(key, enabled ? 'true' : 'false');
        }

        function loadAudioSource() {
            const value = String(read(KEYS.audioSource) || '').trim().toLowerCase();
            return AUDIO_SOURCES.includes(value) ? value : 'system';
        }

        function loadClientUpdateReminder() {
            return parseInt(read(KEYS.clientUpdateReminder) || '0', 10) || 0;
        }

        const preferences = {
            loadUiTranslationMode,
            saveUiTranslationMode,
            readTranslationUiMode,
            loadTranslationUiMode,
            saveTranslationUiMode,
            loadLlmRefineMode,
            saveLlmRefineMode,
            loadSegmentMode,
            saveSegmentMode,
            loadDisplayMode,
            saveDisplayMode,
            loadSubtitleFlowDirection,
            saveSubtitleFlowDirection,
            loadAutoRestartEnabled: () => loadBoolean(KEYS.autoRestartEnabled, true),
            saveAutoRestartEnabled: (value) => saveBoolean(KEYS.autoRestartEnabled, value),
            readSleepOnSilenceEnabled: () => {
                const value = read(KEYS.sleepOnSilenceEnabled);
                return value === null ? null : value === 'true';
            },
            loadSleepOnSilenceEnabled: () => loadBoolean(KEYS.sleepOnSilenceEnabled, true),
            saveSleepOnSilenceEnabled: (value) => saveBoolean(KEYS.sleepOnSilenceEnabled, value),
            loadBottomSafeAreaEnabled: () => loadBoolean(KEYS.bottomSafeAreaEnabled, false),
            saveBottomSafeAreaEnabled: (value) => saveBoolean(KEYS.bottomSafeAreaEnabled, value),
            loadBundledCjkFontEnabled: () => loadBoolean(KEYS.bundledCjkFontEnabled, false),
            saveBundledCjkFontEnabled: (value) => saveBoolean(KEYS.bundledCjkFontEnabled, value),
            loadTheme: () => read(KEYS.theme),
            saveTheme: (value) => write(KEYS.theme, value),
            loadAudioSource,
            saveAudioSource: (value) => write(KEYS.audioSource, value),
            loadClientUpdateReminder,
            saveClientUpdateReminder: (value) => write(KEYS.clientUpdateReminder, value),
        };

        return Object.assign({
            clear,
            read,
            write,
            remove,
            loadServerSettingsRaw,
            loadServerSettings,
            saveServerSettings,
            loadProviderSettings,
            saveProviderSettings,
            preferences,
        }, preferences);
    }

    const api = {
        KEYS,
        create,
        safeHttpUrl,
        normalizeServerUrl,
    };

    root.SettingsStore = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

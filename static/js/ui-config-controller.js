(function (root) {
    'use strict';

    function create(options = {}) {
        const fetchImpl = options.fetch || root.fetch;
        const safeHttpUrl = options.safeHttpUrl;
        const normalizeSonioxRegion = options.normalizeSonioxRegion;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const translationMode = options.translationModeController;
        const segmentMode = options.segmentModeController;
        const theme = options.themeController;
        const speakerLabels = options.speakerLabelController;
        const actions = options.actions || {};
        const logger = options.console || root.console || { error() {} };

        if (typeof fetchImpl !== 'function') {
            throw new TypeError('UiConfigController.create requires fetch');
        }
        if (typeof safeHttpUrl !== 'function' || typeof normalizeSonioxRegion !== 'function') {
            throw new TypeError('UiConfigController.create requires URL/region normalizers');
        }
        if (!translationMode || typeof translationMode.applyBackendConfig !== 'function') {
            throw new TypeError('UiConfigController.create requires translation mode controller');
        }
        if (!segmentMode || typeof segmentMode.applyBackendConfig !== 'function') {
            throw new TypeError('UiConfigController.create requires segment mode controller');
        }
        if (!theme || typeof theme.setChromaEnabled !== 'function') {
            throw new TypeError('UiConfigController.create requires theme controller');
        }
        if (
            !speakerLabels
            || typeof speakerLabels.applyBackendConfig !== 'function'
            || typeof speakerLabels.applyVisibility !== 'function'
        ) {
            throw new TypeError('UiConfigController.create requires speaker label controller');
        }

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            const action = actions[name];
            return typeof action === 'function' ? action(...args) : undefined;
        }

        function apply(data = {}) {
            const previous = { ...state() };
            const lockManualControls = !!data.lock_manual_controls;
            updateState({ lockManualControls });
            translationMode.applyBackendConfig(data, {
                currentBootId: previous.backendBootId,
            });

            const providerPatch = {};
            if (
                Number.isFinite(Number(data.soniox_no_translation_factor))
                && Number(data.soniox_no_translation_factor) > 0
            ) {
                providerPatch.sonioxNoTranslationFactor = Math.min(
                    1,
                    Number(data.soniox_no_translation_factor),
                );
            }
            if (
                typeof data.translation_target_lang === 'string'
                && data.translation_target_lang.trim()
            ) {
                const language = data.translation_target_lang.trim().toLowerCase();
                providerPatch.defaultTranslationTargetLang = language;
                providerPatch.currentTranslationTargetLang = language;
            }
            let translationProvider = previous.translationProvider;
            if (typeof data.provider === 'string' && data.provider.trim()) {
                translationProvider = data.provider.trim().toLowerCase();
                providerPatch.translationProvider = translationProvider;
            }
            updateState(providerPatch);
            if (
                Object.prototype.hasOwnProperty.call(providerPatch, 'translationProvider')
                && translationProvider !== previous.translationProvider
            ) {
                call('sessionCostReset');
            }
            if (Array.isArray(data.languages)) {
                call('setLanguageListFromCodes', data.languages);
            }

            if (data.capabilities && typeof data.capabilities === 'object') {
                updateState({
                    segmentModeSupported: data.capabilities.segment_mode !== false,
                    twoWaySupported: data.capabilities.two_way_translation === true,
                });
            }

            const runtimePatch = { setupRequired: !!data.setup_required };
            if (typeof data.boot_id === 'string') runtimePatch.backendBootId = data.boot_id;
            if (data.env_key_present && typeof data.env_key_present === 'object') {
                runtimePatch.envKeyPresent = {
                    soniox: !!data.env_key_present.soniox,
                    gemini: !!data.env_key_present.gemini,
                };
            }
            if (typeof data.key_source === 'string') runtimePatch.backendKeySource = data.key_source;
            if (typeof data.soniox_region === 'string' && data.soniox_region.trim()) {
                runtimePatch.backendSonioxRegion = normalizeSonioxRegion(data.soniox_region);
            }
            if (typeof data.soniox_custom_url === 'boolean') {
                runtimePatch.backendSonioxCustomUrl = data.soniox_custom_url;
            }
            if (typeof data.relay_available === 'boolean') {
                runtimePatch.relayAvailable = data.relay_available;
            }
            if (typeof data.server_url === 'string') runtimePatch.relayServerUrl = data.server_url;
            runtimePatch.creditsPurchaseUrl = safeHttpUrl(data.credits_purchase_url);
            updateState(runtimePatch);
            call('resetFirstRedeemBonus', data.first_redeem_bonus_credits);

            const accountPatch = { clientUpdateUrl: safeHttpUrl(data.client_update_url) };
            if (typeof data.client_version === 'string' && data.client_version.trim()) {
                accountPatch.clientVersion = data.client_version.trim();
            }
            if (typeof data.client_latest_version === 'string') {
                accountPatch.clientLatestVersion = data.client_latest_version.trim();
            }
            if (typeof data.client_minimum_version === 'string') {
                accountPatch.clientMinimumVersion = data.client_minimum_version.trim();
            }
            if (typeof data.client_update_notes === 'string') {
                accountPatch.clientUpdateNotes = data.client_update_notes.trim();
            }
            if (typeof data.mode === 'string') accountPatch.backendMode = data.mode;
            if (typeof data.logged_in === 'boolean') accountPatch.backendLoggedIn = data.logged_in;
            updateState(accountPatch);
            call('updateBalanceBarVisibility');
            call('updateAccountSection');

            const translationPatch = {};
            if (typeof data.translation_mode === 'string' && data.translation_mode.trim()) {
                translationPatch.backendTranslationMode = data.translation_mode.trim().toLowerCase();
            }
            if (typeof data.target_lang_1 === 'string' && data.target_lang_1.trim()) {
                translationPatch.backendTargetLang1 = data.target_lang_1.trim().toLowerCase();
            }
            if (typeof data.target_lang_2 === 'string' && data.target_lang_2.trim()) {
                translationPatch.backendTargetLang2 = data.target_lang_2.trim().toLowerCase();
            }
            updateState(translationPatch);
            const translatedState = state();
            let uiTranslationMode = translatedState.uiTranslationMode;
            if (!uiTranslationMode) {
                uiTranslationMode = translatedState.backendTranslationMode || 'one_way';
                updateState({ uiTranslationMode });
            }
            translationProvider = state().translationProvider;
            updateState({
                suppressTranslationDisplay: translationProvider === 'gemini'
                    && uiTranslationMode === 'none',
            });
            call('updateSettingsButtonVisibility');
            segmentMode.applyBackendConfig(data);

            if (typeof data.custom_font_available === 'boolean') {
                updateState({ customFontAvailable: data.custom_font_available });
                if (!data.custom_font_available) {
                    call('applyBundledCjkFontPreference', false, {
                        persist: false,
                        sync: false,
                    });
                }
            }
            call('renderBundledCjkFontPicker');

            speakerLabels.applyBackendConfig(data);
            call('applySleepOnSilenceConfig', data);
            call('renderRuntimeSettingsPickers');
            if (typeof data.enable_chroma_theme === 'boolean') {
                theme.setChromaEnabled(data.enable_chroma_theme);
            }
            speakerLabels.applyVisibility();
            call('applyLockPauseRestartControlsUI');
            call('enforceTranslateSegmentMode');
            return data;
        }

        async function fetchUiConfig() {
            try {
                const response = await fetchImpl('/ui-config');
                if (!response.ok) return false;
                apply(await response.json());
                return true;
            } catch (error) {
                logger.error('Error fetching UI config:', error);
                return false;
            }
        }

        return { apply, fetch: fetchUiConfig };
    }

    const api = { create };
    root.UiConfigController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

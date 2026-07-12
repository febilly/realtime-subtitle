(function (root) {
    'use strict';

    const TRANSLATION_UI_MODES = ['fast', 'accurate', 'hybrid'];
    const DEFAULT_TRANSLATION_UI_MODE = 'hybrid';
    const EMPTY_MICROPHONE_DATA = {
        available: false,
        default: null,
        devices: [],
        selected_id: '',
    };

    function normalizeMicrophoneData(value) {
        const data = value && typeof value === 'object' ? value : {};
        return {
            available: !!data.available,
            default: data.default && typeof data.default === 'object' ? data.default : null,
            devices: Array.isArray(data.devices) ? data.devices : [],
            selected_id: String(data.selected_id || ''),
        };
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const storage = options.storage || root.localStorage;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const localizeBackendMessage = typeof options.localizeBackendMessage === 'function'
            ? options.localizeBackendMessage
            : (message) => String(message || '');
        const buildCustomSelect = typeof options.buildCustomSelect === 'function'
            ? options.buildCustomSelect
            : null;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const actions = options.actions || {};
        const elements = options.elements || {};
        const consoleRef = options.console || root.console || { warn() {}, error() {} };
        const pickers = {
            microphone: null,
            autoRestart: null,
            speakerLabels: null,
            bundledCjkFont: null,
            translationMode: null,
            segmentMode: null,
        };
        let microphoneData = normalizeMicrophoneData(options.microphoneData || EMPTY_MICROPHONE_DATA);
        let draft = {};

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function setDraft(patch) {
            draft = { ...draft, ...(patch || {}) };
            return getDraft();
        }

        function pickerValue(name, fallback) {
            const picker = pickers[name];
            return picker && typeof picker.value === 'string' ? picker.value : fallback;
        }

        function getDraft() {
            return {
                ...draft,
                microphoneDeviceId: pickerValue('microphone', draft.microphoneDeviceId || ''),
                autoRestartEnabled: pickerValue(
                    'autoRestart',
                    draft.autoRestartEnabled === false ? 'false' : 'true',
                ) !== 'false',
                hideSpeakerLabels: pickerValue(
                    'speakerLabels',
                    draft.hideSpeakerLabels ? 'hide' : 'show',
                ) === 'hide',
                useBundledCjkFont: pickerValue(
                    'bundledCjkFont',
                    draft.useBundledCjkFont ? 'true' : 'false',
                ) === 'true',
                translationUiMode: pickerValue(
                    'translationMode',
                    draft.translationUiMode || DEFAULT_TRANSLATION_UI_MODE,
                ),
                segmentMode: pickerValue('segmentMode', draft.segmentMode || ''),
            };
        }

        function getPickers() {
            return { ...pickers };
        }

        function getMicrophoneData() {
            return normalizeMicrophoneData(microphoneData);
        }

        function replaceHost(host, name, selectOptions, config = {}) {
            if (!host) {
                pickers[name] = null;
                return null;
            }
            host.innerHTML = '';
            if (!buildCustomSelect) {
                throw new Error('SettingsRuntime requires buildCustomSelect to render pickers');
            }
            const picker = buildCustomSelect(selectOptions, config);
            pickers[name] = picker;
            host.appendChild(picker);
            return picker;
        }

        function selectedProvider() {
            const current = state();
            return current.selectedProvider || current.provider || 'soniox';
        }

        function getTranslationModes() {
            const configured = state().translationUiModes;
            return Array.isArray(configured) && configured.length
                ? configured.filter((mode) => TRANSLATION_UI_MODES.includes(mode))
                : [...TRANSLATION_UI_MODES];
        }

        function getSegmentModes() {
            const configured = state().segmentModes;
            return Array.isArray(configured) ? configured.filter(Boolean) : [];
        }

        function getStoredHideSpeakerLabelsSetting() {
            const current = state();
            const settings = current.providerSettings && typeof current.providerSettings === 'object'
                ? current.providerSettings
                : {};
            return typeof settings.hideSpeakerLabels === 'boolean' ? settings.hideSpeakerLabels : null;
        }

        function desiredHideSpeakerLabels() {
            const stored = getStoredHideSpeakerLabelsSetting();
            return stored === null ? !!state().hideSpeakerLabels : stored;
        }

        function microphoneDefaultLabel() {
            const defaultName = microphoneData.default && microphoneData.default.name;
            return defaultName
                ? t('microphone_device_default', { name: defaultName })
                : t('microphone_device_default_unknown');
        }

        function renderMicrophoneDevicePicker() {
            const host = elements.microphoneDevicePickerHost;
            if (!host) {
                pickers.microphone = null;
                return null;
            }
            const selectOptions = [
                { value: '', label: microphoneDefaultLabel() },
                ...microphoneData.devices
                    .filter((device) => !device.is_default)
                    .map((device) => ({
                        value: String(device.id || ''),
                        label: String(device.name || device.id || ''),
                    }))
                    .filter((entry) => entry.value && entry.label),
            ];
            const selected = selectOptions.some((entry) => entry.value === microphoneData.selected_id)
                ? microphoneData.selected_id
                : '';
            setDraft({ microphoneDeviceId: selected });
            const picker = replaceHost(host, 'microphone', selectOptions, {
                value: selected,
                disabled: !microphoneData.available,
                onChange: (value) => setDraft({ microphoneDeviceId: value }),
            });
            if (elements.microphoneDeviceHint) {
                elements.microphoneDeviceHint.textContent = microphoneData.available
                    ? t('microphone_device_hint')
                    : t('microphone_device_unavailable');
            }
            return picker;
        }

        async function fetchMicrophoneDevices() {
            if (!elements.microphoneDeviceSection || !fetchRef) {
                return false;
            }
            try {
                const response = await fetchRef('/microphones');
                if (!response.ok) {
                    microphoneData = normalizeMicrophoneData(EMPTY_MICROPHONE_DATA);
                    renderMicrophoneDevicePicker();
                    return false;
                }
                microphoneData = normalizeMicrophoneData(await response.json());
                renderMicrophoneDevicePicker();
                return true;
            } catch (error) {
                consoleRef.error('Failed to fetch microphone devices:', error);
                microphoneData = normalizeMicrophoneData(EMPTY_MICROPHONE_DATA);
                renderMicrophoneDevicePicker();
                return false;
            }
        }

        async function saveMicrophoneDeviceSelection() {
            if (!elements.microphoneDeviceSection || !microphoneData.available) {
                return { ok: true };
            }
            if (!fetchRef) {
                return { ok: false, message: 'fetch unavailable' };
            }
            const id = getDraft().microphoneDeviceId;
            try {
                const response = await fetchRef('/microphone-device', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    return {
                        ok: false,
                        message: localizeBackendMessage(data && data.message) || `HTTP ${response.status}`,
                    };
                }
                microphoneData.selected_id = String((data && data.id) || id || '');
                setDraft({ microphoneDeviceId: microphoneData.selected_id });
                return { ok: true };
            } catch (error) {
                return { ok: false, message: String(error) };
            }
        }

        function renderAutoRestartPicker() {
            const enabled = !!state().autoRestartEnabled;
            setDraft({ autoRestartEnabled: enabled });
            return replaceHost(elements.autoRestartPickerHost, 'autoRestart', [
                { value: 'true', label: t('auto_restart_enabled') },
                { value: 'false', label: t('auto_restart_disabled') },
            ], {
                value: enabled ? 'true' : 'false',
                onChange: (value) => setDraft({ autoRestartEnabled: value !== 'false' }),
            });
        }

        function renderSpeakerLabelsPicker() {
            const supported = selectedProvider() === 'soniox';
            if (elements.speakerLabelsSettingField) {
                elements.speakerLabelsSettingField.hidden = !supported;
            }
            if (!supported) {
                if (elements.speakerLabelsPickerHost) elements.speakerLabelsPickerHost.innerHTML = '';
                pickers.speakerLabels = null;
                return null;
            }
            const hidden = desiredHideSpeakerLabels();
            setDraft({ hideSpeakerLabels: hidden });
            return replaceHost(elements.speakerLabelsPickerHost, 'speakerLabels', [
                { value: 'show', label: t('speaker_labels_enabled') },
                { value: 'hide', label: t('speaker_labels_disabled') },
            ], {
                value: hidden ? 'hide' : 'show',
                onChange: (value) => setDraft({ hideSpeakerLabels: value === 'hide' }),
            });
        }

        function renderBundledCjkFontPicker() {
            const host = elements.bundledCjkFontPickerHost;
            if (!host) {
                pickers.bundledCjkFont = null;
                return null;
            }
            host.innerHTML = '';
            const current = state();
            if (!current.customFontAvailable) {
                pickers.bundledCjkFont = null;
                setDraft({ useBundledCjkFont: false });
                const status = documentRef.createElement('span');
                status.className = 'font-not-detected-status';
                status.style.color = '#ef4444';
                status.style.fontSize = '0.95em';
                status.style.fontWeight = '500';
                status.textContent = t('custom_font_not_detected') || 'Not detected';
                host.appendChild(status);
                if (elements.bundledCjkFontHint) {
                    elements.bundledCjkFontHint.textContent = t('custom_font_missing_hint');
                }
                return null;
            }
            const enabled = !!current.useBundledCjkFont;
            setDraft({ useBundledCjkFont: enabled });
            const picker = replaceHost(host, 'bundledCjkFont', [
                { value: 'true', label: t('bundled_cjk_font_enabled') },
                { value: 'false', label: t('bundled_cjk_font_disabled') },
            ], {
                value: enabled ? 'true' : 'false',
                onChange: (value) => setDraft({ useBundledCjkFont: value === 'true' }),
            });
            if (elements.bundledCjkFontHint) {
                elements.bundledCjkFontHint.textContent = t('bundled_cjk_font_hint');
            }
            return picker;
        }

        async function syncBundledCjkFontPreference(enabled) {
            if (!fetchRef) return false;
            try {
                await fetchRef('/subtitle-font', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ use_bundled_cjk_fonts: !!enabled }),
                });
                return true;
            } catch (error) {
                consoleRef.warn('Failed to sync subtitle font preference:', error);
                return false;
            }
        }

        function applyBundledCjkFontPreference(enabled, applyOptions = {}) {
            const normalized = !!enabled;
            updateState({ useBundledCjkFont: normalized });
            if (documentRef && documentRef.body) {
                documentRef.body.classList.toggle('use-bundled-cjk-fonts', normalized);
            }
            renderBundledCjkFontPicker();
            if (applyOptions.persist) {
                try {
                    storage.setItem('useBundledCjkFont', normalized ? 'true' : 'false');
                } catch (error) {
                    consoleRef.warn('Unable to persist subtitle font preference:', error);
                }
            }
            if (applyOptions.sync) {
                void syncBundledCjkFontPreference(normalized);
            }
            return normalized;
        }

        function applyBundledCjkFontDraft() {
            const current = state();
            const enabled = !!current.customFontAvailable
                && !!pickers.bundledCjkFont
                && getDraft().useBundledCjkFont;
            return applyBundledCjkFontPreference(enabled, { persist: true, sync: true });
        }

        function translationModeLabel(mode) {
            return t(`translation_mode_${mode}`) || mode;
        }

        function translationModeCostHint(mode) {
            return mode === 'fast' ? '' : t('translation_cost_llm');
        }

        function updateTranslationModeHint() {
            if (!elements.translationModeHint) return;
            const mode = pickerValue(
                'translationMode',
                state().translationUiMode || DEFAULT_TRANSLATION_UI_MODE,
            );
            const hint = translationModeCostHint(mode);
            elements.translationModeHint.textContent = hint;
            elements.translationModeHint.hidden = !hint;
        }

        function renderTranslationModePicker() {
            const current = state();
            const shown = !!current.llmRefineAvailable && !current.lockManualControls;
            if (elements.translationModeSection) elements.translationModeSection.hidden = !shown;
            if (elements.translationModeSettingField) elements.translationModeSettingField.hidden = !shown;
            if (!shown) {
                if (elements.translationModePickerHost) elements.translationModePickerHost.innerHTML = '';
                pickers.translationMode = null;
                if (elements.translationModeHint) elements.translationModeHint.hidden = true;
                return null;
            }
            const modes = getTranslationModes();
            const fallback = modes.includes(current.defaultTranslationUiMode)
                ? current.defaultTranslationUiMode
                : DEFAULT_TRANSLATION_UI_MODE;
            const selected = modes.includes(current.translationUiMode) ? current.translationUiMode : fallback;
            setDraft({ translationUiMode: selected });
            const picker = replaceHost(
                elements.translationModePickerHost,
                'translationMode',
                modes.map((mode) => ({ value: mode, label: translationModeLabel(mode) })),
                {
                    value: selected,
                    onChange: (value) => {
                        setDraft({ translationUiMode: value });
                        updateTranslationModeHint();
                    },
                },
            );
            updateTranslationModeHint();
            return picker;
        }

        function segmentModeLabel(mode) {
            if (mode === 'translation') return t('segment_mode_translation');
            if (mode === 'endpoint') return t('segment_mode_endpoint');
            return t('segment_mode_punctuation');
        }

        function renderSegmentModePicker() {
            const current = state();
            if (elements.segmentModeSettingField) {
                elements.segmentModeSettingField.hidden = !current.segmentModeSupported;
            }
            if (!current.segmentModeSupported) {
                if (elements.segmentModePickerHost) elements.segmentModePickerHost.innerHTML = '';
                pickers.segmentMode = null;
                return null;
            }
            const modes = getSegmentModes();
            const selected = modes.includes(current.segmentMode) ? current.segmentMode : (modes[0] || '');
            setDraft({ segmentMode: selected });
            return replaceHost(
                elements.segmentModePickerHost,
                'segmentMode',
                modes.map((mode) => ({ value: mode, label: segmentModeLabel(mode) })),
                {
                    value: selected,
                    onChange: (value) => setDraft({ segmentMode: value }),
                },
            );
        }

        function renderRuntimeSettingsPickers() {
            if (elements.runtimeControlsSection) elements.runtimeControlsSection.hidden = false;
            renderAutoRestartPicker();
            renderSpeakerLabelsPicker();
            renderSegmentModePicker();
        }

        function renderSettingsPickers() {
            renderMicrophoneDevicePicker();
            renderRuntimeSettingsPickers();
            renderBundledCjkFontPicker();
            renderTranslationModePicker();
            return getPickers();
        }

        function writeProviderSettingsDraft(settings, provider = selectedProvider()) {
            const target = settings && typeof settings === 'object' ? settings : {};
            if (pickers.speakerLabels && provider === 'soniox') {
                target.hideSpeakerLabels = getDraft().hideSpeakerLabels;
            }
            return target;
        }

        async function actionSucceeded(name, ...args) {
            const action = actions[name];
            if (typeof action !== 'function') return false;
            const result = await action(...args);
            return result === true || !!(result && result.ok === true);
        }

        async function applyRuntimeControlSettings() {
            if (pickers.autoRestart && typeof pickers.autoRestart.value === 'string') {
                const enabled = getDraft().autoRestartEnabled;
                updateState({ autoRestartEnabled: enabled });
                storage.setItem('autoRestartEnabled', enabled ? 'true' : 'false');
                if (typeof actions.updateAutoRestartButton === 'function') {
                    actions.updateAutoRestartButton();
                }
            }

            const currentProvider = selectedProvider();
            if (pickers.speakerLabels && currentProvider === 'soniox') {
                const requestedHidden = getDraft().hideSpeakerLabels;
                if (requestedHidden !== !!state().hideSpeakerLabels) {
                    const ok = await actionSucceeded('setSpeakerLabelsHidden', requestedHidden);
                    if (!ok) return { ok: false, message: t('backend_speaker_labels_disabled') };
                }
            }

            const segmentModes = getSegmentModes();
            const requestedSegmentMode = getDraft().segmentMode;
            if (
                segmentModes.includes(requestedSegmentMode)
                && requestedSegmentMode !== state().segmentMode
            ) {
                const ok = await actionSucceeded('setSegmentMode', requestedSegmentMode);
                if (!ok) return { ok: false, message: t('backend_segment_mode_disabled') };
            }

            const translationModes = getTranslationModes();
            const requestedTranslationMode = getDraft().translationUiMode;
            if (
                pickers.translationMode
                && translationModes.includes(requestedTranslationMode)
                && requestedTranslationMode !== state().translationUiMode
            ) {
                const ok = await actionSucceeded('setTranslationUiMode', requestedTranslationMode, {
                    restartIfNeeded: true,
                });
                if (!ok) return { ok: false, message: t('validation_error') };
            }

            return { ok: true };
        }

        return {
            getDraft,
            setDraft,
            getPickers,
            getMicrophoneData,
            microphoneDefaultLabel,
            renderMicrophoneDevicePicker,
            fetchMicrophoneDevices,
            saveMicrophoneDeviceSelection,
            renderAutoRestartPicker,
            getStoredHideSpeakerLabelsSetting,
            renderSpeakerLabelsPicker,
            renderBundledCjkFontPicker,
            syncBundledCjkFontPreference,
            applyBundledCjkFontPreference,
            applyBundledCjkFontDraft,
            translationModeLabel,
            translationModeCostHint,
            updateTranslationModeHint,
            renderTranslationModePicker,
            segmentModeLabel,
            renderSegmentModePicker,
            renderRuntimeSettingsPickers,
            renderSettingsPickers,
            writeProviderSettingsDraft,
            applyRuntimeControlSettings,
        };
    }

    const api = {
        TRANSLATION_UI_MODES,
        DEFAULT_TRANSLATION_UI_MODE,
        EMPTY_MICROPHONE_DATA,
        normalizeMicrophoneData,
        create,
    };
    root.SettingsRuntime = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

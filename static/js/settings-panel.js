(function (root) {
    'use strict';

    const DEFAULT_PROVIDER_KEY_URLS = Object.freeze({
        soniox: 'https://console.soniox.com/api-keys',
        gemini: 'https://aistudio.google.com/apikey',
    });

    function create(options = {}) {
        const policy = options.policy || root.SettingsPolicy;
        const billing = options.billing || (root.Hosted && root.Hosted.Billing);
        if (!policy) throw new TypeError('SettingsPanel.create requires SettingsPolicy');
        if (!billing) throw new TypeError('SettingsPanel.create requires Hosted.Billing');
        const documentRef = options.document || root.document;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const buildCustomSelect = options.buildCustomSelect;
        if (typeof buildCustomSelect !== 'function') {
            throw new TypeError('SettingsPanel.create requires buildCustomSelect');
        }
        const loadProviderSettings = options.loadProviderSettings || (() => ({}));
        const saveProviderSettings = options.saveProviderSettings || (() => {});
        const freePoolsSummary = typeof options.freePoolsSummary === 'function'
            ? options.freePoolsSummary
            : () => '';
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const providerKeyUrls = options.providerKeyUrls || DEFAULT_PROVIDER_KEY_URLS;
        const actions = options.actions || {};
        const elements = options.elements || {};
        const listeners = [];
        let initialized = false;
        let forcedOpen = false;
        let relayPricing = null;
        let sonioxRegionPicker = null;
        let localStatusLoaded = false;
        let localDraftDirty = false;
        let lastLocalConfig = {};

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function setText(id, key) {
            const element = documentRef && documentRef.getElementById(id);
            if (element) element.textContent = t(key);
        }

        function updateButtonVisibility() {
            const current = state();
            if (elements.settingsButton) {
                elements.settingsButton.style.display = current.lockManualControls ? 'none' : '';
            }
            if (elements.overlayButton && current.lockManualControls) {
                elements.overlayButton.style.display = 'none';
            }
        }

        function setSaving(saving) {
            if (!elements.saveButton) return;
            elements.saveButton.disabled = !!saving;
            elements.saveButton.textContent = t(saving ? 'saving' : 'save');
        }

        function setError(message) {
            if (elements.errorElement) {
                elements.errorElement.textContent = message || '';
            }
        }

        function getDesiredProvider() {
            const settings = loadProviderSettings();
            return settings.providerOverride || state().translationProvider || 'soniox';
        }

        function setProvider(provider) {
            if (!elements.form) return;
            elements.form.querySelectorAll('input[name="provider"]').forEach((radio) => {
                radio.checked = radio.value === provider;
            });
        }

        function getSelectedProvider() {
            if (elements.form) {
                const checked = elements.form.querySelector('input[name="provider"]:checked');
                if (checked) return checked.value;
            }
            return getDesiredProvider();
        }

        function getProviderDisplayName(provider) {
            if (provider === 'gemini') return t('provider_gemini');
            if (provider === 'local') return t('provider_local');
            return t('provider_soniox');
        }

        function formatRate(value) {
            return billing.formatRate(value);
        }

        function getProviderDescription(provider) {
            if (getMode() === 'relay') {
                const info = relayPricing && relayPricing[provider];
                if (!info) return t('provider_relay_desc_loading');
                const summary = freePoolsSummary(info.free_pools);
                if (summary) return `${t('provider_relay_desc_free')} (${summary})`;
                const pricePerSecond = Number(info.price_per_second) || 0;
                return t('provider_relay_desc', {
                    price: formatRate(pricePerSecond),
                    minutePrice: formatRate(pricePerSecond * 60),
                });
            }
            return t(`provider_${provider}_desc`);
        }

        async function fetchRelayPricing() {
            if (!state().relayAvailable) return false;
            try {
                const response = await fetchRef('/account/pricing');
                if (!response.ok) return false;
                const data = await response.json();
                relayPricing = (data && data.pricing) || {};
                if (elements.panel && !elements.panel.hidden) {
                    updateApiKeyField(getSelectedProvider());
                }
                return true;
            } catch (error) {
                return false;
            }
        }

        function getDesiredSonioxRegion() {
            const current = state();
            const settings = loadProviderSettings();
            return policy.normalizeSonioxRegion(
                settings.sonioxRegion || current.backendSonioxRegion || 'us',
            );
        }

        function getSelectedSonioxRegion() {
            if (state().backendSonioxCustomUrl) return null;
            if (sonioxRegionPicker && sonioxRegionPicker.value) {
                return policy.normalizeSonioxRegion(sonioxRegionPicker.value);
            }
            return getDesiredSonioxRegion();
        }

        function renderSonioxRegionPicker(selectedRegion) {
            if (!elements.sonioxRegionPickerHost) return;
            elements.sonioxRegionPickerHost.innerHTML = '';
            if (state().backendSonioxCustomUrl) {
                sonioxRegionPicker = buildCustomSelect([
                    { value: 'custom', label: t('soniox_region_custom') },
                ], { value: 'custom', disabled: true });
                elements.sonioxRegionPickerHost.appendChild(sonioxRegionPicker);
                return;
            }
            const value = policy.normalizeSonioxRegion(selectedRegion);
            const selectOptions = policy.SONIOX_REGIONS.map((region) => ({
                value: region,
                label: t(`soniox_region_${region}`),
            }));
            sonioxRegionPicker = buildCustomSelect(selectOptions, { value });
            elements.sonioxRegionPickerHost.appendChild(sonioxRegionPicker);
        }

        function updateSonioxRegion(provider) {
            const relay = getMode() === 'relay';
            if (elements.sonioxRegionSection) {
                elements.sonioxRegionSection.hidden = relay || provider !== 'soniox';
            }
            if (!relay && provider === 'soniox') {
                renderSonioxRegionPicker(getDesiredSonioxRegion());
            }
        }

        function updateApiKeyField(provider) {
            const settings = loadProviderSettings();
            const current = state();
            const override = settings.keys && settings.keys[provider];
            const providerName = getProviderDisplayName(provider);
            if (elements.apiKeyLabel) {
                elements.apiKeyLabel.textContent = `${providerName} ${t('api_key')}`;
            }
            if (elements.apiKeyInput) {
                elements.apiKeyInput.value = override || '';
                if (override) {
                    elements.apiKeyInput.placeholder = '';
                } else if (current.envKeyPresent && current.envKeyPresent[provider]) {
                    elements.apiKeyInput.placeholder = t('api_key_placeholder_env_configured', {
                        provider: providerName,
                    });
                } else {
                    elements.apiKeyInput.placeholder = t('api_key_placeholder_env_missing', {
                        provider: providerName,
                    });
                }
            }
            if (elements.apiKeySourceHint) elements.apiKeySourceHint.textContent = '';
            if (elements.providerDescription) {
                elements.providerDescription.textContent = getProviderDescription(provider);
            }
            if (elements.apiKeyGetLink) {
                const url = providerKeyUrls[provider];
                const parent = elements.apiKeyGetLink.parentElement;
                if (url) {
                    elements.apiKeyGetLink.textContent = t('api_key_get_link', { provider: providerName });
                    elements.apiKeyGetLink.href = url;
                    if (parent) parent.hidden = false;
                } else {
                    elements.apiKeyGetLink.textContent = '';
                    elements.apiKeyGetLink.removeAttribute('href');
                    if (parent) parent.hidden = true;
                }
            }
        }

        function localDeviceOptions(devices) {
            const options = [
                { value: 'auto', label: t('local_device_auto') },
                { value: 'cpu', label: 'CPU' },
            ];
            for (const device of Array.isArray(devices) ? devices : []) {
                const index = Number(device && device.index);
                if (!Number.isInteger(index) || index < 0) continue;
                const name = String(device.description || device.name || `GPU ${index}`);
                options.push({ value: `vulkan:${index}`, label: `GPU ${index} · ${name}` });
            }
            return options;
        }

        function populateNativeSelect(select, options, value) {
            if (!select) return;
            select.innerHTML = '';
            for (const option of options) {
                const element = documentRef.createElement('option');
                element.value = option.value;
                element.textContent = option.label;
                select.appendChild(element);
            }
            select.value = value || 'auto';
            if (!select.value) select.value = 'auto';
        }

        function renderModelState(element, ready) {
            if (!element) return;
            element.textContent = t(ready ? 'local_model_ready' : 'local_model_missing');
            element.classList.toggle('ready', !!ready);
            element.classList.toggle('missing', !ready);
        }

        function normalizeLocalBackend(value) {
            return String(value || '').toLowerCase() === 'remote' ? 'remote' : 'local';
        }

        function getLocalBackend() {
            return normalizeLocalBackend(
                elements.localInferenceBackend && elements.localInferenceBackend.value,
            );
        }

        function updateLocalBackendVisibility(backend = getLocalBackend()) {
            const remote = normalizeLocalBackend(backend) === 'remote';
            if (elements.localNativeControls) elements.localNativeControls.hidden = remote;
            if (elements.localRemoteControls) elements.localRemoteControls.hidden = !remote;
        }

        function renderRemoteStatus(remoteStatus) {
            const element = elements.localRemoteStatus;
            if (!element) return;
            const status = remoteStatus && typeof remoteStatus === 'object' ? remoteStatus : null;
            const ready = !!(status && status.ready);
            const detail = status && (status.error || status.health);
            element.textContent = ready
                ? t('local_remote_ready')
                : (detail ? String(detail) : t('local_remote_not_tested'));
            element.classList.toggle('ready', ready);
            element.classList.toggle('missing', !!status && !ready);
        }

        function applyLocalInferenceConfig(config = {}) {
            const backend = normalizeLocalBackend(config.backend);
            if (elements.localInferenceBackend) elements.localInferenceBackend.value = backend;
            if (elements.localRemoteServerUrl) {
                elements.localRemoteServerUrl.value = config.server_url || 'ws://127.0.0.1:18775';
            }
            if (elements.localRemoteTimeoutSeconds) {
                const timeout = Number(config.remote_timeout_seconds);
                elements.localRemoteTimeoutSeconds.value = Number.isFinite(timeout) && timeout > 0
                    ? String(Math.floor(timeout))
                    : '';
            }
            updateLocalBackendVisibility(backend);
        }

        async function refreshLocalInferenceStatus({ refreshDevices = false } = {}) {
            if (!fetchRef) return false;
            try {
                const suffix = refreshDevices ? '?refresh_devices=1' : '';
                const response = await fetchRef(`/local-inference/status${suffix}`);
                const data = await response.json();
                if (!response.ok) throw new Error(data.message || 'status failed');
                const models = data.models || {};
                const asr = models.asr && models.asr['qwen3-asr'] || {};
                const translation = models.translation && models.translation.hymt2 || {};
                renderModelState(elements.localAsrStatus, !!asr.ready);
                renderModelState(elements.localTranslationStatus, !!translation.ready);
                if (elements.localAsrPath) {
                    elements.localAsrPath.textContent = asr.model_path || models.models_dir || '';
                }
                if (elements.localTranslationPath) {
                    elements.localTranslationPath.textContent = translation.model_path
                        || t('local_hymt_install_path', { path: translation.install_dir || '' });
                }
                if (elements.localAsrDownloadButton) {
                    elements.localAsrDownloadButton.hidden = !!asr.ready;
                }
                const options = localDeviceOptions(data.devices);
                lastLocalConfig = data.config || {};
                const localConfig = { ...lastLocalConfig, ...(loadProviderSettings().localConfig || {}) };
                if (!localDraftDirty) applyLocalInferenceConfig(localConfig);
                renderRemoteStatus(data.remote_status);
                populateNativeSelect(elements.localAsrDevice, options, localConfig.asr_device);
                populateNativeSelect(
                    elements.localTranslationDevice,
                    options,
                    localConfig.translation_device,
                );
                if (elements.localEncoderDevice) {
                    elements.localEncoderDevice.value = localConfig.encoder_device || 'auto';
                }
                localStatusLoaded = true;
                return true;
            } catch (error) {
                if (elements.localAsrStatus) {
                    elements.localAsrStatus.textContent = String(error);
                    elements.localAsrStatus.classList.add('missing');
                }
                return false;
            }
        }

        async function probeRemoteInference() {
            if (!fetchRef) return false;
            const button = elements.localRemoteProbeButton;
            const serverUrl = elements.localRemoteServerUrl
                ? elements.localRemoteServerUrl.value.trim() : 'ws://127.0.0.1:18775';
            if (button) button.disabled = true;
            try {
                const response = await fetchRef('/local-inference/probe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        server_url: serverUrl,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.message || 'probe failed');
                if (!elements.localRemoteServerUrl || elements.localRemoteServerUrl.value.trim() === serverUrl) {
                    renderRemoteStatus(data.remote_status);
                }
                return !!(data.remote_status && data.remote_status.ready);
            } catch (error) {
                renderRemoteStatus({ ready: false, error: String(error) });
                return false;
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function downloadLocalAsr() {
            const button = elements.localAsrDownloadButton;
            if (button) {
                button.disabled = true;
                button.textContent = t('local_model_downloading');
            }
            setError('');
            try {
                const response = await fetchRef('/local-inference/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ component: 'qwen3-asr' }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.message || 'download failed');
                await refreshLocalInferenceStatus();
            } catch (error) {
                setError(String(error));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = t('local_asr_download');
                }
            }
        }

        function setMode(mode) {
            if (!elements.form) return;
            elements.form.querySelectorAll('input[name="connmode"]').forEach((radio) => {
                radio.checked = radio.value === mode;
            });
        }

        function getMode() {
            if (getSelectedProvider() === 'local') return 'direct';
            const current = state();
            if (!current.relayAvailable) return 'direct';
            if (elements.form) {
                const checked = elements.form.querySelector('input[name="connmode"]:checked');
                if (checked) return checked.value;
            }
            return current.connectionMode === 'relay' ? 'relay' : 'direct';
        }

        function getDraft() {
            const provider = getSelectedProvider();
            const draft = {
                provider,
                region: getSelectedSonioxRegion(),
                mode: getMode(),
                apiKey: elements.apiKeyInput ? elements.apiKeyInput.value : '',
            };
            if (provider === 'local') {
                const backend = getLocalBackend();
                draft.localConfig = {
                    backend,
                    asr_device: elements.localAsrDevice ? elements.localAsrDevice.value : 'auto',
                    encoder_device: elements.localEncoderDevice ? elements.localEncoderDevice.value : 'auto',
                    translation_device: elements.localTranslationDevice
                        ? elements.localTranslationDevice.value
                        : 'auto',
                };
                {
                    draft.localConfig.server_url = elements.localRemoteServerUrl
                        ? elements.localRemoteServerUrl.value.trim()
                        : 'ws://127.0.0.1:18775';
                    const timeout = Number(elements.localRemoteTimeoutSeconds
                        && elements.localRemoteTimeoutSeconds.value);
                    if (Number.isFinite(timeout) && timeout > 0) {
                        draft.localConfig.remote_timeout_seconds = Math.floor(timeout);
                    }
                }
            }
            return draft;
        }

        function applyModeVisibility(mode) {
            const current = state();
            const local = getSelectedProvider() === 'local';
            if (elements.modeSection) elements.modeSection.hidden = local || !current.relayAvailable;
            const relay = !local && mode === 'relay';
            if (elements.accountSection) elements.accountSection.hidden = !relay;
            if (elements.apiKeySection) elements.apiKeySection.hidden = relay || local;
            if (elements.localInferenceSection) elements.localInferenceSection.hidden = !local;
            if (local) updateLocalBackendVisibility();
            if (elements.modeDescription) {
                elements.modeDescription.textContent = local ? '' : t(
                    relay ? 'conn_mode_relay_desc' : 'conn_mode_direct_desc'
                );
            }
            if (local && !localStatusLoaded) void refreshLocalInferenceStatus();
        }

        function populate() {
            const current = state();
            localDraftDirty = false;
            applyLocalInferenceConfig({ ...lastLocalConfig, ...(loadProviderSettings().localConfig || {}) });
            const provider = getDesiredProvider();
            setProvider(provider);
            const mode = current.relayAvailable && current.connectionMode === 'relay'
                ? 'relay'
                : 'direct';
            setMode(mode);
            updateApiKeyField(provider);
            updateSonioxRegion(provider);
            applyModeVisibility(mode);
            call('renderOutputDevicePicker');
            call('renderMicrophoneDevicePicker');
            call('renderRuntimeSettingsPickers');
            call('renderBundledCjkFontPicker');
            call('updateAccountSection');
            if (elements.errorElement) {
                elements.errorElement.textContent = current.setupRequired
                    ? t('setup_required_hint')
                    : '';
            }
        }

        function applyI18n() {
            setText('settingsTitle', 'settings');
            setText('modeLabel', 'conn_mode');
            setText('modeRelayLabel', 'conn_mode_relay');
            setText('modeDirectLabel', 'conn_mode_direct');
            setText('accountLabel', 'account');
            setText('redeemLabel', 'account_redeem_label');
            setText('redeemButton', 'account_redeem');
            if (elements.redeemPasteButton) {
                const pasteLabel = t('login_paste');
                elements.redeemPasteButton.setAttribute('aria-label', pasteLabel);
                elements.redeemPasteButton.setAttribute('title', pasteLabel);
            }
            setText('purchaseCreditsLink', 'account_purchase_credits');
            setText('copyInviteButton', 'account_invite_copy');
            setText('openUserWebButton', 'account_open_web');
            setText('reLoginButton', 'account_relogin');
            setText('logoutButton', 'account_logout');
            setText('providerLabel', 'api_selection');
            setText('providerSonioxLabel', 'provider_soniox');
            setText('providerGeminiLabel', 'provider_gemini');
            setText('providerLocalLabel', 'provider_local');
            setText('localInferenceLabel', 'local_models');
            setText('localInferenceBackendLabel', 'local_inference_backend');
            setText('localInferenceBackendLocalOption', 'local_inference_local');
            setText('localInferenceBackendRemoteOption', 'local_inference_remote');
            setText('localRemoteServerUrlLabel', 'local_remote_server_url');
            setText('localRemoteTimeoutLabel', 'local_remote_timeout');
            setText('localRemoteProbeButton', 'local_remote_probe');
            setText('localAsrDownloadButton', 'local_asr_download');
            setText('localAsrDeviceLabel', 'local_asr_device');
            setText('localEncoderDeviceLabel', 'local_encoder_device');
            setText('localTranslationDeviceLabel', 'local_translation_device');
            setText('sonioxRegionLabel', 'soniox_region');
            setText('microphoneDeviceLabel', 'microphone_device');
            setText('outputDeviceLabel', 'output_device');
            setText('runtimeControlsLabel', 'recognition_controls');
            setText('autoRestartSettingLabel', 'auto_restart_setting');
            setText('sleepOnSilenceSettingLabel', 'auto_sleep_setting');
            setText('speakerLabelsSettingLabel', 'speaker_labels_setting');
            setText('interruptRepairSettingLabel', 'interrupt_repair_setting');
            setText('segmentModeSettingLabel', 'segment_mode_setting');
            setText('translationModeSettingLabel', 'translation_mode_setting');
            setText('appearanceLabel', 'appearance');
            setText('bundledCjkFontLabel', 'bundled_cjk_font');
            setText('bundledCjkFontHint', 'bundled_cjk_font_hint');
            renderSonioxRegionPicker(getSelectedSonioxRegion());
            call('renderMicrophoneDevicePicker');
            call('renderOutputDevicePicker');
            call('renderRuntimeSettingsPickers');
            call('renderBundledCjkFontPicker');
            if (elements.saveButton) elements.saveButton.textContent = t('save');
            if (elements.cancelButton) elements.cancelButton.textContent = t('cancel');
            if (elements.backButton) elements.backButton.textContent = t('mode_back_to_chooser');
            if (elements.resetButton) elements.resetButton.textContent = t('reset_all');
            if (elements.settingsButton) elements.settingsButton.title = t('settings');
            if (elements.closeButton) elements.closeButton.title = t('close');
            if (elements.versionElement) {
                elements.versionElement.textContent = t('client_version', {
                    version: state().clientVersion,
                });
            }
        }

        function open({ forced = false } = {}) {
            const current = state();
            if (current.lockManualControls) return false;
            forcedOpen = !!forced;
            applyI18n();
            populate();
            call('renderTranslationModePicker');
            if (current.relayAvailable && current.connectionMode === 'relay') {
                void fetchRelayPricing();
                void call('fetchBalance');
            }
            void call('fetchMicrophoneDevices');
            void call('fetchOutputDevices');
            if (elements.overlay) elements.overlay.hidden = false;
            if (elements.panel) elements.panel.hidden = false;
            const hideClose = forcedOpen ? 'none' : '';
            if (elements.cancelButton) elements.cancelButton.style.display = hideClose;
            if (elements.closeButton) elements.closeButton.style.display = hideClose;
            if (elements.backButton) {
                elements.backButton.hidden = !(forcedOpen && current.relayAvailable);
            }
            return true;
        }

        function hide() {
            if (elements.overlay) elements.overlay.hidden = true;
            if (elements.panel) elements.panel.hidden = true;
            forcedOpen = false;
        }

        function close() {
            if (forcedOpen) return false;
            hide();
            return true;
        }

        function updateProviderFields(provider) {
            updateApiKeyField(provider);
            updateSonioxRegion(provider);
            applyModeVisibility(getMode());
            call('renderRuntimeSettingsPickers');
            call('renderTranslationModePicker');
        }

        function refreshProviderFields(provider) {
            updateApiKeyField(provider);
            updateSonioxRegion(provider);
        }

        function handleProviderChange() {
            const provider = getSelectedProvider();
            updateProviderFields(provider);
            if (state().canRefreshBalance) {
                void call('fetchBalance', { provider, force: true });
            }
        }

        function handleModeChange() {
            const mode = getMode();
            applyModeVisibility(mode);
            call('updateAccountSection');
            const provider = getSelectedProvider();
            updateSonioxRegion(provider);
            updateApiKeyField(provider);
            if (mode === 'relay') {
                void fetchRelayPricing();
                if (state().canRefreshBalance) {
                    void call('fetchBalance', { provider, force: true });
                }
            }
        }

        function bind(target, event, listener) {
            if (!target) return;
            target.addEventListener(event, listener);
            listeners.push([target, event, listener]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (elements.form) {
                elements.form.querySelectorAll('input[name="provider"]').forEach((radio) => {
                    bind(radio, 'change', handleProviderChange);
                });
                elements.form.querySelectorAll('input[name="connmode"]').forEach((radio) => {
                    bind(radio, 'change', handleModeChange);
                });
            }
            bind(elements.localAsrDownloadButton, 'click', downloadLocalAsr);
            bind(elements.localRemoteProbeButton, 'click', probeRemoteInference);
            bind(elements.localInferenceBackend, 'change', () => {
                localDraftDirty = true;
                updateLocalBackendVisibility();
            });
            for (const field of [elements.localRemoteServerUrl, elements.localRemoteTimeoutSeconds]) {
                bind(field, 'input', () => { localDraftDirty = true; renderRemoteStatus(null); });
            }
            return true;
        }

        function destroy() {
            for (const [target, event, listener] of listeners.splice(0)) {
                target.removeEventListener(event, listener);
            }
            initialized = false;
        }

        function getDebugState() {
            return {
                forcedOpen,
                initialized,
                relayPricing,
                selectedProvider: getSelectedProvider(),
                selectedRegion: getSelectedSonioxRegion(),
                mode: getMode(),
            };
        }

        return {
            applyI18n,
            applyModeVisibility,
            close,
            destroy,
            fetchRelayPricing,
            getDebugState,
            getDraft,
            getMode,
            refreshLocalInferenceStatus,
            probeRemoteInference,
            getProviderDescription,
            getSelectedProvider,
            getSelectedSonioxRegion,
            hide,
            init,
            open,
            populate,
            renderSonioxRegionPicker,
            refreshProviderFields,
            setError,
            setMode,
            setProvider,
            setSaving,
            updateApiKeyField,
            updateButtonVisibility,
            updateProviderFields,
            updateSonioxRegion,
        };
    }

    const api = { DEFAULT_PROVIDER_KEY_URLS, create };
    root.SettingsPanel = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

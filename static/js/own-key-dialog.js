(function (root) {
    'use strict';

    const SUPPORTED_PROVIDERS = Object.freeze(['soniox', 'gemini']);

    function create(options = {}) {
        const policy = options.policy || root.SettingsPolicy;
        if (!policy || typeof policy.normalizeSonioxRegion !== 'function') {
            throw new TypeError('OwnKeyDialog.create requires SettingsPolicy');
        }
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const buildCustomSelect = options.buildCustomSelect;
        if (typeof buildCustomSelect !== 'function') {
            throw new TypeError('OwnKeyDialog.create requires buildCustomSelect');
        }
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const loadProviderSettings = options.loadProviderSettings || (() => ({}));
        const saveProviderSettings = options.saveProviderSettings || (() => {});
        const actions = options.actions || {};
        const elements = options.elements || {};
        const overlay = elements.overlay || null;
        const dialog = elements.dialog || null;
        const form = elements.form || null;
        const title = elements.title || null;
        const providerLabel = elements.providerLabel || null;
        const providerFieldset = elements.providerFieldset || null;
        const providerSonioxLabel = elements.providerSonioxLabel || null;
        const providerGeminiLabel = elements.providerGeminiLabel || null;
        const regionSection = elements.regionSection || null;
        const regionLabel = elements.regionLabel || null;
        const regionPickerHost = elements.regionPickerHost || null;
        const apiKeyLabel = elements.apiKeyLabel || null;
        const apiKeyInput = elements.apiKeyInput || null;
        const errorElement = elements.errorElement || null;
        const backButton = elements.backButton || null;
        const confirmButton = elements.confirmButton || null;

        let initialized = false;
        let busy = false;
        let sonioxRegionPicker = null;
        let lastProvider = 'soniox';
        let draftKeys = {};
        const listeners = [];

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function isOpen() {
            return !!(dialog && !dialog.hidden);
        }

        function normalizeProvider(provider) {
            return provider === 'gemini' ? 'gemini' : 'soniox';
        }

        function getProviderDisplayName(provider) {
            return t(provider === 'gemini' ? 'provider_gemini' : 'provider_soniox');
        }

        function getSelectedProvider() {
            if (form) {
                const checked = form.querySelector('input[name="ownKeyProvider"]:checked');
                if (checked) return normalizeProvider(checked.value);
            }
            return lastProvider;
        }

        function setSelectedProvider(provider) {
            const normalized = normalizeProvider(provider);
            lastProvider = normalized;
            if (!form) return;
            form.querySelectorAll('input[name="ownKeyProvider"]').forEach((radio) => {
                radio.checked = radio.value === normalized;
            });
        }

        function getDesiredSonioxRegion() {
            const current = state();
            const settings = loadProviderSettings() || {};
            return policy.normalizeSonioxRegion(
                settings.sonioxRegion || current.backendSonioxRegion || 'us',
            );
        }

        function getSelectedRegion() {
            if (state().backendSonioxCustomUrl) return null;
            if (sonioxRegionPicker && sonioxRegionPicker.value) {
                return policy.normalizeSonioxRegion(sonioxRegionPicker.value);
            }
            return getDesiredSonioxRegion();
        }

        function renderRegionPicker(selectedRegion) {
            if (!regionPickerHost) return;
            if (sonioxRegionPicker && typeof sonioxRegionPicker.close === 'function') {
                sonioxRegionPicker.close();
            }
            regionPickerHost.innerHTML = '';
            if (state().backendSonioxCustomUrl) {
                sonioxRegionPicker = buildCustomSelect([
                    { value: 'custom', label: t('soniox_region_custom') },
                ], { value: 'custom', disabled: true });
                regionPickerHost.appendChild(sonioxRegionPicker);
                return;
            }
            const value = policy.normalizeSonioxRegion(selectedRegion);
            const selectOptions = (policy.SONIOX_REGIONS || ['us', 'eu', 'jp']).map((region) => ({
                value: region,
                label: t(`soniox_region_${region}`),
            }));
            sonioxRegionPicker = buildCustomSelect(selectOptions, { value, disabled: busy });
            regionPickerHost.appendChild(sonioxRegionPicker);
        }

        function setError(message) {
            if (errorElement) errorElement.textContent = message || '';
        }

        function updateApiKeyField(provider, { preserveInput = false } = {}) {
            const normalized = normalizeProvider(provider);
            const providerName = getProviderDisplayName(normalized);
            if (apiKeyLabel) {
                apiKeyLabel.textContent = `${providerName} ${t('api_key')}`;
            }
            if (apiKeyInput) {
                if (!preserveInput) {
                    apiKeyInput.value = draftKeys[normalized] || '';
                }
                apiKeyInput.placeholder = t('api_key_placeholder_env_missing', {
                    provider: providerName,
                });
            }
        }

        function updateRegionVisibility(provider) {
            if (regionSection) {
                regionSection.hidden = normalizeProvider(provider) !== 'soniox';
            }
        }

        function setBusy(nextBusy) {
            busy = !!nextBusy;
            if (form) {
                form.querySelectorAll('input[name="ownKeyProvider"]').forEach((radio) => {
                    radio.disabled = busy;
                });
            }
            if (apiKeyInput) apiKeyInput.disabled = busy;
            if (backButton) backButton.disabled = busy;
            if (confirmButton) {
                confirmButton.disabled = busy;
                confirmButton.textContent = t(busy ? 'own_key_checking' : 'confirm');
            }
            if (sonioxRegionPicker) {
                const trigger = sonioxRegionPicker.querySelector('.lang-picker-button');
                if (trigger) trigger.disabled = busy || !!state().backendSonioxCustomUrl;
                sonioxRegionPicker.classList.toggle('disabled', busy || !!state().backendSonioxCustomUrl);
                if (busy && typeof sonioxRegionPicker.close === 'function') {
                    sonioxRegionPicker.close();
                }
            }
        }

        function applyI18n() {
            const provider = getSelectedProvider();
            if (title) title.textContent = t('own_key_dialog_title');
            if (providerLabel) providerLabel.textContent = t('api_selection');
            if (providerSonioxLabel) providerSonioxLabel.textContent = t('provider_soniox');
            if (providerGeminiLabel) providerGeminiLabel.textContent = t('provider_gemini');
            if (regionLabel) regionLabel.textContent = t('soniox_region');
            if (backButton) backButton.textContent = t('login_back');
            if (confirmButton && !busy) confirmButton.textContent = t('confirm');
            renderRegionPicker(getSelectedRegion());
            updateApiKeyField(provider, { preserveInput: true });
        }

        function positionAtBaseline() {
            if (!dialog || !documentRef || !documentRef.body) return;
            const baseline = dialog.cloneNode(true);
            baseline.removeAttribute('id');
            baseline.hidden = false;
            baseline.querySelectorAll('[id]').forEach((element) => element.removeAttribute('id'));
            const baselineRegion = baseline.querySelector('.own-key-region-section')
                || baseline.querySelectorAll('.auth-section')[1];
            if (baselineRegion) baselineRegion.hidden = false;
            const baselineError = baseline.querySelector('.own-key-warning');
            if (baselineError) baselineError.textContent = '';
            baseline.setAttribute('aria-hidden', 'true');
            baseline.inert = true;
            Object.assign(baseline.style, {
                display: 'block',
                visibility: 'hidden',
                pointerEvents: 'none',
                top: '0px',
                maxHeight: 'none',
                height: 'auto',
                overflow: 'visible',
            });
            documentRef.body.appendChild(baseline);
            try {
                const height = baseline.getBoundingClientRect().height;
                const viewportHeight = windowRef && windowRef.innerHeight;
                if (height > 0 && viewportHeight > 0) {
                    const top = Math.max(16, Math.round((viewportHeight - height) / 2));
                    dialog.style.setProperty('--own-key-dialog-top', `${top}px`);
                }
            } finally {
                baseline.remove();
            }
        }

        function open() {
            const current = state();
            if (current.lockManualControls || !current.relayAvailable) return false;
            const settings = loadProviderSettings() || {};
            const savedKeys = settings.keys && typeof settings.keys === 'object' ? settings.keys : {};
            draftKeys = {
                soniox: savedKeys.soniox ? String(savedKeys.soniox) : '',
                gemini: savedKeys.gemini ? String(savedKeys.gemini) : '',
            };
            const candidate = settings.providerOverride || current.translationProvider;
            const initialProvider = SUPPORTED_PROVIDERS.includes(candidate) ? candidate : 'soniox';
            setBusy(false);
            setError('');
            setSelectedProvider(initialProvider);
            applyI18n();
            renderRegionPicker(getDesiredSonioxRegion());
            updateRegionVisibility(initialProvider);
            updateApiKeyField(initialProvider);
            positionAtBaseline();
            if (overlay) overlay.hidden = false;
            if (dialog) dialog.hidden = false;
            void call('syncDesktopShortcutOption', { resetChecked: true, target: 'ownKey' });
            if (apiKeyInput) apiKeyInput.focus();
            return true;
        }

        function hide() {
            if (sonioxRegionPicker && typeof sonioxRegionPicker.close === 'function') {
                sonioxRegionPicker.close();
            }
            setBusy(false);
            setError('');
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
        }

        function handleProviderChange() {
            if (apiKeyInput) {
                draftKeys[lastProvider] = apiKeyInput.value;
            }
            const nextProvider = getSelectedProvider();
            lastProvider = nextProvider;
            setError('');
            updateRegionVisibility(nextProvider);
            updateApiKeyField(nextProvider);
            if (apiKeyInput) apiKeyInput.focus();
        }

        async function handleSubmit(event) {
            if (event) event.preventDefault();
            if (busy) return { ok: false, reason: 'busy' };

            const provider = getSelectedProvider();
            const region = provider === 'soniox' ? getSelectedRegion() : null;
            const apiKey = String((apiKeyInput && apiKeyInput.value) || '').trim();
            if (!apiKey) {
                setError(t('api_key_required'));
                if (apiKeyInput) apiKeyInput.focus();
                return { ok: false, reason: 'empty' };
            }

            setError('');
            setBusy(true);
            let result = null;
            try {
                result = await call('pushSetup', provider, apiKey, {
                    silent: true,
                    mode: 'direct',
                    region,
                });
            } catch (error) {
                result = { ok: false, data: { message: String(error) } };
            }

            if (!result || !result.ok) {
                setBusy(false);
                setError(t('own_key_unavailable'));
                if (apiKeyInput) apiKeyInput.focus();
                return { ok: false, reason: 'invalid' };
            }

            const settings = loadProviderSettings() || {};
            settings.providerOverride = provider;
            if (provider === 'soniox' && region) {
                settings.sonioxRegion = region;
            }
            settings.keys = {
                ...(settings.keys && typeof settings.keys === 'object' ? settings.keys : {}),
                [provider]: apiKey,
            };
            saveProviderSettings(settings);
            hide();
            void call('createDesktopShortcutIfRequested', { target: 'ownKey' });
            await call('switchToOwnKeyMode', { forced: false });
            return { ok: true, provider, region };
        }

        function bind(target, type, listener) {
            if (!target) return;
            target.addEventListener(type, listener);
            listeners.push([target, type, listener]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (providerFieldset || form) {
                const scope = providerFieldset || form;
                scope.querySelectorAll('input[name="ownKeyProvider"]').forEach((radio) => {
                    bind(radio, 'change', handleProviderChange);
                });
            }
            bind(apiKeyInput, 'input', () => {
                draftKeys[getSelectedProvider()] = apiKeyInput.value;
                setError('');
            });
            bind(backButton, 'click', () => {
                if (!busy) hide();
            });
            bind(form, 'submit', (event) => {
                void handleSubmit(event);
            });
            bind(windowRef, 'resize', () => {
                if (isOpen()) positionAtBaseline();
            });
            return true;
        }

        function destroy() {
            hide();
            for (const [target, type, listener] of listeners.splice(0)) {
                target.removeEventListener(type, listener);
            }
            initialized = false;
        }

        function getDebugState() {
            return {
                busy,
                initialized,
                open: isOpen(),
                selectedProvider: getSelectedProvider(),
                selectedRegion: getSelectedRegion(),
                errorText: errorElement ? errorElement.textContent : '',
            };
        }

        return {
            applyI18n,
            destroy,
            getDebugState,
            getSelectedProvider,
            getSelectedRegion,
            handleSubmit,
            hide,
            init,
            isOpen,
            open,
        };
    }

    const api = { SUPPORTED_PROVIDERS, create };
    root.OwnKeyDialog = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

(function (root) {
    'use strict';

    function create(options = {}) {
        const runtime = options.runtime;
        const setup = options.setup;
        if (!runtime) throw new TypeError('SettingsSave.create requires SettingsRuntime');
        if (!setup) throw new TypeError('SettingsSave.create requires SettingsSetup');
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const localizeBackendMessage = typeof options.localizeBackendMessage === 'function'
            ? options.localizeBackendMessage
            : (message) => String(message || '');
        const getDraft = typeof options.getDraft === 'function' ? options.getDraft : () => ({});
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const loadProviderSettings = options.loadProviderSettings || (() => ({}));
        const saveProviderSettings = options.saveProviderSettings || (() => {});
        const loadServerSettings = options.loadServerSettings || (() => ({}));
        const saveServerSettings = options.saveServerSettings || (() => {});
        const ensureHostedVersionAllowed = options.ensureHostedVersionAllowed || (async () => true);
        const actions = options.actions || {};

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function setSaving(saving) {
            call('setSaving', !!saving);
        }

        function setError(message) {
            call('setError', message || '');
        }

        async function handleSubmit(event) {
            if (event) event.preventDefault();
            runtime.applyBundledCjkFontDraft();
            const draft = getDraft() || {};
            const provider = draft.provider || 'soniox';
            const region = draft.region || null;
            const mode = draft.mode === 'relay' ? 'relay' : 'direct';
            const settings = loadProviderSettings();
            const previousProviderKey = settings.keys && settings.keys[provider]
                ? String(settings.keys[provider])
                : '';
            settings.providerOverride = provider;
            if (region) settings.sonioxRegion = region;
            settings.keys = settings.keys || {};
            runtime.writeProviderSettingsDraft(settings, provider);

            if (mode === 'relay') {
                const allowed = await ensureHostedVersionAllowed({ candidateMode: 'relay' });
                if (!allowed) {
                    call('refreshProviderFields', provider);
                    return { status: 'version_blocked', mode, provider };
                }
            }

            const server = loadServerSettings();
            server.mode = mode;
            server.modeChosen = true;
            saveServerSettings(server);

            const runtimeResult = await runtime.applyRuntimeControlSettings();
            if (!runtimeResult.ok) {
                setError(runtimeResult.message || t('backend_segment_mode_disabled'));
                return { status: 'runtime_error', mode, provider };
            }

            if (mode === 'relay') {
                return saveRelay({ provider, region, settings, server });
            }
            return saveDirect({
                provider,
                region,
                settings,
                previousProviderKey,
                apiKey: String(draft.apiKey || '').trim(),
            });
        }

        async function saveRelay({ provider, region, settings, server }) {
            if (!server.token) {
                saveProviderSettings(settings);
                call('hideSettingsPanel');
                call('openLogin', { forced: false });
                return { status: 'login_required', mode: 'relay', provider };
            }

            setSaving(true);
            setError('');
            const microphoneResult = await runtime.saveMicrophoneDeviceSelection();
            if (!microphoneResult.ok) {
                setSaving(false);
                setError(microphoneResult.message || t('validation_error'));
                return { status: 'microphone_error', mode: 'relay', provider };
            }

            saveProviderSettings(settings);
            if (!setup.relayNeedsSetup({ provider, token: server.token })) {
                setSaving(false);
                call('finishHotSettingsSave');
                return { status: 'hot_saved', mode: 'relay', provider };
            }

            const result = await setup.push(provider, null, {
                silent: false,
                mode: 'relay',
                token: server.token,
                region,
            });
            setSaving(false);
            if (!result.ok) {
                const message = result.data && result.data.message;
                setError(localizeBackendMessage(message || t('validation_api_key')));
                return { status: 'setup_error', mode: 'relay', provider };
            }
            if (result.data && result.data.setup_required) {
                call('hideSettingsPanel');
                call('openLogin', { forced: true });
                return { status: 'login_required', mode: 'relay', provider, forced: true };
            }
            call('hideSettingsPanel');
            call('clearSubtitleState');
            return { status: 'saved', mode: 'relay', provider };
        }

        async function saveDirect({
            provider,
            region,
            settings,
            previousProviderKey,
            apiKey,
        }) {
            if (apiKey) {
                settings.keys[provider] = apiKey;
            } else {
                delete settings.keys[provider];
            }
            const hasOverride = !!settings.keys[provider];
            const envKeyPresent = state().envKeyPresent || {};
            if (!hasOverride && !envKeyPresent[provider]) {
                setError(t('api_key_required'));
                return { status: 'api_key_required', mode: 'direct', provider };
            }

            saveProviderSettings(settings);
            setSaving(true);
            setError('');
            const microphoneResult = await runtime.saveMicrophoneDeviceSelection();
            if (!microphoneResult.ok) {
                setSaving(false);
                setError(microphoneResult.message || t('validation_error'));
                return { status: 'microphone_error', mode: 'direct', provider };
            }

            const apiKeyToPush = settings.keys[provider] || null;
            if (!setup.directNeedsSetup({
                provider,
                region,
                apiKeyToPush,
                previousKey: previousProviderKey,
            })) {
                setSaving(false);
                call('finishHotSettingsSave');
                return { status: 'hot_saved', mode: 'direct', provider };
            }

            const result = await setup.push(provider, apiKeyToPush, {
                silent: false,
                region,
                mode: 'direct',
            });
            setSaving(false);
            if (!result.ok) {
                const message = result.data && result.data.message;
                setError(localizeBackendMessage(message || t('validation_api_key')));
                return { status: 'setup_error', mode: 'direct', provider };
            }
            if (result.data && result.data.setup_required) {
                setError(t('setup_required_hint'));
                call('populateSettingsForm');
                return { status: 'setup_required', mode: 'direct', provider };
            }
            call('hideSettingsPanel');
            call('clearSubtitleState');
            return { status: 'saved', mode: 'direct', provider };
        }

        return {
            handleSubmit,
            saveDirect,
            saveRelay,
        };
    }

    const api = { create };
    root.SettingsSave = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

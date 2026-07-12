const SettingsSave = require('../../static/js/settings-save');

function setup(overrides = {}) {
    const draft = {
        provider: 'soniox',
        region: 'us',
        mode: 'direct',
        apiKey: ' new-key ',
        ...overrides.draft,
    };
    const state = {
        envKeyPresent: { soniox: false, gemini: false },
        ...overrides.state,
    };
    let providerSettings = {
        providerOverride: null,
        sonioxRegion: 'eu',
        keys: { soniox: 'old-key' },
        ...overrides.providerSettings,
    };
    let serverSettings = {
        mode: 'direct',
        modeChosen: true,
        token: 'relay-token',
        ...overrides.serverSettings,
    };
    const runtime = {
        applyBundledCjkFontDraft: vi.fn(),
        writeProviderSettingsDraft: vi.fn(),
        applyRuntimeControlSettings: vi.fn().mockResolvedValue({ ok: true }),
        saveMicrophoneDeviceSelection: vi.fn().mockResolvedValue({ ok: true }),
        ...overrides.runtime,
    };
    const setupController = {
        directNeedsSetup: vi.fn(() => true),
        relayNeedsSetup: vi.fn(() => true),
        push: vi.fn().mockResolvedValue({ ok: true, data: {} }),
        ...overrides.setup,
    };
    const actionNames = [
        'setSaving', 'setError', 'refreshProviderFields', 'hideSettingsPanel',
        'openLogin', 'finishHotSettingsSave', 'clearSubtitleState', 'populateSettingsForm',
    ];
    const actions = Object.fromEntries(actionNames.map((name) => [name, vi.fn()]));
    Object.assign(actions, overrides.actions);
    const saveProviderSettings = vi.fn((value) => {
        providerSettings = { ...value, keys: { ...(value.keys || {}) } };
    });
    const saveServerSettings = vi.fn((value) => { serverSettings = { ...value }; });
    const ensureHostedVersionAllowed = overrides.ensureHostedVersionAllowed
        || vi.fn().mockResolvedValue(true);
    const controller = SettingsSave.create({
        runtime,
        setup: setupController,
        t: (key) => `t:${key}`,
        localizeBackendMessage: (message) => `localized:${message}`,
        getDraft: () => ({ ...draft }),
        getState: () => state,
        loadProviderSettings: () => ({
            ...providerSettings,
            keys: { ...(providerSettings.keys || {}) },
        }),
        saveProviderSettings,
        loadServerSettings: () => ({ ...serverSettings }),
        saveServerSettings,
        ensureHostedVersionAllowed,
        actions,
    });
    return {
        actions,
        controller,
        draft,
        ensureHostedVersionAllowed,
        getProviderSettings: () => providerSettings,
        getServerSettings: () => serverSettings,
        runtime,
        saveProviderSettings,
        saveServerSettings,
        setup: setupController,
        state,
    };
}

describe('SettingsSave direct mode', () => {
    it('persists a trimmed key and pushes the exact direct setup request', async () => {
        const page = setup();
        const event = { preventDefault: vi.fn() };

        await expect(page.controller.handleSubmit(event)).resolves.toEqual({
            status: 'saved', mode: 'direct', provider: 'soniox',
        });

        expect(event.preventDefault).toHaveBeenCalledOnce();
        expect(page.runtime.applyBundledCjkFontDraft).toHaveBeenCalledOnce();
        expect(page.runtime.writeProviderSettingsDraft).toHaveBeenCalledWith(
            expect.objectContaining({ providerOverride: 'soniox', sonioxRegion: 'us' }),
            'soniox',
        );
        expect(page.getServerSettings()).toMatchObject({ mode: 'direct', modeChosen: true });
        expect(page.getProviderSettings().keys.soniox).toBe('new-key');
        expect(page.setup.directNeedsSetup).toHaveBeenCalledWith({
            provider: 'soniox', region: 'us', apiKeyToPush: 'new-key', previousKey: 'old-key',
        });
        expect(page.setup.push).toHaveBeenCalledWith('soniox', 'new-key', {
            silent: false, region: 'us', mode: 'direct',
        });
        expect(page.actions.setSaving.mock.calls).toEqual([[true], [false]]);
        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.clearSubtitleState).toHaveBeenCalledOnce();
    });

    it('rejects a missing direct key after saving the connection mode but before provider settings', async () => {
        const page = setup({
            draft: { apiKey: '' },
            providerSettings: { keys: {} },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({
            status: 'api_key_required', mode: 'direct',
        });

        expect(page.saveServerSettings).toHaveBeenCalledOnce();
        expect(page.runtime.applyRuntimeControlSettings).toHaveBeenCalledOnce();
        expect(page.saveProviderSettings).not.toHaveBeenCalled();
        expect(page.runtime.saveMicrophoneDeviceSelection).not.toHaveBeenCalled();
        expect(page.setup.push).not.toHaveBeenCalled();
        expect(page.actions.setError).toHaveBeenCalledWith('t:api_key_required');
    });

    it('clears an override and hot-saves when an env key remains available', async () => {
        const page = setup({
            draft: { apiKey: '' },
            state: { envKeyPresent: { soniox: true, gemini: false } },
            setup: { directNeedsSetup: vi.fn(() => false) },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'hot_saved' });

        expect(page.getProviderSettings().keys).not.toHaveProperty('soniox');
        expect(page.setup.directNeedsSetup).toHaveBeenCalledWith({
            provider: 'soniox', region: 'us', apiKeyToPush: null, previousKey: 'old-key',
        });
        expect(page.setup.push).not.toHaveBeenCalled();
        expect(page.actions.finishHotSettingsSave).toHaveBeenCalledOnce();
    });

    it('stops after runtime failure without saving provider settings', async () => {
        const page = setup({
            runtime: {
                applyRuntimeControlSettings: vi.fn().mockResolvedValue({ ok: false, message: 'runtime bad' }),
            },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'runtime_error' });

        expect(page.saveServerSettings).toHaveBeenCalledOnce();
        expect(page.saveProviderSettings).not.toHaveBeenCalled();
        expect(page.runtime.saveMicrophoneDeviceSelection).not.toHaveBeenCalled();
        expect(page.actions.setError).toHaveBeenCalledWith('runtime bad');
    });

    it('keeps already-persisted provider settings when microphone save fails', async () => {
        const page = setup({
            runtime: {
                saveMicrophoneDeviceSelection: vi.fn().mockResolvedValue({ ok: false, message: 'mic bad' }),
            },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({
            status: 'microphone_error', mode: 'direct',
        });

        expect(page.saveProviderSettings).toHaveBeenCalledOnce();
        expect(page.setup.push).not.toHaveBeenCalled();
        expect(page.actions.setSaving.mock.calls).toEqual([[true], [false]]);
        expect(page.actions.setError).toHaveBeenLastCalledWith('mic bad');
    });

    it('localizes setup failures and leaves the panel open', async () => {
        const page = setup({
            setup: { push: vi.fn().mockResolvedValue({ ok: false, data: { message: 'bad key' } }) },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'setup_error' });

        expect(page.actions.setError).toHaveBeenLastCalledWith('localized:bad key');
        expect(page.actions.hideSettingsPanel).not.toHaveBeenCalled();
        expect(page.actions.clearSubtitleState).not.toHaveBeenCalled();
    });

    it('repopulates the panel when direct setup still reports setup_required', async () => {
        const page = setup({
            setup: {
                push: vi.fn().mockResolvedValue({ ok: true, data: { setup_required: true } }),
            },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'setup_required' });

        expect(page.actions.setError).toHaveBeenLastCalledWith('t:setup_required_hint');
        expect(page.actions.populateSettingsForm).toHaveBeenCalledOnce();
        expect(page.actions.hideSettingsPanel).not.toHaveBeenCalled();
    });
});

describe('SettingsSave relay mode', () => {
    it('honors the version gate before saving server or runtime settings', async () => {
        const ensureHostedVersionAllowed = vi.fn().mockResolvedValue(false);
        const page = setup({
            draft: { mode: 'relay' },
            ensureHostedVersionAllowed,
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'version_blocked' });

        expect(ensureHostedVersionAllowed).toHaveBeenCalledWith({ candidateMode: 'relay' });
        expect(page.actions.refreshProviderFields).toHaveBeenCalledWith('soniox');
        expect(page.saveServerSettings).not.toHaveBeenCalled();
        expect(page.runtime.applyRuntimeControlSettings).not.toHaveBeenCalled();
    });

    it('saves provider drafts and opens normal login when no token exists', async () => {
        const page = setup({
            draft: { mode: 'relay' },
            serverSettings: { token: '' },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'login_required' });

        expect(page.saveProviderSettings).toHaveBeenCalledOnce();
        expect(page.runtime.saveMicrophoneDeviceSelection).not.toHaveBeenCalled();
        expect(page.setup.push).not.toHaveBeenCalled();
        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: false });
    });

    it('does not persist relay provider settings when microphone save fails', async () => {
        const page = setup({
            draft: { mode: 'relay' },
            runtime: {
                saveMicrophoneDeviceSelection: vi.fn().mockResolvedValue({ ok: false }),
            },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({
            status: 'microphone_error', mode: 'relay',
        });

        expect(page.saveProviderSettings).not.toHaveBeenCalled();
        expect(page.actions.setError).toHaveBeenLastCalledWith('t:validation_error');
        expect(page.setup.push).not.toHaveBeenCalled();
    });

    it('hot-saves an already synchronized relay session', async () => {
        const page = setup({
            draft: { mode: 'relay' },
            setup: { relayNeedsSetup: vi.fn(() => false) },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'hot_saved' });

        expect(page.saveProviderSettings).toHaveBeenCalledOnce();
        expect(page.setup.push).not.toHaveBeenCalled();
        expect(page.actions.finishHotSettingsSave).toHaveBeenCalledOnce();
    });

    it('pushes relay token and region without a provider API key', async () => {
        const page = setup({ draft: { mode: 'relay', provider: 'gemini', region: null } });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({
            status: 'saved', mode: 'relay', provider: 'gemini',
        });

        expect(page.setup.relayNeedsSetup).toHaveBeenCalledWith({
            provider: 'gemini', token: 'relay-token',
        });
        expect(page.setup.push).toHaveBeenCalledWith('gemini', null, {
            silent: false, mode: 'relay', token: 'relay-token', region: null,
        });
        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.clearSubtitleState).toHaveBeenCalledOnce();
    });

    it('localizes relay setup failures without clearing subtitles', async () => {
        const page = setup({
            draft: { mode: 'relay' },
            setup: { push: vi.fn().mockResolvedValue({ ok: false, data: {} }) },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({ status: 'setup_error' });

        expect(page.actions.setError).toHaveBeenLastCalledWith('localized:t:validation_api_key');
        expect(page.actions.hideSettingsPanel).not.toHaveBeenCalled();
        expect(page.actions.clearSubtitleState).not.toHaveBeenCalled();
    });

    it('forces login when relay setup still requires authentication', async () => {
        const page = setup({
            draft: { mode: 'relay' },
            setup: {
                push: vi.fn().mockResolvedValue({ ok: true, data: { setup_required: true } }),
            },
        });

        await expect(page.controller.handleSubmit()).resolves.toMatchObject({
            status: 'login_required', forced: true,
        });

        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: true });
        expect(page.actions.clearSubtitleState).not.toHaveBeenCalled();
    });
});

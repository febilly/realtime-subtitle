const SettingsPolicy = require('../../static/js/settings-policy');
const SettingsSetup = require('../../static/js/settings-setup');

function response(data, { ok = true, status = 200, jsonReject = false } = {}) {
    return {
        ok,
        status,
        json: jsonReject
            ? vi.fn().mockRejectedValue(new Error('invalid json'))
            : vi.fn().mockResolvedValue(data),
    };
}

function setup(overrides = {}) {
    const events = [];
    const state = {
        translationProvider: 'soniox',
        backendBootId: 'boot-1',
        setupRequired: false,
        backendMode: 'direct',
        backendLoggedIn: false,
        backendSonioxCustomUrl: false,
        backendSonioxRegion: 'us',
        backendKeySource: 'env',
        pushedOverrideBootId: null,
        uiTranslationMode: 'two_way',
        lockManualControls: false,
        providerSettings: { providerOverride: null, keys: {} },
        connectionMode: 'direct',
        serverSettings: {},
        sleepOnSilenceEnabled: true,
        ...overrides.state,
    };
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response({
        provider: 'soniox',
        boot_id: 'boot-2',
        setup_required: false,
        mode: 'direct',
        logged_in: false,
    }));
    const actions = {
        sessionCostReset: vi.fn(() => events.push('cost.reset')),
        showToast: vi.fn((...args) => events.push(`toast:${args.join(':')}`)),
        setUiTranslationMode: vi.fn(() => events.push('translation.one_way')),
        fetchUiConfig: vi.fn(async () => { events.push('config'); }),
        ...overrides.actions,
    };
    const updateState = vi.fn((patch) => {
        Object.assign(state, patch);
        events.push(`state:${Object.keys(patch).join(',')}`);
    });
    const controller = SettingsSetup.create({
        policy: SettingsPolicy,
        fetch,
        t: (key) => key,
        getState: () => state,
        updateState,
        actions,
    });
    return { actions, controller, events, fetch, state, updateState };
}

describe('SettingsSetup push', () => {
    it('posts an exact direct payload and commits backend state before refreshing config', async () => {
        const env = setup();
        await expect(env.controller.push('soniox', 'local-key', {
            region: 'eu', mode: 'direct',
        })).resolves.toMatchObject({ ok: true });

        expect(env.fetch).toHaveBeenCalledWith('/setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: 'soniox',
                mode: 'direct',
                api_key: 'local-key',
                soniox_region: 'eu',
                sleep_on_silence: true,
            }),
        });
        expect(env.state).toMatchObject({
            translationProvider: 'soniox',
            backendBootId: 'boot-2',
            pushedOverrideBootId: 'boot-2',
            backendMode: 'direct',
            backendLoggedIn: false,
        });
        expect(env.actions.sessionCostReset).not.toHaveBeenCalled();
        expect(env.events.at(-2)).toBe('config');
        expect(env.events.at(-1)).toBe('toast:settings_saved');
    });

    it('uses relay token instead of an API key and resets cost only on provider change', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            provider: 'gemini',
            boot_id: 'boot-3',
            setup_required: false,
            mode: 'relay',
            logged_in: true,
        }));
        const env = setup({ fetch });
        const result = await env.controller.push('gemini', 'must-not-leak', {
            mode: 'relay', token: 'relay-token', silent: true,
        });

        expect(result.ok).toBe(true);
        expect(JSON.parse(env.fetch.mock.calls[0][1].body)).toEqual({
            provider: 'gemini', mode: 'relay', token: 'relay-token', sleep_on_silence: true,
        });
        expect(env.actions.sessionCostReset).toHaveBeenCalledOnce();
        expect(env.actions.showToast).not.toHaveBeenCalled();
        expect(env.state).toMatchObject({
            translationProvider: 'gemini', backendMode: 'relay', backendLoggedIn: true,
        });
    });

    it('persists a two-way downgrade before config refresh', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            provider: 'gemini',
            boot_id: 'boot-4',
            downgraded_two_way: true,
        }));
        const env = setup({ fetch });
        await env.controller.push('gemini', 'key');

        expect(env.state.uiTranslationMode).toBe('one_way');
        expect(env.actions.showToast).toHaveBeenNthCalledWith(1, 'gemini_no_two_way_warning', true);
        expect(env.actions.setUiTranslationMode).toHaveBeenCalledWith('one_way', {
            persistOnly: true,
        });
        expect(env.events.indexOf('translation.one_way')).toBeLessThan(env.events.indexOf('config'));
    });

    it('returns HTTP and network failures without committing state', async () => {
        const http = setup({
            fetch: vi.fn().mockResolvedValue(response({ message: 'bad key' }, { ok: false, status: 400 })),
        });
        await expect(http.controller.push('soniox', 'bad')).resolves.toEqual({
            ok: false,
            data: { message: 'bad key' },
        });
        expect(http.updateState).not.toHaveBeenCalled();
        expect(http.actions.fetchUiConfig).not.toHaveBeenCalled();

        const network = setup({ fetch: vi.fn().mockRejectedValue(new Error('offline')) });
        await expect(network.controller.push('soniox', 'key')).resolves.toMatchObject({
            ok: false,
            data: { message: 'Error: offline' },
        });
        expect(network.updateState).not.toHaveBeenCalled();
    });

    it('accepts an invalid JSON success body as the existing empty response', async () => {
        const env = setup({
            fetch: vi.fn().mockResolvedValue(response(null, { jsonReject: true })),
        });
        const result = await env.controller.push('soniox', null, { mode: 'direct' });
        expect(result).toEqual({ ok: true, data: {} });
        expect(env.state.backendBootId).toBe('boot-1');
        expect(env.state.pushedOverrideBootId).toBe('boot-1');
    });
});

describe('SettingsSetup decisions and startup sync', () => {
    it('adapts direct and relay setup decisions from live backend state', () => {
        const env = setup();
        expect(env.controller.directNeedsSetup({
            provider: 'soniox', region: 'us', apiKeyToPush: null, previousKey: '',
        })).toBe(false);
        env.state.setupRequired = true;
        expect(env.controller.directNeedsSetup({
            provider: 'soniox', region: 'us', apiKeyToPush: null, previousKey: '',
        })).toBe(true);

        env.state.setupRequired = false;
        env.state.backendMode = 'relay';
        env.state.backendLoggedIn = true;
        expect(env.controller.relayNeedsSetup({ provider: 'soniox', token: 'token' })).toBe(false);
        env.state.backendLoggedIn = false;
        expect(env.controller.relayNeedsSetup({ provider: 'soniox', token: 'token' })).toBe(true);
    });

    it('does not sync while locked or when persisted settings already match', async () => {
        const locked = setup({ state: { lockManualControls: true } });
        await expect(locked.controller.syncFromStorage()).resolves.toBeNull();
        expect(locked.fetch).not.toHaveBeenCalled();

        const matched = setup();
        await expect(matched.controller.syncFromStorage()).resolves.toBeNull();
        expect(matched.fetch).not.toHaveBeenCalled();
    });

    it('executes the policy plan once for a stored direct override', async () => {
        const env = setup({
            state: {
                providerSettings: {
                    providerOverride: 'gemini',
                    keys: { gemini: 'gemini-key' },
                },
            },
            fetch: vi.fn().mockResolvedValue(response({
                provider: 'gemini', boot_id: 'boot-2', mode: 'direct',
            })),
        });
        const result = await env.controller.syncFromStorage();
        expect(result.ok).toBe(true);
        expect(JSON.parse(env.fetch.mock.calls[0][1].body)).toEqual({
            provider: 'gemini', mode: 'direct', api_key: 'gemini-key', sleep_on_silence: true,
        });
    });
});

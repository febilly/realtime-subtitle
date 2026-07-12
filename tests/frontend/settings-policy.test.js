const SettingsPolicy = require('../../static/js/settings-policy');

const {
    hasExplicitConnectionMode,
    resolveConnectionMode,
    normalizeSonioxRegion,
    buildSetupBody,
    directSettingsNeedSetup,
    relaySettingsNeedSetup,
    buildProviderSyncPlan,
    resolveForceOpenAction,
    shouldPreopenHostedLogin,
} = SettingsPolicy;

describe('settings policy connection mode', () => {
    it.each([
        ['missing settings', null, false],
        ['modeChosen true', { mode: null, modeChosen: true }, true],
        ['legacy direct', { mode: 'direct', modeChosen: false }, true],
        ['legacy logged-in relay', { mode: 'relay', token: 'saved' }, true],
        ['implicit relay', { mode: 'relay', modeChosen: false, token: '' }, false],
        ['unrelated token', { mode: null, token: 'saved' }, false],
    ])('%s explicit=%s', (_label, settings, expected) => {
        expect(hasExplicitConnectionMode(settings)).toBe(expected);
    });

    it.each([
        ['relay unavailable', false, { mode: null }, 'direct'],
        ['explicit relay', true, { mode: 'relay', modeChosen: true }, 'relay'],
        ['legacy relay token', true, { mode: 'relay', token: 'saved' }, 'relay'],
        ['explicit direct', true, { mode: 'direct' }, 'direct'],
        ['implicit relay', true, { mode: 'relay' }, null],
        ['invalid chosen mode', true, { mode: 'other', modeChosen: true }, null],
    ])('%s resolves to %s', (_label, relayAvailable, serverSettings, expected) => {
        expect(resolveConnectionMode({ relayAvailable, serverSettings })).toBe(expected);
    });
});

describe('settings policy setup payload', () => {
    it.each([
        ['provider only', ['soniox', null, {}], { provider: 'soniox' }],
        ['direct key and region', ['soniox', 'key', { mode: 'direct', region: 'eu' }], {
            provider: 'soniox', mode: 'direct', api_key: 'key', soniox_region: 'eu',
        }],
        ['relay token excludes provider key', ['soniox', 'key', { mode: 'relay', token: 'token', region: 'jp' }], {
            provider: 'soniox', mode: 'relay', token: 'token', soniox_region: 'jp',
        }],
        ['relay without token', ['soniox', 'key', { mode: 'relay' }], {
            provider: 'soniox', mode: 'relay',
        }],
        ['non-Soniox region ignored', ['gemini', 'key', { mode: 'direct', region: 'eu' }], {
            provider: 'gemini', mode: 'direct', api_key: 'key',
        }],
        ['key without explicit mode', ['gemini', 'key', {}], {
            provider: 'gemini', api_key: 'key',
        }],
    ])('%s', (_label, args, expected) => {
        expect(buildSetupBody(...args)).toEqual(expected);
    });

    it.each([
        ['us', 'us'],
        [' EU ', 'eu'],
        ['JP', 'jp'],
        ['', 'us'],
        [null, 'us'],
        ['invalid', 'us'],
    ])('normalizes region %j to %s', (input, expected) => {
        expect(normalizeSonioxRegion(input)).toBe(expected);
    });
});

describe('settings policy save decisions', () => {
    const directBaseline = {
        provider: 'soniox',
        region: 'us',
        apiKeyToPush: null,
        previousKey: '',
        translationProvider: 'soniox',
        backendMode: 'direct',
        backendSonioxCustomUrl: false,
        backendSonioxRegion: 'us',
        backendKeySource: 'env',
        setupRequired: false,
    };

    it.each([
        ['already synchronized', {}, false],
        ['backend requests setup', { setupRequired: true }, true],
        ['provider changed', { provider: 'gemini' }, true],
        ['mode changed', { backendMode: 'relay' }, true],
        ['region changed', { region: 'eu' }, true],
        ['key changed', { apiKeyToPush: 'new-key', previousKey: 'old-key', backendKeySource: 'localstorage' }, true],
        ['key source changed', { apiKeyToPush: 'same', previousKey: 'same' }, true],
        ['region case only', { region: ' US ' }, false],
        ['custom endpoint pins region', { region: 'eu', backendSonioxCustomUrl: true }, false],
        ['Gemini ignores region', { provider: 'gemini', translationProvider: 'gemini', region: 'eu' }, false],
        ['empty region is ignored', { region: '' }, false],
    ])('direct: %s', (_label, patch, expected) => {
        expect(directSettingsNeedSetup({ ...directBaseline, ...patch })).toBe(expected);
    });

    const relayBaseline = {
        provider: 'soniox',
        token: 'token',
        translationProvider: 'soniox',
        backendMode: 'relay',
        backendLoggedIn: true,
        setupRequired: false,
    };

    it.each([
        ['already synchronized', {}, false],
        ['backend requests setup', { setupRequired: true }, true],
        ['provider changed', { provider: 'gemini' }, true],
        ['mode changed', { backendMode: 'direct' }, true],
        ['token must be pushed', { backendLoggedIn: false }, true],
        ['missing token does not itself require setup', { token: '', backendLoggedIn: false }, false],
    ])('relay: %s', (_label, patch, expected) => {
        expect(relaySettingsNeedSetup({ ...relayBaseline, ...patch })).toBe(expected);
    });
});

describe('settings policy provider synchronization', () => {
    const directBaseline = {
        lockManualControls: false,
        providerSettings: { providerOverride: null, keys: {} },
        translationProvider: 'soniox',
        backendSonioxCustomUrl: false,
        backendSonioxRegion: 'us',
        connectionMode: 'direct',
        serverSettings: {},
        backendMode: 'direct',
        backendLoggedIn: false,
        backendKeySource: 'env',
        pushedOverrideBootId: null,
        backendBootId: 'boot-1',
    };

    it.each([
        ['manual controls locked', { lockManualControls: true }],
        ['connection undecided', { connectionMode: null }],
        ['direct already synchronized', {}],
        ['same boot already pushed', {
            backendMode: 'relay', pushedOverrideBootId: 'boot-1', backendBootId: 'boot-1',
        }],
        ['relay without token', {
            connectionMode: 'relay', backendMode: 'direct', serverSettings: { token: '' },
        }],
        ['relay already synchronized', {
            connectionMode: 'relay', backendMode: 'relay', backendLoggedIn: true,
            serverSettings: { token: 'token' },
        }],
    ])('%s produces no push', (_label, patch) => {
        expect(buildProviderSyncPlan({ ...directBaseline, ...patch })).toBeNull();
    });

    it('builds a relay push for a provider mismatch', () => {
        expect(buildProviderSyncPlan({
            ...directBaseline,
            providerSettings: { providerOverride: 'gemini', keys: {}, sonioxRegion: 'jp' },
            connectionMode: 'relay',
            backendMode: 'relay',
            backendLoggedIn: true,
            serverSettings: { token: 'relay-token' },
        })).toEqual({
            provider: 'gemini',
            apiKey: null,
            options: {
                silent: true, mode: 'relay', token: 'relay-token', region: 'jp',
            },
        });
    });

    it.each([
        ['override key source', {
            providerSettings: { providerOverride: null, keys: { soniox: 'local-key' } },
        }, {
            provider: 'soniox', apiKey: 'local-key',
            options: { silent: true, mode: 'direct', region: 'us' },
        }],
        ['saved region', {
            providerSettings: { providerOverride: null, keys: {}, sonioxRegion: 'EU' },
        }, {
            provider: 'soniox', apiKey: null,
            options: { silent: true, mode: 'direct', region: 'eu' },
        }],
        ['custom endpoint with provider mismatch', {
            providerSettings: { providerOverride: 'gemini', keys: {} },
            backendSonioxCustomUrl: true,
        }, {
            provider: 'gemini', apiKey: null,
            options: { silent: true, mode: 'direct', region: null },
        }],
    ])('builds a direct push for %s', (_label, patch, expected) => {
        expect(buildProviderSyncPlan({ ...directBaseline, ...patch })).toEqual(expected);
    });
});

describe('settings policy forced and preopened panels', () => {
    it.each([
        ['locked controls', { lockManualControls: true, connectionMode: 'relay' }, null],
        ['relay without token', { connectionMode: 'relay', serverSettings: {} }, 'login'],
        ['relay requiring setup', {
            connectionMode: 'relay', serverSettings: { token: 'token' }, setupRequired: true,
        }, 'login'],
        ['relay ready', {
            connectionMode: 'relay', serverSettings: { token: 'token' }, setupRequired: false,
        }, null],
        ['direct requiring setup', { connectionMode: 'direct', setupRequired: true }, 'settings'],
        ['direct ready', { connectionMode: 'direct', setupRequired: false }, null],
        ['undecided', { connectionMode: null, setupRequired: true }, null],
    ])('force-open: %s', (_label, input, expected) => {
        expect(resolveForceOpenAction(input)).toBe(expected);
    });

    it.each([
        ['first launch', {}, true],
        ['explicit relay without token', { serverSettings: { mode: 'relay', modeChosen: true } }, true],
        ['manual controls locked', { lockManualControls: true }, false],
        ['relay unavailable', { relayAvailable: false }, false],
        ['server URL missing', { relayServerUrl: '' }, false],
        ['already logged in', { serverSettings: { mode: 'relay', token: 'token' } }, false],
        ['explicit direct', { serverSettings: { mode: 'direct' } }, false],
        ['chosen invalid mode', { serverSettings: { mode: null, modeChosen: true } }, false],
    ])('preopen: %s', (_label, patch, expected) => {
        const input = {
            lockManualControls: false,
            relayAvailable: true,
            relayServerUrl: 'https://relay.example',
            serverSettings: { mode: null, modeChosen: false },
            ...patch,
        };
        expect(shouldPreopenHostedLogin(input)).toBe(expected);
    });
});

describe('settings policy exports', () => {
    it('publishes immutable current Soniox regions', () => {
        expect(SettingsPolicy.SONIOX_REGIONS).toEqual(['us', 'eu', 'jp']);
        expect(Object.isFrozen(SettingsPolicy.SONIOX_REGIONS)).toBe(true);
    });
});

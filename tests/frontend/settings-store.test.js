const SettingsStore = require('../../static/js/settings-store');

function createMemoryStorage(initial = {}) {
    const values = new Map(Object.entries(initial).map(([key, value]) => [key, String(value)]));
    return {
        getItem(key) {
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            values.set(key, String(value));
        },
        removeItem(key) {
            values.delete(key);
        },
        clear() {
            values.clear();
        },
        json(key) {
            const value = this.getItem(key);
            return value === null ? null : JSON.parse(value);
        },
    };
}

describe('settings-store URL helpers', () => {
    it('accepts only HTTP(S) links and normalizes server bucket URLs', () => {
        expect(SettingsStore.safeHttpUrl(' HTTPS://Example.COM/path ')).toBe('https://example.com/path');
        expect(SettingsStore.safeHttpUrl('javascript:alert(1)')).toBe('');
        expect(SettingsStore.safeHttpUrl('not a URL')).toBe('');

        expect(SettingsStore.normalizeServerUrl('https://EXAMPLE.com/path///?ignored=1#hash'))
            .toBe('https://example.com/path');
        expect(SettingsStore.normalizeServerUrl(' custom-server/// ')).toBe('custom-server');
    });
});

describe('settings-store server credentials', () => {
    it('returns current defaults when storage is empty', () => {
        const store = SettingsStore.create({
            storage: createMemoryStorage(),
            getRelayServerUrl: () => 'https://relay.example/',
        });

        expect(store.loadServerSettingsRaw()).toEqual({ mode: null, modeChosen: false, servers: {} });
        expect(store.loadServerSettings()).toEqual({
            mode: null,
            modeChosen: false,
            token: '',
            displayName: '',
            trustRank: '',
            servers: {},
        });
    });

    it('uses the relay URL getter dynamically and preserves other server buckets', () => {
        const storage = createMemoryStorage();
        let relayUrl = 'https://ONE.example/';
        const store = SettingsStore.create({ storage, getRelayServerUrl: () => relayUrl });

        expect(store.saveServerSettings({
            mode: 'relay', modeChosen: true, token: 'token-one', displayName: 'One', trustRank: 'user',
        })).toBe(true);
        relayUrl = 'https://two.example/base/';
        expect(store.saveServerSettings({
            mode: 'relay', modeChosen: true, token: 'token-two', displayName: 'Two', trustRank: 'trusted_user',
        })).toBe(true);

        const raw = storage.json(SettingsStore.KEYS.server);
        expect(raw.servers).toEqual({
            'https://one.example': { token: 'token-one', displayName: 'One', trustRank: 'user' },
            'https://two.example/base': { token: 'token-two', displayName: 'Two', trustRank: 'trusted_user' },
        });

        relayUrl = 'https://one.example';
        expect(store.loadServerSettings()).toMatchObject({ token: 'token-one', displayName: 'One' });
        relayUrl = 'https://two.example/base';
        expect(store.loadServerSettings()).toMatchObject({ token: 'token-two', displayName: 'Two' });
    });

    it('views legacy top-level credentials and migrates them on save', () => {
        const storage = createMemoryStorage({
            [SettingsStore.KEYS.server]: JSON.stringify({
                mode: 'relay',
                token: 'legacy-token',
                displayName: 'Legacy',
                trustRank: 'known_user',
            }),
        });
        const store = SettingsStore.create({
            storage,
            getRelayServerUrl: () => 'https://relay.example/',
        });

        const settings = store.loadServerSettings();
        expect(settings).toMatchObject({
            token: 'legacy-token', displayName: 'Legacy', trustRank: 'known_user',
        });
        settings.modeChosen = true;
        expect(store.saveServerSettings(settings)).toBe(true);

        const raw = storage.json(SettingsStore.KEYS.server);
        expect(raw).not.toHaveProperty('token');
        expect(raw).not.toHaveProperty('displayName');
        expect(raw).not.toHaveProperty('trustRank');
        expect(raw.servers['https://relay.example']).toEqual({
            token: 'legacy-token', displayName: 'Legacy', trustRank: 'known_user',
        });
    });
});

describe('settings-store provider settings and corruption handling', () => {
    it('round-trips provider data while retaining additional settings', () => {
        const storage = createMemoryStorage();
        const store = SettingsStore.create({ storage });
        const settings = {
            providerOverride: 'gemini',
            keys: { gemini: 'secret' },
            sonioxRegion: 'jp',
            hideSpeakerLabels: true,
        };

        expect(store.saveProviderSettings(settings)).toBe(true);
        expect(store.loadProviderSettings()).toEqual(settings);
    });

    it('falls back for bad JSON and repairs a malformed provider keys field', () => {
        const storage = createMemoryStorage({
            [SettingsStore.KEYS.server]: '{bad',
            [SettingsStore.KEYS.provider]: '{bad',
        });
        const store = SettingsStore.create({ storage });

        expect(store.loadServerSettingsRaw()).toEqual({ mode: null, modeChosen: false, servers: {} });
        expect(store.loadProviderSettings()).toEqual({ providerOverride: null, keys: {} });

        storage.setItem(SettingsStore.KEYS.provider, JSON.stringify({ providerOverride: 'soniox', keys: 'bad' }));
        expect(store.loadProviderSettings()).toEqual({ providerOverride: 'soniox', keys: {} });
    });

    it('tolerates Storage getters, writers, removers, and clear throwing', () => {
        const storage = {
            getItem() { throw new Error('blocked'); },
            setItem() { throw new Error('blocked'); },
            removeItem() { throw new Error('blocked'); },
            clear() { throw new Error('blocked'); },
        };
        const store = SettingsStore.create({
            storage,
            getRelayServerUrl: () => { throw new Error('not ready'); },
        });

        expect(store.loadServerSettings()).toMatchObject({ token: '', mode: null });
        expect(store.loadProviderSettings()).toEqual({ providerOverride: null, keys: {} });
        expect(store.saveServerSettings({})).toBe(false);
        expect(store.saveProviderSettings({})).toBe(false);
        expect(store.remove('anything')).toBe(false);
        expect(store.clear()).toBe(false);
    });
});

describe('settings-store typed preferences', () => {
    it('matches current defaults and round-trips typed values', () => {
        const storage = createMemoryStorage();
        const store = SettingsStore.create({ storage });

        expect(store.loadUiTranslationMode()).toBeNull();
        expect(store.loadTranslationUiMode()).toBe('hybrid');
        expect(store.loadLlmRefineMode()).toBeNull();
        expect(store.loadSegmentMode()).toBe('punctuation');
        expect(store.loadDisplayMode()).toBe('both');
        expect(store.loadAutoRestartEnabled()).toBe(true);
        expect(store.loadBottomSafeAreaEnabled()).toBe(false);
        expect(store.loadBundledCjkFontEnabled()).toBe(false);
        expect(store.loadAudioSource()).toBe('system');
        expect(store.loadClientUpdateReminder()).toBe(0);

        expect(store.saveUiTranslationMode('two_way')).toBe(true);
        expect(store.saveTranslationUiMode('accurate')).toBe(true);
        expect(store.saveLlmRefineMode('translate')).toBe(true);
        expect(store.saveSegmentMode('endpoint')).toBe(true);
        expect(store.saveDisplayMode('translation')).toBe(true);
        expect(store.saveAutoRestartEnabled(false)).toBe(true);
        expect(store.saveSleepOnSilenceEnabled(false)).toBe(true);
        expect(store.saveBottomSafeAreaEnabled(true)).toBe(true);
        expect(store.saveBundledCjkFontEnabled(true)).toBe(true);
        expect(store.saveAudioSource('mix')).toBe(true);
        expect(store.saveClientUpdateReminder(1234)).toBe(true);

        expect(store.loadUiTranslationMode()).toBe('two_way');
        expect(store.loadTranslationUiMode()).toBe('accurate');
        expect(store.loadLlmRefineMode()).toBe('translate');
        expect(store.loadSegmentMode()).toBe('endpoint');
        expect(store.loadDisplayMode()).toBe('translation');
        expect(store.loadAutoRestartEnabled()).toBe(false);
        expect(store.readSleepOnSilenceEnabled()).toBe(false);
        expect(store.loadSleepOnSilenceEnabled()).toBe(false);
        expect(store.loadBottomSafeAreaEnabled()).toBe(true);
        expect(store.loadBundledCjkFontEnabled()).toBe(true);
        expect(store.loadAudioSource()).toBe('mix');
        expect(store.loadClientUpdateReminder()).toBe(1234);
    });

    it('preserves legacy migrations and existing corrupt-value behavior', () => {
        const storage = createMemoryStorage({
            [SettingsStore.KEYS.translationUiMode]: 'refine',
            [SettingsStore.KEYS.llmRefineEnabled]: 'true',
            [SettingsStore.KEYS.segmentMode]: 'bogus',
            [SettingsStore.KEYS.displayMode]: 'bogus',
            [SettingsStore.KEYS.autoRestartEnabled]: 'bogus',
            [SettingsStore.KEYS.audioSource]: 'bogus',
        });
        const store = SettingsStore.create({ storage });

        expect(store.readTranslationUiMode()).toBe('hybrid');
        expect(store.loadLlmRefineMode()).toBe('refine');
        expect(store.loadSegmentMode()).toBe('punctuation');
        // app.js currently accepts any non-empty displayMode string at startup.
        expect(store.loadDisplayMode()).toBe('bogus');
        expect(store.loadAutoRestartEnabled()).toBe(false);
        expect(store.loadAudioSource()).toBe('system');
        expect(store.saveUiTranslationMode('bogus')).toBe(false);
        expect(store.saveSegmentMode('bogus')).toBe(false);
    });
});

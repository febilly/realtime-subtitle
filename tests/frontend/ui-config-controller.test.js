const UiConfigController = require('../../static/js/ui-config-controller');

function response(body = {}, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: vi.fn(async () => body),
    };
}

function createHarness(options = {}) {
    const runtime = {
        backendBootId: 'boot-old',
        translationProvider: 'soniox',
        backendTranslationMode: 'one_way',
        uiTranslationMode: 'one_way',
        lockManualControls: false,
        segmentModeSupported: true,
        twoWaySupported: false,
        hideSpeakerLabels: false,
        ...(options.state || {}),
    };
    const patches = [];
    const events = [];
    const updateState = vi.fn((patch) => {
        patches.push({ ...patch });
        Object.assign(runtime, patch);
    });
    const translationModeController = {
        applyBackendConfig: vi.fn((...args) => events.push(['translation', ...args])),
    };
    const segmentModeController = {
        applyBackendConfig: vi.fn((...args) => events.push(['segment', ...args])),
    };
    const themeController = {
        setChromaEnabled: vi.fn((...args) => events.push(['theme', ...args])),
    };
    const action = (name, result) => vi.fn((...args) => {
        events.push([name, ...args]);
        return typeof result === 'function' ? result(...args) : result;
    });
    const actions = {
        sessionCostReset: action('sessionCostReset'),
        setLanguageListFromCodes: action('languages'),
        resetFirstRedeemBonus: action('bonus'),
        updateBalanceBarVisibility: action('balance'),
        updateAccountSection: action('account'),
        updateSettingsButtonVisibility: action('settings'),
        applyBundledCjkFontPreference: action('disableFont'),
        renderBundledCjkFontPicker: action('renderFont'),
        getStoredHideSpeakerLabelsSetting: action(
            'storedSpeaker',
            options.storedHideSpeakerLabels ?? null,
        ),
        setSpeakerLabelsHidden: action('setSpeaker'),
        applySpeakerLabelVisibility: action('speakerVisibility'),
        renderRuntimeSettingsPickers: action('pickers'),
        applyLockPauseRestartControlsUI: action('lockUi'),
        enforceTranslateSegmentMode: action('enforceSegment'),
    };
    const safeHttpUrl = vi.fn((value) => (value ? `safe:${String(value).trim()}` : null));
    const normalizeSonioxRegion = vi.fn((value) => `region:${String(value).trim().toLowerCase()}`);
    const logger = { error: vi.fn() };
    const fetch = options.fetch || vi.fn(async () => response(options.responseBody || {}));
    const controller = UiConfigController.create({
        fetch,
        safeHttpUrl,
        normalizeSonioxRegion,
        getState: () => runtime,
        updateState,
        translationModeController,
        segmentModeController,
        themeController,
        actions,
        console: logger,
    });
    return {
        actions,
        controller,
        events,
        fetch,
        logger,
        normalizeSonioxRegion,
        patches,
        runtime,
        safeHttpUrl,
        segmentModeController,
        themeController,
        translationModeController,
        updateState,
    };
}

describe('UiConfigController dependencies and fetching', () => {
    it('validates normalizers and subordinate controllers', () => {
        expect(() => UiConfigController.create({ fetch: vi.fn() })).toThrow(
            'UiConfigController.create requires URL/region normalizers',
        );
        expect(() => UiConfigController.create({
            fetch: vi.fn(),
            safeHttpUrl: vi.fn(),
            normalizeSonioxRegion: vi.fn(),
        })).toThrow('UiConfigController.create requires translation mode controller');
    });

    it('returns false for non-OK responses and logs fetch or parse failures', async () => {
        const nonOk = createHarness({
            fetch: vi.fn(async () => response({}, { ok: false })),
        });
        await expect(nonOk.controller.fetch()).resolves.toBe(false);
        expect(nonOk.updateState).not.toHaveBeenCalled();

        const failure = new Error('offline');
        const broken = createHarness({
            fetch: vi.fn(async () => { throw failure; }),
        });
        await expect(broken.controller.fetch()).resolves.toBe(false);
        expect(broken.logger.error).toHaveBeenCalledWith('Error fetching UI config:', failure);

        const parseFailure = new Error('invalid JSON');
        const invalid = createHarness({
            fetch: vi.fn(async () => ({
                ok: true,
                json: vi.fn(async () => { throw parseFailure; }),
            })),
        });
        await expect(invalid.controller.fetch()).resolves.toBe(false);
        expect(invalid.logger.error).toHaveBeenCalledWith(
            'Error fetching UI config:', parseFailure,
        );
    });

    it('fetches and applies a successful response', async () => {
        const page = createHarness({ responseBody: { boot_id: 'boot-new' } });
        await expect(page.controller.fetch()).resolves.toBe(true);
        expect(page.fetch).toHaveBeenCalledWith('/ui-config');
        expect(page.runtime.backendBootId).toBe('boot-new');
    });
});

describe('UiConfigController state mapping and action order', () => {
    it('maps a complete payload and preserves the existing orchestration order', () => {
        const page = createHarness({
            state: { uiTranslationMode: null },
            storedHideSpeakerLabels: null,
        });
        const data = {
            lock_manual_controls: false,
            llm_refine_available: true,
            soniox_no_translation_factor: 2,
            translation_target_lang: ' ZH ',
            provider: ' GEMINI ',
            languages: ['en', 'zh'],
            capabilities: { segment_mode: false, two_way_translation: true },
            boot_id: 'boot-new',
            setup_required: true,
            env_key_present: { soniox: 1, gemini: 0 },
            key_source: 'runtime',
            soniox_region: ' EU ',
            soniox_custom_url: true,
            relay_available: true,
            server_url: 'wss://relay.example',
            credits_purchase_url: ' https://buy.example ',
            first_redeem_bonus_credits: 12,
            client_version: ' 1.2.3 ',
            client_latest_version: ' 2.0.0 ',
            client_minimum_version: ' 1.0.0 ',
            client_update_url: ' https://update.example ',
            client_update_notes: ' notes ',
            mode: 'relay',
            logged_in: true,
            translation_mode: ' NONE ',
            target_lang_1: ' EN ',
            target_lang_2: ' JA ',
            segment_mode: 'endpoint',
            custom_font_available: false,
            speaker_diarization_enabled: false,
            hide_speaker_labels: true,
            enable_chroma_theme: true,
        };

        expect(page.controller.apply(data)).toBe(data);
        expect(page.runtime).toMatchObject({
            lockManualControls: false,
            sonioxNoTranslationFactor: 1,
            defaultTranslationTargetLang: 'zh',
            currentTranslationTargetLang: 'zh',
            translationProvider: 'gemini',
            segmentModeSupported: false,
            twoWaySupported: true,
            backendBootId: 'boot-new',
            setupRequired: true,
            envKeyPresent: { soniox: true, gemini: false },
            backendKeySource: 'runtime',
            backendSonioxRegion: 'region:eu',
            backendSonioxCustomUrl: true,
            relayAvailable: true,
            relayServerUrl: 'wss://relay.example',
            creditsPurchaseUrl: 'safe:https://buy.example',
            clientVersion: '1.2.3',
            clientLatestVersion: '2.0.0',
            clientMinimumVersion: '1.0.0',
            clientUpdateUrl: 'safe:https://update.example',
            clientUpdateNotes: 'notes',
            backendMode: 'relay',
            backendLoggedIn: true,
            backendTranslationMode: 'none',
            backendTargetLang1: 'en',
            backendTargetLang2: 'ja',
            uiTranslationMode: 'none',
            suppressTranslationDisplay: true,
            customFontAvailable: false,
            speakerDiarizationEnabled: false,
            hideSpeakerLabels: true,
        });
        expect(page.translationModeController.applyBackendConfig)
            .toHaveBeenCalledWith(data, { currentBootId: 'boot-old' });
        expect(page.normalizeSonioxRegion).toHaveBeenCalledWith(' EU ');
        expect(page.actions.applyBundledCjkFontPreference).toHaveBeenCalledWith(
            false, { persist: false, sync: false },
        );
        expect(page.events.map(([name]) => name)).toEqual([
            'translation',
            'sessionCostReset',
            'languages',
            'bonus',
            'balance',
            'account',
            'settings',
            'segment',
            'disableFont',
            'renderFont',
            'storedSpeaker',
            'speakerVisibility',
            'pickers',
            'theme',
            'speakerVisibility',
            'lockUi',
            'enforceSegment',
        ]);
    });

    it('keeps optional state when fields are missing but resets always-derived values', () => {
        const page = createHarness({
            state: {
                segmentModeSupported: false,
                twoWaySupported: true,
                customFontAvailable: true,
                clientVersion: 'kept',
                creditsPurchaseUrl: 'old',
                clientUpdateUrl: 'old',
                setupRequired: true,
            },
        });
        page.controller.apply({});

        expect(page.runtime).toMatchObject({
            segmentModeSupported: false,
            twoWaySupported: true,
            customFontAvailable: true,
            clientVersion: 'kept',
            creditsPurchaseUrl: null,
            clientUpdateUrl: null,
            setupRequired: false,
        });
        expect(page.actions.sessionCostReset).not.toHaveBeenCalled();
        expect(page.actions.applyBundledCjkFontPreference).not.toHaveBeenCalled();
        expect(page.actions.renderBundledCjkFontPicker).toHaveBeenCalledOnce();
    });

    it.each([
        ['unlocked soniox', { lockManualControls: false, translationProvider: 'soniox' }, false, true],
        ['locked soniox', { lockManualControls: true, translationProvider: 'soniox' }, true, false],
        ['unlocked gemini', { lockManualControls: false, translationProvider: 'gemini' }, true, false],
        ['no stored value', { lockManualControls: false, translationProvider: 'soniox' }, null, false],
    ])('applies speaker preference matrix: %s', (
        _label, state, storedValue, shouldSync,
    ) => {
        const page = createHarness({ state, storedHideSpeakerLabels: storedValue });
        page.controller.apply({
            lock_manual_controls: state.lockManualControls,
            provider: state.translationProvider,
            hide_speaker_labels: true,
        });

        if (storedValue === null || !shouldSync) {
            expect(page.runtime.hideSpeakerLabels).toBe(true);
            expect(page.actions.setSpeakerLabelsHidden).not.toHaveBeenCalled();
        } else {
            expect(page.runtime.hideSpeakerLabels).toBe(storedValue);
            expect(page.actions.setSpeakerLabelsHidden).toHaveBeenCalledWith(storedValue);
        }
    });

    it('does not reset session cost for the same provider or accept invalid billing factors', () => {
        const page = createHarness({
            state: { translationProvider: 'soniox', sonioxNoTranslationFactor: 0.5 },
        });
        page.controller.apply({
            provider: ' SONIOX ',
            soniox_no_translation_factor: 0,
        });
        expect(page.runtime.translationProvider).toBe('soniox');
        expect(page.runtime.sonioxNoTranslationFactor).toBe(0.5);
        expect(page.actions.sessionCostReset).not.toHaveBeenCalled();
    });
});

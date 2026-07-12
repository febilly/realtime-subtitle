const { JSDOM } = require('jsdom');
const SettingsPanel = require('../../static/js/settings-panel');
const SettingsPolicy = require('../../static/js/settings-policy');

function response(data, { ok = true } = {}) {
    return { ok, json: vi.fn().mockResolvedValue(data) };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <button id="settingsButton"></button><button id="overlayButton"></button>
        <div id="settingsOverlay" hidden></div><aside id="settingsPanel" hidden>
            <form id="settingsForm">
                <input type="radio" name="provider" value="soniox">
                <input type="radio" name="provider" value="gemini">
                <input type="radio" name="connmode" value="relay">
                <input type="radio" name="connmode" value="direct">
                <section id="modeSection"></section><section id="accountSection"></section>
                <section id="apiKeySection"></section><span id="modeDescription"></span>
                <label id="apiKeyLabel"></label><input id="apiKeyInput">
                <span id="apiKeySourceHint"></span><span id="providerDescription"></span>
                <p><a id="apiKeyGetLink"></a></p>
                <section id="sonioxRegionSection"><div id="sonioxRegionPicker"></div></section>
                <div id="settingsError"></div>
                <button id="settingsSaveButton"></button><button id="settingsCancelButton"></button>
                <button id="settingsModeBackButton"></button><button id="resetAllButton"></button>
                <button id="settingsCloseButton"></button><button id="redeemPasteButton"></button>
            </form>
            <span id="settingsVersion"></span>
        </aside>
        <span id="settingsTitle"></span><span id="modeLabel"></span>
        <span id="providerSonioxLabel"></span><span id="providerGeminiLabel"></span>
        <span id="bundledCjkFontHint"></span>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const state = {
        lockManualControls: false,
        relayAvailable: true,
        connectionMode: 'direct',
        translationProvider: 'soniox',
        backendSonioxRegion: 'us',
        backendSonioxCustomUrl: false,
        envKeyPresent: { soniox: false, gemini: false },
        setupRequired: false,
        clientVersion: '1.2.3',
        canRefreshBalance: false,
        ...overrides.state,
    };
    let providerSettings = {
        providerOverride: null,
        sonioxRegion: 'jp',
        keys: {},
        ...overrides.providerSettings,
    };
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response({ pricing: {} }));
    const selectCalls = [];
    const buildCustomSelect = vi.fn((selectOptions, config = {}) => {
        const picker = document.createElement('div');
        picker.className = 'fake-picker';
        picker.value = config.value;
        picker.disabled = !!config.disabled;
        picker.selectOptions = selectOptions;
        selectCalls.push({ selectOptions, config, picker });
        return picker;
    });
    const actionNames = [
        'renderMicrophoneDevicePicker', 'renderRuntimeSettingsPickers',
        'renderBundledCjkFontPicker', 'renderTranslationModePicker',
        'fetchMicrophoneDevices', 'fetchBalance', 'updateAccountSection',
    ];
    const actions = Object.fromEntries(actionNames.map((name) => [name, vi.fn()]));
    Object.assign(actions, overrides.actions);
    const freePoolsSummary = overrides.freePoolsSummary || vi.fn(() => '');
    const t = (key, vars = {}) => [key, ...Object.entries(vars).map(
        ([name, value]) => `${name}=${value}`,
    )].join('|');
    const controller = SettingsPanel.create({
        policy: SettingsPolicy,
        billing: { formatRate: (value) => `R${value}` },
        document,
        fetch,
        t,
        buildCustomSelect,
        loadProviderSettings: () => ({
            ...providerSettings,
            keys: { ...(providerSettings.keys || {}) },
        }),
        freePoolsSummary,
        getState: () => state,
        actions,
        elements: {
            settingsButton: document.getElementById('settingsButton'),
            overlayButton: document.getElementById('overlayButton'),
            overlay: document.getElementById('settingsOverlay'),
            panel: document.getElementById('settingsPanel'),
            form: document.getElementById('settingsForm'),
            closeButton: document.getElementById('settingsCloseButton'),
            cancelButton: document.getElementById('settingsCancelButton'),
            saveButton: document.getElementById('settingsSaveButton'),
            backButton: document.getElementById('settingsModeBackButton'),
            resetButton: document.getElementById('resetAllButton'),
            errorElement: document.getElementById('settingsError'),
            apiKeyLabel: document.getElementById('apiKeyLabel'),
            apiKeyInput: document.getElementById('apiKeyInput'),
            apiKeySourceHint: document.getElementById('apiKeySourceHint'),
            providerDescription: document.getElementById('providerDescription'),
            apiKeyGetLink: document.getElementById('apiKeyGetLink'),
            sonioxRegionSection: document.getElementById('sonioxRegionSection'),
            sonioxRegionPickerHost: document.getElementById('sonioxRegionPicker'),
            modeSection: document.getElementById('modeSection'),
            accountSection: document.getElementById('accountSection'),
            apiKeySection: document.getElementById('apiKeySection'),
            modeDescription: document.getElementById('modeDescription'),
            redeemPasteButton: document.getElementById('redeemPasteButton'),
            versionElement: document.getElementById('settingsVersion'),
        },
    });
    return {
        actions,
        buildCustomSelect,
        controller,
        document,
        dom,
        fetch,
        freePoolsSummary,
        selectCalls,
        setProviderSettings(value) { providerSettings = value; },
        state,
    };
}

describe('SettingsPanel provider and mode drafts', () => {
    it('populates persisted provider, mode, key, region, and setup warning', () => {
        const page = setup({
            providerSettings: {
                providerOverride: 'soniox', sonioxRegion: 'eu', keys: { soniox: 'saved-key' },
            },
            state: { connectionMode: 'direct', setupRequired: true },
        });
        page.controller.populate();

        expect(page.controller.getSelectedProvider()).toBe('soniox');
        expect(page.controller.getMode()).toBe('direct');
        expect(page.controller.getSelectedSonioxRegion()).toBe('eu');
        expect(page.document.getElementById('apiKeyInput').value).toBe('saved-key');
        expect(page.document.getElementById('apiKeyInput').placeholder).toBe('');
        expect(page.document.getElementById('settingsError').textContent).toBe('setup_required_hint');
        expect(page.actions.renderRuntimeSettingsPickers).toHaveBeenCalledOnce();
        expect(page.actions.updateAccountSection).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('falls back to the backend provider and forces direct when relay is unavailable', () => {
        const page = setup({
            providerSettings: { providerOverride: null, keys: {} },
            state: { translationProvider: 'gemini', relayAvailable: false, connectionMode: 'relay' },
        });
        page.controller.populate();

        expect(page.controller.getSelectedProvider()).toBe('gemini');
        expect(page.controller.getMode()).toBe('direct');
        expect(page.document.getElementById('modeSection').hidden).toBe(true);
        expect(page.document.getElementById('accountSection').hidden).toBe(true);
        expect(page.document.getElementById('apiKeySection').hidden).toBe(false);
        page.dom.window.close();
    });

    it('shows override, env-configured, and missing-key states with provider links', () => {
        const page = setup({
            providerSettings: { keys: { soniox: 'override' } },
            state: { envKeyPresent: { soniox: true, gemini: true } },
        });

        page.controller.updateApiKeyField('soniox');
        expect(page.document.getElementById('apiKeyInput').value).toBe('override');
        expect(page.document.getElementById('apiKeyInput').placeholder).toBe('');
        expect(page.document.getElementById('apiKeyGetLink').href)
            .toBe('https://console.soniox.com/api-keys');

        page.setProviderSettings({ keys: {} });
        page.controller.updateApiKeyField('gemini');
        expect(page.document.getElementById('apiKeyInput').placeholder)
            .toBe('api_key_placeholder_env_configured|provider=provider_gemini');
        page.state.envKeyPresent.gemini = false;
        page.controller.updateApiKeyField('gemini');
        expect(page.document.getElementById('apiKeyInput').placeholder)
            .toBe('api_key_placeholder_env_missing|provider=provider_gemini');
        expect(page.document.getElementById('apiKeyGetLink').href)
            .toBe('https://aistudio.google.com/apikey');
        page.dom.window.close();
    });

    it('shows region only for direct Soniox and renders custom endpoints disabled', () => {
        const page = setup();
        page.controller.setMode('direct');
        page.controller.updateSonioxRegion('soniox');
        expect(page.document.getElementById('sonioxRegionSection').hidden).toBe(false);
        expect(page.selectCalls.at(-1).config).toMatchObject({ value: 'jp' });

        page.controller.updateSonioxRegion('gemini');
        expect(page.document.getElementById('sonioxRegionSection').hidden).toBe(true);
        page.controller.setMode('relay');
        page.controller.updateSonioxRegion('soniox');
        expect(page.document.getElementById('sonioxRegionSection').hidden).toBe(true);

        page.state.backendSonioxCustomUrl = true;
        page.controller.renderSonioxRegionPicker(null);
        expect(page.selectCalls.at(-1).config).toEqual({ value: 'custom', disabled: true });
        expect(page.controller.getSelectedSonioxRegion()).toBeNull();
        page.dom.window.close();
    });
});

describe('SettingsPanel pricing and lifecycle', () => {
    it('renders relay loading, free-pool, and paid pricing descriptions', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            pricing: {
                soniox: { price_per_second: 0, free_pools: [{ period: 'daily' }] },
                gemini: { price_per_second: 0.25, free_pools: [] },
            },
        }));
        const freePoolsSummary = vi.fn((pools) => pools.length ? '日 100' : '');
        const page = setup({
            fetch,
            freePoolsSummary,
            state: { connectionMode: 'relay' },
        });
        page.controller.setMode('relay');
        expect(page.controller.getProviderDescription('soniox')).toBe('provider_relay_desc_loading');

        await expect(page.controller.fetchRelayPricing()).resolves.toBe(true);
        expect(page.controller.getProviderDescription('soniox'))
            .toBe('provider_relay_desc_free (日 100)');
        expect(page.controller.getProviderDescription('gemini'))
            .toBe('provider_relay_desc|price=R0.25|minutePrice=R15');
        page.dom.window.close();
    });

    it('treats pricing HTTP and network failures as non-fatal', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({}, { ok: false }))
            .mockRejectedValueOnce(new Error('offline'));
        const page = setup({ fetch });
        await expect(page.controller.fetchRelayPricing()).resolves.toBe(false);
        await expect(page.controller.fetchRelayPricing()).resolves.toBe(false);
        page.dom.window.close();
    });

    it('blocks manual open while locked and enforces forced-panel close rules', () => {
        const page = setup();
        page.state.lockManualControls = true;
        expect(page.controller.open()).toBe(false);
        expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        page.controller.updateButtonVisibility();
        expect(page.document.getElementById('settingsButton').style.display).toBe('none');

        page.state.lockManualControls = false;
        expect(page.controller.open({ forced: true })).toBe(true);
        expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        expect(page.document.getElementById('settingsCloseButton').style.display).toBe('none');
        expect(page.document.getElementById('settingsCancelButton').style.display).toBe('none');
        expect(page.document.getElementById('settingsModeBackButton').hidden).toBe(false);
        expect(page.controller.close()).toBe(false);
        expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        page.controller.hide();
        expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        page.dom.window.close();
    });

    it('rebuilds localized region choices without losing the current draft', () => {
        const page = setup();
        page.controller.renderSonioxRegionPicker('eu');
        page.controller.applyI18n();

        expect(page.selectCalls.at(-1).config.value).toBe('eu');
        expect(page.document.getElementById('settingsTitle').textContent).toBe('settings');
        expect(page.document.getElementById('settingsVersion').textContent)
            .toBe('client_version|version=1.2.3');
        expect(page.document.getElementById('redeemPasteButton').getAttribute('aria-label'))
            .toBe('login_paste');
        page.dom.window.close();
    });

    it('binds provider and mode changes once, refreshes balance, and destroys cleanly', async () => {
        const page = setup({ state: { canRefreshBalance: true } });
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        const gemini = page.document.querySelector('input[name="provider"][value="gemini"]');
        gemini.checked = true;
        gemini.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
        expect(page.actions.fetchBalance).toHaveBeenCalledWith({ provider: 'gemini', force: true });
        expect(page.actions.renderRuntimeSettingsPickers).toHaveBeenCalledOnce();

        const relay = page.document.querySelector('input[name="connmode"][value="relay"]');
        relay.checked = true;
        relay.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
        await Promise.resolve();
        expect(page.actions.updateAccountSection).toHaveBeenCalledOnce();
        expect(page.fetch).toHaveBeenCalledWith('/account/pricing');
        expect(page.actions.fetchBalance).toHaveBeenLastCalledWith({ provider: 'gemini', force: true });

        page.controller.destroy();
        gemini.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
        expect(page.actions.renderRuntimeSettingsPickers).toHaveBeenCalledOnce();
        page.dom.window.close();
    });
});

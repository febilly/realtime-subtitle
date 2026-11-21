const { JSDOM } = require('jsdom');
const SettingsPolicy = require('../../static/js/settings-policy');
const SettingsUi = require('../../static/js/settings-ui');
const OwnKeyDialog = require('../../static/js/own-key-dialog');

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="ownKeyOverlay" hidden></div>
        <div id="ownKeyDialog" hidden>
            <form id="ownKeyForm" novalidate>
                <h1 id="ownKeyTitle"></h1>
                <div id="ownKeyProviderLabel"></div>
                <fieldset id="ownKeyProviderFieldset">
                    <label><input type="radio" name="ownKeyProvider" value="soniox" id="ownKeyProviderSoniox" checked><span id="ownKeyProviderSonioxLabel"></span></label>
                    <label><input type="radio" name="ownKeyProvider" value="gemini" id="ownKeyProviderGemini"><span id="ownKeyProviderGeminiLabel"></span></label>
                </fieldset>
                <div id="ownKeyRegionSection"><span id="ownKeyRegionLabel"></span><div id="ownKeySonioxRegionPicker"></div></div>
                <label for="ownKeyApiKeyInput"><span id="ownKeyApiKeyLabel"></span><input id="ownKeyApiKeyInput" type="password"></label>
                <p id="ownKeyError"></p>
                <button id="ownKeyBackButton" type="button"></button>
                <button id="ownKeyConfirmButton" type="submit"></button>
            </form>
        </div>
    </body>`);
    const document = dom.window.document;
    const settingsUi = SettingsUi.create({ document, window: dom.window });
    let providerSettings = { keys: {}, ...(overrides.providerSettings || {}) };
    const saveProviderSettings = vi.fn((value) => { providerSettings = { ...value }; });
    const state = {
        lockManualControls: false,
        relayAvailable: true,
        translationProvider: 'soniox',
        backendSonioxRegion: 'us',
        backendSonioxCustomUrl: false,
        ...overrides.state,
    };
    const actions = {
        pushSetup: vi.fn(async () => ({ ok: true })),
        switchToOwnKeyMode: vi.fn(async () => {}),
        syncDesktopShortcutOption: vi.fn(async () => true),
        createDesktopShortcutIfRequested: vi.fn(async () => true),
        ...overrides.actions,
    };
    const controller = OwnKeyDialog.create({
        policy: SettingsPolicy,
        document,
        window: dom.window,
        t: (key, vars) => vars ? `${key}:${vars.provider || ''}` : key,
        buildCustomSelect: (options, config) => settingsUi.buildCustomSelect(options, config),
        loadProviderSettings: () => ({ ...providerSettings, keys: { ...(providerSettings.keys || {}) } }),
        saveProviderSettings,
        getState: () => state,
        actions,
        elements: {
            overlay: document.getElementById('ownKeyOverlay'),
            dialog: document.getElementById('ownKeyDialog'),
            form: document.getElementById('ownKeyForm'),
            title: document.getElementById('ownKeyTitle'),
            providerLabel: document.getElementById('ownKeyProviderLabel'),
            providerFieldset: document.getElementById('ownKeyProviderFieldset'),
            providerSonioxLabel: document.getElementById('ownKeyProviderSonioxLabel'),
            providerGeminiLabel: document.getElementById('ownKeyProviderGeminiLabel'),
            regionSection: document.getElementById('ownKeyRegionSection'),
            regionLabel: document.getElementById('ownKeyRegionLabel'),
            regionPickerHost: document.getElementById('ownKeySonioxRegionPicker'),
            apiKeyLabel: document.getElementById('ownKeyApiKeyLabel'),
            apiKeyInput: document.getElementById('ownKeyApiKeyInput'),
            errorElement: document.getElementById('ownKeyError'),
            backButton: document.getElementById('ownKeyBackButton'),
            confirmButton: document.getElementById('ownKeyConfirmButton'),
        },
    });
    controller.init();
    return {
        actions,
        controller,
        document,
        dom,
        getProviderSettings: () => providerSettings,
        saveProviderSettings,
        state,
    };
}

describe('OwnKeyDialog', () => {
    it('supports only Soniox and Gemini, toggles region visibility, and closes on Back', () => {
        const page = setup({ providerSettings: { providerOverride: 'local', keys: {} } });
        expect(page.controller.open()).toBe(true);
        expect(page.controller.isOpen()).toBe(true);
        expect(page.document.querySelectorAll('input[name="ownKeyProvider"]')).toHaveLength(2);
        expect(page.document.querySelector('input[name="ownKeyProvider"][value="local"]')).toBeNull();
        expect(page.document.getElementById('ownKeyRegionSection').hidden).toBe(false);

        const geminiRadio = page.document.getElementById('ownKeyProviderGemini');
        geminiRadio.checked = true;
        geminiRadio.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
        expect(page.document.getElementById('ownKeyRegionSection').hidden).toBe(true);
        expect(page.document.getElementById('ownKeyApiKeyLabel').textContent).toBe('provider_gemini api_key');

        page.document.getElementById('ownKeyBackButton').click();
        expect(page.controller.isOpen()).toBe(false);
        expect(page.actions.syncDesktopShortcutOption).toHaveBeenCalledWith({
            resetChecked: true,
            target: 'ownKey',
        });
        expect(page.actions.pushSetup).not.toHaveBeenCalled();
        expect(page.actions.switchToOwnKeyMode).not.toHaveBeenCalled();
        expect(page.actions.createDesktopShortcutIfRequested).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('requires a non-empty API key and clears the warning when typing', async () => {
        const page = setup();
        page.controller.open();
        const result = await page.controller.handleSubmit();
        expect(result).toEqual({ ok: false, reason: 'empty' });
        expect(page.document.getElementById('ownKeyError').textContent).toBe('api_key_required');
        expect(page.actions.pushSetup).not.toHaveBeenCalled();

        const input = page.document.getElementById('ownKeyApiKeyInput');
        input.value = 'sk-draft';
        input.dispatchEvent(new page.dom.window.Event('input', { bubbles: true }));
        expect(page.document.getElementById('ownKeyError').textContent).toBe('');
        page.dom.window.close();
    });

    it('shows unavailable warning and stays open without saving when key validation fails', async () => {
        const page = setup({
            actions: {
                pushSetup: vi.fn(async () => ({ ok: false, data: { message: 'bad key' } })),
            },
        });
        page.controller.open();
        page.document.getElementById('ownKeyApiKeyInput').value = 'sk-invalid';

        const result = await page.controller.handleSubmit();
        expect(result).toEqual({ ok: false, reason: 'invalid' });
        expect(page.controller.isOpen()).toBe(true);
        expect(page.document.getElementById('ownKeyError').textContent).toBe('own_key_unavailable');
        expect(page.saveProviderSettings).not.toHaveBeenCalled();
        expect(page.actions.switchToOwnKeyMode).not.toHaveBeenCalled();
        expect(page.actions.createDesktopShortcutIfRequested).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('saves provider, Soniox region, and key then switches mode after verification succeeds', async () => {
        const page = setup();
        page.controller.open();
        page.document.querySelector('#ownKeySonioxRegionPicker .custom-select-button').click();
        page.document.querySelectorAll('.custom-select-option')[2].click();
        page.document.getElementById('ownKeyApiKeyInput').value = ' sk-valid-jp ';

        const result = await page.controller.handleSubmit();
        expect(result).toEqual({ ok: true, provider: 'soniox', region: 'jp' });
        expect(page.actions.pushSetup).toHaveBeenCalledWith('soniox', 'sk-valid-jp', {
            silent: true,
            mode: 'direct',
            region: 'jp',
        });
        expect(page.getProviderSettings()).toMatchObject({
            providerOverride: 'soniox',
            sonioxRegion: 'jp',
            keys: { soniox: 'sk-valid-jp' },
        });
        expect(page.controller.isOpen()).toBe(false);
        expect(page.actions.createDesktopShortcutIfRequested).toHaveBeenCalledWith({ target: 'ownKey' });
        expect(page.actions.switchToOwnKeyMode).toHaveBeenCalledWith({ forced: false });
        page.dom.window.close();
    });

    it('keeps the anchored dialog top edge fixed when toggling between Soniox and Gemini', async () => {
        const page = setup();
        page.dom.window.innerHeight = 800;
        const originalRect = page.dom.window.HTMLElement.prototype.getBoundingClientRect;
        page.dom.window.HTMLElement.prototype.getBoundingClientRect = function rect() {
            if (!this.id && this.classList.contains('own-key-dialog')) {
                const region = this.querySelector('.own-key-region-section');
                return { height: region && !region.hidden ? 380 : 300 };
            }
            return originalRect.call(this);
        };
        try {
            const sonioxRadio = page.document.getElementById('ownKeyProviderSoniox');
            const geminiRadio = page.document.getElementById('ownKeyProviderGemini');
            const dialog = page.document.getElementById('ownKeyDialog');
            dialog.classList.add('own-key-dialog');
            page.document.getElementById('ownKeyRegionSection').classList.add('auth-section', 'own-key-region-section');

            page.controller.open();
            const initialTop = dialog.style.getPropertyValue('--own-key-dialog-top');
            expect(initialTop).toBe('210px');

            geminiRadio.checked = true;
            geminiRadio.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
            expect(dialog.style.getPropertyValue('--own-key-dialog-top')).toBe(initialTop);

            await page.controller.handleSubmit();
            expect(dialog.style.getPropertyValue('--own-key-dialog-top')).toBe(initialTop);

            sonioxRadio.checked = true;
            sonioxRadio.dispatchEvent(new page.dom.window.Event('change', { bubbles: true }));
            expect(dialog.style.getPropertyValue('--own-key-dialog-top')).toBe(initialTop);
        } finally {
            page.dom.window.HTMLElement.prototype.getBoundingClientRect = originalRect;
            page.dom.window.close();
        }
    });
});

const { JSDOM } = require('jsdom');
const SettingsPolicy = require('../../static/js/settings-policy');
const HostedMode = require('../../static/js/hosted-mode');

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="overlay" hidden></div><section id="chooser" hidden>
            <h2 id="modeChooserTitle"></h2><p id="modeChooserHint"></p>
            <button id="relay"><span id="modeChooserRelayTitle"></span><span id="modeChooserRelayDesc"></span></button>
            <button id="direct"><span id="modeChooserDirectTitle"></span><span id="modeChooserDirectDesc"></span></button>
        </section>
    </body>`);
    const document = dom.window.document;
    let server = {
        mode: null,
        modeChosen: false,
        token: '',
        ...overrides.server,
    };
    const state = {
        lockManualControls: false,
        relayAvailable: true,
        relayServerUrl: 'https://relay.example',
        connectionMode: null,
        ...overrides.state,
    };
    const events = [];
    const actions = Object.fromEntries([
        'openLogin', 'hideLogin', 'applyLoginI18n', 'updateLoginSubmitState',
        'resetBootGuard', 'hideSettingsPanel', 'ensureHostedVersionAllowed',
        'syncProviderFromStorage', 'maybeForceOpenSettings', 'updateBalanceBarVisibility',
        'setModeRadio', 'applyModeSectionsVisibility', 'updateAccountSection', 'openSettings',
    ].map((name) => [name, vi.fn(async (...args) => { events.push([name, ...args]); })]));
    Object.assign(actions, overrides.actions);
    const saveServerSettings = vi.fn((value) => { server = { ...value }; });
    const controller = HostedMode.create({
        policy: SettingsPolicy,
        document,
        t: (key) => key,
        loadServerSettings: () => ({ ...server }),
        saveServerSettings,
        getState: () => state,
        actions,
        elements: {
            chooserOverlay: document.getElementById('overlay'),
            chooser: document.getElementById('chooser'),
            relayButton: document.getElementById('relay'),
            directButton: document.getElementById('direct'),
        },
    });
    return {
        actions,
        controller,
        document,
        dom,
        events,
        getServer: () => server,
        saveServerSettings,
        state,
    };
}

describe('HostedMode chooser and startup', () => {
    it.each([
        ['first launch', {}, true],
        ['explicit direct', { server: { mode: 'direct' } }, false],
        ['saved relay token', { server: { mode: 'relay', token: 'token' } }, false],
        ['locked', { state: { lockManualControls: true } }, false],
        ['no relay URL', { state: { relayServerUrl: '' } }, false],
    ])('preopen decision: %s', (_label, overrides, expected) => {
        const page = setup(overrides);
        expect(page.controller.shouldPreopenHostedLogin()).toBe(expected);
        page.dom.window.close();
    });

    it('preopens login, persists the hosted default, and refreshes localized state', () => {
        const page = setup();
        expect(page.controller.preopenHostedLoginIfNeeded()).toBe(true);
        expect(page.getServer()).toMatchObject({ mode: 'relay', modeChosen: true });
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: true });

        page.state.connectionMode = 'relay';
        page.controller.refreshPreopenedHostedLogin();
        expect(page.actions.applyLoginI18n).toHaveBeenCalledOnce();
        expect(page.actions.updateLoginSubmitState).toHaveBeenCalledOnce();
        page.state.relayServerUrl = '';
        page.controller.refreshPreopenedHostedLogin();
        expect(page.actions.hideLogin).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('opens the chooser, localizes it, and persists a direct choice', async () => {
        const page = setup();
        const result = page.controller.openModeChooser();
        expect(page.document.getElementById('chooser').hidden).toBe(false);
        expect(page.document.getElementById('modeChooserTitle').textContent).toBe('chooser_title');
        page.document.getElementById('direct').click();
        await expect(result).resolves.toBe('direct');
        expect(page.getServer()).toMatchObject({ mode: 'direct', modeChosen: true });
        expect(page.document.getElementById('chooser').hidden).toBe(true);
        page.dom.window.close();
    });

    it('defaults first launch to direct without relay and hosted with relay', async () => {
        const direct = setup({ state: { relayAvailable: false } });
        await direct.controller.maybeRunFirstLaunchFlow();
        expect(direct.getServer()).toMatchObject({ mode: 'direct', modeChosen: false });
        expect(direct.actions.ensureHostedVersionAllowed).not.toHaveBeenCalled();
        direct.dom.window.close();

        const relay = setup();
        await relay.controller.maybeRunFirstLaunchFlow();
        expect(relay.getServer()).toMatchObject({ mode: 'relay', modeChosen: true });
        expect(relay.actions.ensureHostedVersionAllowed).toHaveBeenCalledWith({ candidateMode: 'relay' });
        relay.dom.window.close();
    });
});

describe('HostedMode transitions', () => {
    it('returns to the chooser and resumes startup effects in order', async () => {
        const page = setup({ state: { connectionMode: 'relay' }, server: { mode: 'relay', token: 'token' } });
        const operation = page.controller.returnToModeChooser();
        page.document.getElementById('direct').click();
        await operation;
        expect(page.events.map(([name]) => name)).toEqual([
            'resetBootGuard',
            'hideSettingsPanel',
            'hideLogin',
            'ensureHostedVersionAllowed',
            'syncProviderFromStorage',
            'maybeForceOpenSettings',
            'updateBalanceBarVisibility',
        ]);
        page.dom.window.close();
    });

    it('switches to own-key mode and opens forced settings', async () => {
        const page = setup({ state: { connectionMode: 'relay' }, server: { mode: 'relay' } });
        await page.controller.switchToOwnKeyMode();
        expect(page.getServer()).toMatchObject({ mode: 'direct', modeChosen: true });
        expect(page.events.map(([name]) => name)).toEqual([
            'resetBootGuard',
            'hideLogin',
            'setModeRadio',
            'applyModeSectionsVisibility',
            'syncProviderFromStorage',
            'updateAccountSection',
            'updateBalanceBarVisibility',
            'openSettings',
        ]);
        expect(page.actions.openSettings).toHaveBeenCalledWith({ forced: true });
        page.dom.window.close();
    });

    it('ignores transitions while locked', async () => {
        const page = setup({ state: { lockManualControls: true } });
        await page.controller.returnToModeChooser();
        await page.controller.switchToOwnKeyMode();
        expect(page.events).toEqual([]);
        expect(page.saveServerSettings).not.toHaveBeenCalled();
        page.dom.window.close();
    });
});

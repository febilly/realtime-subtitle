const { JSDOM } = require('jsdom');
const HostedLogin = require('../../static/js/hosted-login');

function response(data, { ok = true, status = 200 } = {}) {
    return { ok, status, json: vi.fn().mockResolvedValue(data) };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="loginOverlay" hidden></div><section id="loginPanel" hidden>
            <h2 id="loginTitle"></h2><span id="loginServerHint"></span><span id="loginCodeHint"></span>
            <div id="loginStepInput"><label id="loginUserInputLabel"></label></div>
            <div id="loginStepMethod"></div><div id="loginStepChallenge"></div>
            <form id="loginForm"><input id="loginUserInput"><button id="loginPrimaryButton"></button></form>
            <button id="loginModeBackButton"></button><button id="loginBackButton"></button>
            <button id="loginPasteButton"></button><button id="loginCodeLink"></button>
            <button id="loginManualToggle"></button><div id="loginManualField"></div>
            <div id="loginWaitingHint"></div><div id="loginError"></div>
            <button id="loginCloseButton"></button>
            <section id="loginBonusSection"><span id="loginThresholdHint"></span></section>
        </section>
    </body>`, { url: 'http://localhost:8000/' });
    const document = dom.window.document;
    const runtime = {
        lockManualControls: false,
        relayAvailable: true,
        relayServerUrl: 'https://relay.example/',
        translationProvider: 'soniox',
        ...overrides.runtime,
    };
    let server = { mode: null, modeChosen: false, token: '', displayName: '', trustRank: '' };
    let clock = overrides.now ?? 1000;
    let timerId = 1;
    const timers = new Map();
    const setTimeout = vi.fn((callback, ms) => {
        const id = timerId++;
        timers.set(id, { callback, ms });
        return id;
    });
    const clearTimeout = vi.fn((id) => timers.delete(id));
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response({}));
    const calls = [];
    const actionNames = [
        'showToast', 'updateBalanceBarVisibility', 'fetchBalance', 'clearSubtitleState',
        'setTranslationModeSynced', 'pushSetup', 'switchToOwnKeyMode',
    ];
    const actions = Object.fromEntries(actionNames.map((name) => [
        name,
        vi.fn(async (...args) => { calls.push([name, ...args]); return { ok: true }; }),
    ]));
    Object.assign(actions, overrides.actions);
    const clipboard = {
        readText: vi.fn().mockResolvedValue(' pasted-code '),
        writeText: vi.fn().mockResolvedValue(undefined),
    };
    const openWindow = vi.fn();
    dom.window.open = openWindow;
    dom.window.I18N = { lang: 'en' };
    const saveServerSettings = vi.fn((value) => { server = { ...value }; });
    const controller = HostedLogin.create({
        document,
        window: dom.window,
        navigator: { clipboard },
        fetch,
        t: (key, vars) => vars ? `${key}:${vars.name || vars.rank || vars.url || ''}` : key,
        localizeBackendMessage: (message) => `localized:${message}`,
        getRuntimeState: () => runtime,
        loadServerSettings: () => ({ ...server }),
        saveServerSettings,
        loadProviderSettings: () => overrides.providerSettings || {},
        setTimeout,
        clearTimeout,
        now: () => clock,
        actions,
        elements: {
            overlay: document.getElementById('loginOverlay'),
            panel: document.getElementById('loginPanel'),
            form: document.getElementById('loginForm'),
            closeButton: document.getElementById('loginCloseButton'),
            userInput: document.getElementById('loginUserInput'),
            primaryButton: document.getElementById('loginPrimaryButton'),
            modeBackButton: document.getElementById('loginModeBackButton'),
            backButton: document.getElementById('loginBackButton'),
            pasteButton: document.getElementById('loginPasteButton'),
            codeLink: document.getElementById('loginCodeLink'),
            errorElement: document.getElementById('loginError'),
            manualToggle: document.getElementById('loginManualToggle'),
        },
    });
    return {
        actions,
        advance(ms) { clock += ms; },
        calls,
        clearTimeout,
        clipboard,
        controller,
        document,
        dom,
        fetch,
        getServer: () => server,
        openWindow,
        runtime,
        saveServerSettings,
        setTimeout,
        timers,
    };
}

describe('HostedLogin presentation and input', () => {
    it('opens forced, localizes dynamic server text, and blocks close', () => {
        const page = setup();
        page.controller.open({ forced: true });
        expect(page.document.getElementById('loginPanel').hidden).toBe(false);
        expect(page.document.getElementById('loginCloseButton').style.display).toBe('none');
        expect(page.document.getElementById('loginServerHint').textContent)
            .toBe('login_server:https://relay.example/');
        page.controller.close();
        expect(page.document.getElementById('loginPanel').hidden).toBe(false);
        page.controller.hide();
        expect(page.document.getElementById('loginPanel').hidden).toBe(true);
        page.dom.window.close();
    });

    it('does not open while controls are locked', () => {
        const page = setup({ runtime: { lockManualControls: true } });
        page.controller.open();
        expect(page.document.getElementById('loginPanel').hidden).toBe(true);
        page.dom.window.close();
    });

    it('binds manual input and clipboard interactions once', async () => {
        const page = setup();
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        page.controller.open();
        page.document.getElementById('loginManualToggle').click();
        expect(page.document.getElementById('loginManualField').hidden).toBe(false);
        page.document.getElementById('loginPasteButton').click();
        await Promise.resolve();
        expect(page.document.getElementById('loginUserInput').value).toBe('pasted-code');
        expect(page.document.getElementById('loginPrimaryButton').disabled).toBe(false);
        page.controller.destroy();
        page.dom.window.close();
    });

    it('renders registration threshold and localized rank labels', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ registration_threshold: 'known_user' }));
        const page = setup({ fetch });
        expect(await page.controller.fetchRegistrationInfo()).toBe(true);
        expect(page.document.getElementById('loginThresholdHint').textContent)
            .toBe('login_threshold:Known User');
        expect(page.controller.rankLabel('trusted_user')).toBe('Trusted User');
        page.dom.window.close();
    });
});

describe('HostedLogin browser callback polling', () => {
    it('opens a callback URL and schedules polling for the returned state', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ state: 'state-1' }));
        const page = setup({ fetch });
        await page.controller.startHostedLogin();
        const opened = page.openWindow.mock.calls[0][0];
        expect(opened).toContain('https://relay.example/app/#/login?next=%2Flogin-code');
        expect(opened).toContain('client_callback=http%3A%2F%2Flocalhost%3A8000%2Faccount%2Flogin-callback');
        expect(opened).toContain('state=state-1');
        expect(page.controller.getDebugState()).toMatchObject({
            waitingForBrowser: true,
            pollState: 'state-1',
            pollDeadline: 301000,
        });
        expect(page.setTimeout).toHaveBeenCalledWith(expect.any(Function), HostedLogin.POLL_INTERVAL_MS);
        page.dom.window.close();
    });

    it('shows an error toast when no relay server is configured', async () => {
        const page = setup({ runtime: { relayServerUrl: '' } });
        await page.controller.startHostedLogin();
        expect(page.actions.showToast).toHaveBeenCalledWith('server_not_configured', true);
        expect(page.fetch).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('reschedules pending/transient polls and stops at the deadline', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ state: 'state-1' }))
            .mockResolvedValueOnce(response({ status: 'pending' }))
            .mockRejectedValueOnce(new Error('offline'));
        const page = setup({ fetch });
        await page.controller.startHostedLogin();
        await page.controller.pollLoginCallback();
        expect(page.setTimeout).toHaveBeenCalledTimes(2);
        await page.controller.pollLoginCallback();
        expect(page.setTimeout).toHaveBeenCalledTimes(3);
        page.advance(HostedLogin.POLL_TIMEOUT_MS + 1);
        await page.controller.pollLoginCallback();
        expect(page.controller.getDebugState().pollState).toBeNull();
        expect(page.controller.getDebugState().waitingForBrowser).toBe(false);
        page.dom.window.close();
    });

    it('maps terminal poll errors and rate limits', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ state: 'state-1' }))
            .mockResolvedValueOnce(response({ status: 'error', message: 'slow down' }, { ok: false, status: 429 }));
        const page = setup({ fetch });
        await page.controller.startHostedLogin();
        await page.controller.pollLoginCallback();
        expect(page.document.getElementById('loginError').textContent).toBe('login_rate_limited');
        expect(page.controller.getDebugState().pollState).toBeNull();
        page.dom.window.close();
    });

    it('completes a successful poll through shared login success', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ state: 'state-1' }))
            .mockResolvedValueOnce(response({
                status: 'done', api_key: 'token', display_name: 'User', trust_rank: 'user',
            }));
        const page = setup({ fetch, providerSettings: { providerOverride: 'gemini' } });
        await page.controller.startHostedLogin();
        await page.controller.pollLoginCallback();
        expect(page.getServer()).toMatchObject({
            mode: 'relay', modeChosen: true, token: 'token', displayName: 'User', trustRank: 'user',
        });
        expect(page.actions.pushSetup).toHaveBeenCalledWith('gemini', null, {
            silent: true, mode: 'relay', token: 'token',
        });
        expect(page.controller.getDebugState().waitingForBrowser).toBe(false);
        page.dom.window.close();
    });

    it('ignores an obsolete poll response after the login panel closes', async () => {
        let resolvePoll;
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ state: 'state-1' }))
            .mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve; }));
        const page = setup({ fetch });
        await page.controller.startHostedLogin();

        const poll = page.controller.pollLoginCallback();
        page.controller.hide();
        resolvePoll(response({
            status: 'done', api_key: 'obsolete-token', display_name: 'Old User', trust_rank: 'user',
        }));
        await poll;

        expect(page.saveServerSettings).not.toHaveBeenCalled();
        expect(page.actions.pushSetup).not.toHaveBeenCalled();
        expect(page.controller.getDebugState().pollState).toBeNull();
        page.dom.window.close();
    });
});

describe('HostedLogin manual verification', () => {
    it('posts a one-time code and runs the same success effects', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            success: true, api_key: 'token', display_name: 'User', trust_rank: 'known_user',
        }));
        const page = setup({ fetch });
        await expect(page.controller.tryLoginCode('abc')).resolves.toBe('success');
        expect(fetch).toHaveBeenCalledWith('/account/login-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: 'abc' }),
        });
        expect(page.actions.setTranslationModeSynced).toHaveBeenCalledWith(false);
        expect(page.actions.updateBalanceBarVisibility).toHaveBeenCalledOnce();
        expect(page.actions.clearSubtitleState).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('localizes verification errors and validates empty input', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ message: 'bad code' }, { ok: false, status: 400 }));
        const page = setup({ fetch });
        await expect(page.controller.tryLoginCode('bad')).resolves.toBe('error');
        expect(page.document.getElementById('loginError').textContent).toBe('localized:bad code');
        await page.controller.handleLoginInput();
        expect(page.document.getElementById('loginError').textContent).toBe('login_code_required');
        page.dom.window.close();
    });
});

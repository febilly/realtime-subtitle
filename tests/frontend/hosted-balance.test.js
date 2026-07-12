const { JSDOM } = require('jsdom');
const Hosted = require('../../static/js/hosted');
const HostedBalance = require('../../static/js/hosted-balance');

function response(data, { ok = true } = {}) {
    return { ok, json: vi.fn().mockResolvedValue(data) };
}

function balance(overrides = {}) {
    return {
        prepaid_balance: 100,
        price_per_second: 1,
        free: { pools: [] },
        subscriptions: [],
        ...overrides,
    };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="balanceBar" hidden>
            <span id="balanceLabel"></span><span id="balanceValue"></span>
            <span id="sessionLabel"></span><span id="sessionValue"></span>
            <div id="freePools"></div>
            <div id="subItem" hidden><span id="subLabel"></span><span id="subValue"></span></div>
            <div id="balanceActionItem" hidden></div>
            <button id="balanceOpenSettingsButton"></button>
        </div>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const runtime = {
        connectionMode: 'relay',
        backendLoggedIn: true,
        hasToken: false,
        translationProvider: 'soniox',
        uiTranslationMode: 'one_way',
        translationUiMode: 'hybrid',
        sonioxNoTranslationFactor: 0.5,
        ...overrides.runtime,
    };
    let clock = overrides.now ?? 1000;
    let nextTimer = 1;
    const timers = new Map();
    const setInterval = vi.fn((callback, ms) => {
        const id = nextTimer++;
        timers.set(id, { callback, ms });
        return id;
    });
    const clearInterval = vi.fn((id) => timers.delete(id));
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response(balance()));
    const onAccountSectionChanged = vi.fn();
    const onAccountBalanceChanged = vi.fn();
    const onOpenSettings = vi.fn();
    const controller = HostedBalance.create({
        Billing: Hosted.Billing,
        document,
        fetch,
        t: (key, vars) => vars
            ? `${key}:${vars.remaining ?? ''}/${vars.cap ?? ''}`
            : key,
        now: () => clock,
        setInterval,
        clearInterval,
        getRuntimeState: () => runtime,
        onAccountSectionChanged,
        onAccountBalanceChanged,
        onOpenSettings,
        elements: {
            balanceBar: document.getElementById('balanceBar'),
            balanceActionItem: document.getElementById('balanceActionItem'),
            balanceOpenSettingsButton: document.getElementById('balanceOpenSettingsButton'),
        },
    });
    return {
        advance(ms) { clock += ms; },
        clearInterval,
        controller,
        document,
        dom,
        fetch,
        onAccountBalanceChanged,
        onAccountSectionChanged,
        onOpenSettings,
        runtime,
        setInterval,
        timers,
    };
}

describe('HostedBalance polling and fetching', () => {
    it('owns the settings-button listener across an idempotent lifecycle', () => {
        const page = setup();
        const button = page.document.getElementById('balanceOpenSettingsButton');

        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        button.click();
        expect(page.onOpenSettings).toHaveBeenCalledOnce();
        expect(page.onOpenSettings).toHaveBeenCalledWith({ forced: false });

        page.controller.destroy();
        button.click();
        expect(page.onOpenSettings).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('shows only signed-in relay balance and switches idle/active cadence', async () => {
        const page = setup();
        page.controller.updateBalanceBarVisibility();
        await Promise.resolve();
        expect(page.document.getElementById('balanceBar').hidden).toBe(false);
        expect(page.fetch).toHaveBeenCalledWith('/account/balance');
        expect(page.controller.getDebugState().balancePollIntervalMs).toBe(HostedBalance.IDLE_POLL_MS);

        page.controller.sessionCostResume();
        expect(page.controller.getDebugState().balanceIsMetering).toBe(true);
        expect(page.controller.getDebugState().balancePollIntervalMs).toBe(HostedBalance.ACTIVE_POLL_MS);
        expect([...page.timers.values()].some(({ ms }) => ms === 1000)).toBe(true);

        page.controller.sessionCostPause();
        expect(page.controller.getDebugState().balanceIsMetering).toBe(false);
        expect(page.controller.getDebugState().balancePollIntervalMs).toBe(HostedBalance.IDLE_POLL_MS);
        page.dom.window.close();
    });

    it('stops polling when mode or authentication no longer qualifies', () => {
        const page = setup();
        page.controller.updateBalanceBarVisibility();
        page.runtime.connectionMode = 'direct';
        page.controller.updateBalanceBarVisibility();
        expect(page.document.getElementById('balanceBar').hidden).toBe(true);
        expect(page.controller.getDebugState().balancePollIntervalMs).toBe(0);

        page.runtime.connectionMode = 'relay';
        page.runtime.backendLoggedIn = false;
        expect(page.controller.balanceBarShouldShow()).toBe(false);
        page.runtime.hasToken = true;
        expect(page.controller.balanceBarShouldShow()).toBe(true);
        page.dom.window.close();
    });

    it('supports forced provider previews and updates bonus metadata', async () => {
        const fetch = vi.fn().mockResolvedValue(response(balance({
            prepaid_balance: 25,
            first_redeem_bonus_credits: 50,
            first_redeem_bonus_eligible: true,
        })));
        const page = setup({ fetch, runtime: { connectionMode: 'direct', backendLoggedIn: false } });
        expect(await page.controller.fetchBalance({ provider: 'gemini', force: true })).toBe(true);
        expect(fetch).toHaveBeenCalledWith('/account/balance?provider=gemini');
        expect(page.controller.getFirstRedeemBonus()).toEqual({ credits: 50, eligible: true });
        expect(page.onAccountSectionChanged).toHaveBeenCalledOnce();
        expect(page.document.getElementById('balanceValue').textContent).toBe('25');
        page.dom.window.close();
    });

    it('ignores non-forced hidden fetches and transient failures', async () => {
        const fetch = vi.fn().mockRejectedValue(new Error('offline'));
        const page = setup({ fetch, runtime: { connectionMode: 'direct', backendLoggedIn: false } });
        await expect(page.controller.fetchBalance()).resolves.toBe(false);
        expect(fetch).not.toHaveBeenCalled();
        await expect(page.controller.fetchBalance({ force: true })).resolves.toBe(false);
        expect(fetch).toHaveBeenCalledOnce();
        page.dom.window.close();
    });
});

describe('HostedBalance rendering and metering', () => {
    it('renders free pools, subscriptions, and exhausted action state', () => {
        const page = setup();
        page.controller.renderBalance(balance({
            prepaid_balance: 0,
            free: {
                pools: [
                    { period: 'daily', remaining: 2, max_credits: 10 },
                    { period: 'weekly', unlimited: true, max_credits: 0 },
                ],
            },
            subscriptions: [{ remaining_credits: 4, quota_credits: 20 }],
        }));
        expect(page.document.querySelectorAll('#freePools .balance-item')).toHaveLength(2);
        expect(page.document.getElementById('subItem').hidden).toBe(false);
        expect(page.document.getElementById('balanceActionItem').hidden).toBe(true);

        page.controller.renderBalance(balance({ prepaid_balance: 0 }));
        expect(page.document.getElementById('balanceActionItem').hidden).toBe(false);
        page.dom.window.close();
    });

    it('keeps the old baseline for a mid-session server drop and reanchors on top-up', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response(balance({ prepaid_balance: 100, price_per_second: 1 })))
            .mockResolvedValueOnce(response(balance({ prepaid_balance: 98, price_per_second: 1 })))
            .mockResolvedValueOnce(response(balance({ prepaid_balance: 120, price_per_second: 1 })));
        const page = setup({ fetch });
        await page.controller.fetchBalance({ force: true });
        page.controller.sessionCostResume();
        page.advance(2000);
        await page.controller.fetchBalance({ force: true });
        expect(page.controller.getDebugState().balanceBaseline.prepaid_balance).toBe(100);
        expect(page.controller.currentBalanceView().prepaid_balance).toBe(98);

        await page.controller.fetchBalance({ force: true });
        expect(page.controller.getDebugState().balanceBaseline.prepaid_balance).toBe(120);
        page.dom.window.close();
    });

    it('applies the Soniox STT-only factor and displays an LLM breakdown', async () => {
        const page = setup({
            fetch: vi.fn().mockResolvedValue(response(balance({
                prepaid_balance: 100,
                price_per_second: 2,
            }))),
            runtime: { uiTranslationMode: 'none', sonioxNoTranslationFactor: 0.5 },
        });
        await page.controller.fetchBalance({ force: true });
        page.controller.sessionCostResume();
        page.advance(2000);
        expect(page.controller.addLlmCost(1.25)).toBe(true);
        expect(page.controller.currentBalanceView().prepaid_balance).toBe(96.75);
        expect(page.document.getElementById('sessionValue').textContent).toContain('LLM 1.25');
        expect(page.controller.addLlmCost(-1)).toBe(false);
        page.dom.window.close();
    });

    it('resets elapsed/LLM cost, reanchors, and refreshes the server value', async () => {
        const page = setup();
        page.controller.renderBalance(balance({ prepaid_balance: 50 }));
        page.controller.sessionCostResume();
        page.advance(1500);
        page.controller.addLlmCost(2);
        const before = page.fetch.mock.calls.length;
        page.controller.sessionCostReset();
        await Promise.resolve();

        expect(page.controller.getDebugState()).toMatchObject({
            sessionAccumMs: 0,
            sessionLlmCost: 0,
            sessionHadLlmCost: false,
            balanceIsMetering: false,
        });
        expect(page.fetch.mock.calls.length).toBeGreaterThan(before);
        page.dom.window.close();
    });

    it('does not start a session meter outside relay mode and destroys timers', () => {
        const page = setup({ runtime: { connectionMode: 'direct' } });
        page.controller.sessionCostResume();
        expect(page.controller.getDebugState().balanceIsMetering).toBe(false);

        page.runtime.connectionMode = 'relay';
        page.controller.updateBalanceBarVisibility();
        page.controller.sessionCostResume();
        page.controller.destroy();
        expect(page.timers.size).toBe(0);
        page.dom.window.close();
    });
});

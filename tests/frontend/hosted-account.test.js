const { JSDOM } = require('jsdom');
const HostedAccount = require('../../static/js/hosted-account');

function response(data, { ok = true, status = 200 } = {}) {
    return { ok, status, json: vi.fn().mockResolvedValue(data) };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <span id="accountServerHint"></span><span id="accountIdentityHint"></span>
        <p id="purchaseCreditsHint" hidden><a id="purchaseCreditsLink"></a></p>
        <span id="firstRedeemBonusHint" hidden></span>
        <span id="accountBalanceHint" hidden></span><div id="accountFreePools"></div>
        <input id="redeemInput"><button id="redeemButton"></button>
        <button id="redeemPasteButton"></button><button id="reLoginButton"></button>
        <button id="logoutButton"></button><button id="copyInviteButton"></button>
        <button id="openUserWebButton"></button>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const runtime = {
        backendLoggedIn: true,
        relayServerUrl: 'https://relay.example',
        creditsPurchaseUrl: 'https://shop.example/buy',
        ...overrides.runtime,
    };
    let server = {
        token: 'relay-token',
        displayName: 'Test User',
        trustRank: 'known_user',
        ...overrides.server,
    };
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response({}));
    const clipboard = {
        readText: vi.fn().mockResolvedValue(' pasted-code '),
        writeText: vi.fn().mockResolvedValue(undefined),
        ...overrides.clipboard,
    };
    const openWindow = vi.fn();
    dom.window.open = openWindow;
    const storage = overrides.storage || dom.window.localStorage;
    const balance = {
        formatCredits: vi.fn((value) => `C${value}`),
        getFirstRedeemBonus: vi.fn(() => ({ eligible: true, credits: 25 })),
        currentBalanceView: vi.fn(() => ({
            prepaid_balance: 120,
            free: { pools: [{ period: 'daily', remaining: 5 }] },
        })),
        renderFreePools: vi.fn((container, pools) => {
            container.textContent = Array.isArray(pools) ? `pools:${pools.length}` : '';
        }),
        resetFirstRedeemBonus: vi.fn(),
        fetchBalance: vi.fn().mockResolvedValue(true),
        updateBalanceBarVisibility: vi.fn(),
        ...overrides.balance,
    };
    const actions = {
        showToast: vi.fn(),
        setBackendLoggedIn: vi.fn((value) => { runtime.backendLoggedIn = value; }),
        resetBootGuard: vi.fn(),
        hideSettingsPanel: vi.fn(),
        openLogin: vi.fn(),
        openSettings: vi.fn(),
        ...overrides.actions,
    };
    const showConfirm = overrides.showConfirm || vi.fn().mockResolvedValue(true);
    const saveServerSettings = vi.fn((value) => { server = { ...value }; });
    const controller = HostedAccount.create({
        document,
        window: dom.window,
        navigator: { clipboard },
        storage,
        now: overrides.now,
        signedInAtLaunch: !!overrides.signedInAtLaunch,
        fetch,
        t: (key, vars = {}) => [key, ...Object.entries(vars).map(([name, value]) => `${name}=${value}`)].join('|'),
        localizeBackendMessage: (message) => `localized:${message}`,
        showConfirm,
        rankLabel: (rank) => `rank:${rank}`,
        getRuntimeState: () => runtime,
        loadServerSettings: () => ({ ...server }),
        saveServerSettings,
        balance,
        actions,
        elements: {
            serverHint: document.getElementById('accountServerHint'),
            identityHint: document.getElementById('accountIdentityHint'),
            purchaseHint: document.getElementById('purchaseCreditsHint'),
            purchaseLink: document.getElementById('purchaseCreditsLink'),
            firstBonusHint: document.getElementById('firstRedeemBonusHint'),
            balanceHint: document.getElementById('accountBalanceHint'),
            freePools: document.getElementById('accountFreePools'),
            redeemButton: document.getElementById('redeemButton'),
            redeemInput: document.getElementById('redeemInput'),
            redeemPasteButton: document.getElementById('redeemPasteButton'),
            reLoginButton: document.getElementById('reLoginButton'),
            logoutButton: document.getElementById('logoutButton'),
            copyInviteButton: document.getElementById('copyInviteButton'),
            openUserWebButton: document.getElementById('openUserWebButton'),
        },
    });
    return {
        actions,
        balance,
        clipboard,
        controller,
        document,
        dom,
        fetch,
        getServer: () => server,
        openWindow,
        runtime,
        storage,
        saveServerSettings,
        showConfirm,
    };
}

describe('HostedAccount invite reward reminder', () => {
    it('shows only for a user who was already signed in at launch and has no successful invites', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            invite_link: 'https://invite.example/code',
            invited_users_count: 0,
        }));
        const page = setup({ fetch, signedInAtLaunch: true, now: () => 1_000_000_000 });

        await expect(page.controller.maybeShowInviteReminder()).resolves.toBe(true);
        expect(page.actions.showToast).toHaveBeenCalledWith(
            'invite_reward_reminder',
            false,
            expect.objectContaining({ timeoutMs: 12000, actionLabel: 'open_settings' }),
        );
        expect(page.storage.getItem(HostedAccount.INVITE_REMINDER_STORAGE_KEY)).toBe('1000000000');
        page.actions.showToast.mock.calls[0][2].onAction();
        expect(page.actions.openSettings).toHaveBeenCalledWith({ forced: false });
        page.dom.window.close();
    });

    it('does not remind on the login launch, during cooldown, or after a successful invite', async () => {
        const notSignedInAtLaunch = setup({ signedInAtLaunch: false });
        await expect(notSignedInAtLaunch.controller.maybeShowInviteReminder()).resolves.toBe(false);
        expect(notSignedInAtLaunch.fetch).not.toHaveBeenCalled();
        notSignedInAtLaunch.dom.window.close();

        const coolingDown = setup({ signedInAtLaunch: true, now: () => 2_000_000_000 });
        coolingDown.storage.setItem(
            HostedAccount.INVITE_REMINDER_STORAGE_KEY,
            String(2_000_000_000 - HostedAccount.INVITE_REMINDER_COOLDOWN_MS + 1),
        );
        await expect(coolingDown.controller.maybeShowInviteReminder()).resolves.toBe(false);
        expect(coolingDown.fetch).not.toHaveBeenCalled();
        coolingDown.dom.window.close();

        const invited = setup({
            signedInAtLaunch: true,
            fetch: vi.fn().mockResolvedValue(response({ successful_invites: 1 })),
        });
        await expect(invited.controller.maybeShowInviteReminder()).resolves.toBe(false);
        expect(invited.actions.showToast).not.toHaveBeenCalled();
        invited.dom.window.close();
    });
});

describe('HostedAccount presentation', () => {
    it('renders the signed-in identity, purchase link, bonus, balance, and pools', () => {
        const page = setup();
        page.controller.updateSection();

        expect(page.document.getElementById('accountServerHint').textContent)
            .toBe('account_server|url=https://relay.example');
        expect(page.document.getElementById('accountIdentityHint').textContent)
            .toBe('account_identity|name=Test User|rank=rank:known_user');
        expect(page.document.getElementById('purchaseCreditsHint').hidden).toBe(false);
        expect(page.document.getElementById('purchaseCreditsLink').href).toBe('https://shop.example/buy');
        expect(page.document.getElementById('firstRedeemBonusHint').textContent)
            .toBe('account_first_redeem_bonus|credits=C25');
        expect(page.document.getElementById('accountBalanceHint').textContent)
            .toBe('account_balance|balance=C120');
        expect(page.document.getElementById('accountFreePools').textContent).toBe('pools:1');
        page.dom.window.close();
    });

    it('hides private balance and bonus details while signed out', () => {
        const page = setup({
            runtime: { backendLoggedIn: false, creditsPurchaseUrl: '' },
            server: { token: '', displayName: '', trustRank: '' },
        });
        page.controller.updateSection();

        expect(page.document.getElementById('accountIdentityHint').textContent)
            .toBe('account_not_signed_in');
        expect(page.document.getElementById('purchaseCreditsHint').hidden).toBe(true);
        expect(page.document.getElementById('firstRedeemBonusHint').hidden).toBe(true);
        expect(page.document.getElementById('accountBalanceHint').hidden).toBe(true);
        expect(page.balance.renderFreePools).toHaveBeenCalledWith(
            page.document.getElementById('accountFreePools'),
            null,
        );
        page.dom.window.close();
    });
});

describe('HostedAccount actions', () => {
    it('redeems a code, clears the input, refreshes bonus state, and fetches balance', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            success: true,
            granted_credits: 10,
            new_balance: 130,
            first_redeem_bonus_credits: 5,
        }));
        const page = setup({ fetch });
        page.document.getElementById('redeemInput').value = '  gift-code  ';

        await page.controller.handleRedeem();

        expect(fetch).toHaveBeenCalledWith('/account/redeem', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: 'gift-code' }),
        });
        expect(page.document.getElementById('redeemInput').value).toBe('');
        expect(page.actions.showToast).toHaveBeenCalledWith(
            'account_redeem_success|credits=C10|balance=C130',
        );
        expect(page.balance.resetFirstRedeemBonus).toHaveBeenCalledWith(5);
        expect(page.balance.fetchBalance).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('localizes redeem and invite API failures', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ detail: 'bad gift' }, { ok: false, status: 400 }))
            .mockResolvedValueOnce(response({ message: 'no invite' }, { ok: false, status: 403 }));
        const page = setup({ fetch });
        page.document.getElementById('redeemInput').value = 'bad';

        await page.controller.handleRedeem();
        await page.controller.handleCopyInvite();

        expect(page.actions.showToast).toHaveBeenNthCalledWith(1, 'localized:bad gift', true);
        expect(page.actions.showToast).toHaveBeenNthCalledWith(2, 'localized:no invite', true);
        expect(page.document.getElementById('copyInviteButton').disabled).toBe(false);
        page.dom.window.close();
    });

    it('copies an invite and falls back to showing the link when clipboard write fails', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ invite_link: 'https://invite.example/one' }))
            .mockResolvedValueOnce(response({ invite_link: 'https://invite.example/two' }));
        const page = setup({ fetch });

        await page.controller.handleCopyInvite();
        page.clipboard.writeText.mockRejectedValueOnce(new Error('denied'));
        await page.controller.handleCopyInvite();

        expect(page.clipboard.writeText).toHaveBeenNthCalledWith(1, 'https://invite.example/one');
        expect(page.actions.showToast).toHaveBeenNthCalledWith(1, 'account_invite_copied');
        expect(page.actions.showToast).toHaveBeenNthCalledWith(2, 'https://invite.example/two');
        page.dom.window.close();
    });

    it('opens the account URL externally and copies it when opening throws', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ url: 'https://account.example/one' }))
            .mockResolvedValueOnce(response({ url: 'https://account.example/two' }));
        const page = setup({ fetch });

        await page.controller.handleOpenUserWeb();
        page.openWindow.mockImplementationOnce(() => { throw new Error('blocked'); });
        await page.controller.handleOpenUserWeb();
        await Promise.resolve();

        expect(page.openWindow).toHaveBeenNthCalledWith(
            1,
            'https://account.example/one',
            '_blank',
            'noopener,noreferrer',
        );
        expect(page.clipboard.writeText).toHaveBeenCalledWith('https://account.example/two');
        expect(page.actions.showToast).toHaveBeenCalledWith('https://account.example/two');
        expect(page.document.getElementById('openUserWebButton').disabled).toBe(false);
        page.dom.window.close();
    });

    it('shows the same unread admin reply once per launch and opens the ticket page', async () => {
        const unread = {
            unread_ticket_count: 1,
            unread_activity_count: 1,
            admin_initiated_count: 0,
            admin_reply_count: 1,
            tickets: [{
                id: 'ticket_one',
                read_cursor: 'event_admin_1',
                unread_type: 'admin_reply',
                unread_count: 1,
            }],
        };
        const fetch = vi.fn()
            .mockResolvedValueOnce(response(unread))
            .mockResolvedValueOnce(response(unread))
            .mockResolvedValueOnce(response({ url: 'https://account.example/tickets' }));
        const page = setup({ fetch });

        await expect(page.controller.maybeShowTicketUnreadReminder()).resolves.toBe(true);
        await expect(page.controller.maybeShowTicketUnreadReminder()).resolves.toBe(false);
        expect(fetch).toHaveBeenNthCalledWith(1, '/account/tickets/unread-summary');
        expect(fetch).toHaveBeenNthCalledWith(2, '/account/tickets/unread-summary');
        expect(page.actions.showToast).toHaveBeenCalledWith(
            'ticket_reply_reminder',
            false,
            expect.objectContaining({ timeoutMs: 10000, actionLabel: 'open_tickets' }),
        );

        page.actions.showToast.mock.calls[0][2].onAction();
        await vi.waitFor(() => {
            expect(fetch).toHaveBeenNthCalledWith(3, '/account/web-login-url?next=%2Ftickets');
            expect(page.openWindow).toHaveBeenCalledWith(
                'https://account.example/tickets',
                '_blank',
                'noopener,noreferrer',
            );
        });
        expect(fetch).toHaveBeenCalledTimes(3);
        page.dom.window.close();
    });

    it('distinguishes an administrator-initiated ticket from a reply', async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            unread_ticket_count: 1,
            unread_activity_count: 1,
            admin_initiated_count: 1,
            admin_reply_count: 0,
            tickets: [{
                id: 'ticket_admin',
                read_cursor: 'event_admin_2',
                unread_type: 'admin_initiated',
                unread_count: 1,
            }],
        }));
        const page = setup({ fetch });

        await expect(page.controller.maybeShowTicketUnreadReminder()).resolves.toBe(true);
        expect(page.actions.showToast).toHaveBeenCalledWith(
            'ticket_admin_initiated_reminder',
            false,
            expect.objectContaining({ actionLabel: 'open_tickets' }),
        );
        page.dom.window.close();
    });

    it('notifies again when the server exposes a newer unread cursor', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({
                unread_activity_count: 1,
                admin_initiated_count: 0,
                admin_reply_count: 1,
                tickets: [{ id: 'ticket_one', read_cursor: 'event_one', unread_count: 1 }],
            }))
            .mockResolvedValueOnce(response({
                unread_activity_count: 2,
                admin_initiated_count: 1,
                admin_reply_count: 1,
                tickets: [{ id: 'ticket_one', read_cursor: 'event_two', unread_count: 2 }],
            }));
        const page = setup({ fetch });

        await expect(page.controller.maybeShowTicketUnreadReminder()).resolves.toBe(true);
        await expect(page.controller.maybeShowTicketUnreadReminder()).resolves.toBe(true);
        expect(page.actions.showToast).toHaveBeenNthCalledWith(
            2,
            'ticket_unread_mixed_reminder',
            false,
            expect.objectContaining({ actionLabel: 'open_tickets' }),
        );
        page.dom.window.close();
    });

    it('polls unread state every minute and stops polling when destroyed', async () => {
        vi.useFakeTimers();
        try {
            const fetch = vi.fn().mockResolvedValue(response({
                unread_ticket_count: 0,
                unread_activity_count: 0,
                admin_initiated_count: 0,
                admin_reply_count: 0,
                tickets: [],
            }));
            const page = setup({ fetch });

            await page.controller.startTicketUnreadPolling();
            expect(fetch).toHaveBeenCalledTimes(1);
            await vi.advanceTimersByTimeAsync(HostedAccount.TICKET_UNREAD_POLL_INTERVAL_MS);
            expect(fetch).toHaveBeenCalledTimes(2);

            page.controller.destroy();
            await vi.advanceTimersByTimeAsync(HostedAccount.TICKET_UNREAD_POLL_INTERVAL_MS);
            expect(fetch).toHaveBeenCalledTimes(2);
            page.dom.window.close();
        } finally {
            vi.useRealTimers();
        }
    });

    it('keeps credentials when logout is cancelled', async () => {
        const page = setup({ showConfirm: vi.fn().mockResolvedValue(false) });

        await expect(page.controller.handleLogout()).resolves.toBe(false);

        expect(page.fetch).not.toHaveBeenCalled();
        expect(page.saveServerSettings).not.toHaveBeenCalled();
        expect(page.getServer().token).toBe('relay-token');
        page.dom.window.close();
    });

    it('clears local credentials even when the logout request fails', async () => {
        const fetch = vi.fn().mockRejectedValue(new Error('offline'));
        const page = setup({ fetch });

        await expect(page.controller.handleLogout()).resolves.toBe(true);

        expect(page.getServer()).toMatchObject({ token: '', displayName: '', trustRank: '' });
        expect(page.actions.setBackendLoggedIn).toHaveBeenCalledWith(false);
        expect(page.actions.resetBootGuard).toHaveBeenCalledOnce();
        expect(page.balance.updateBalanceBarVisibility).toHaveBeenCalledOnce();
        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: true });
        page.dom.window.close();
    });

    it('binds paste and relogin controls exactly once', async () => {
        const page = setup();
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);

        page.document.getElementById('redeemPasteButton').click();
        await Promise.resolve();
        expect(page.document.getElementById('redeemInput').value).toBe('pasted-code');
        page.document.getElementById('reLoginButton').click();
        expect(page.actions.hideSettingsPanel).toHaveBeenCalledOnce();
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: false });

        page.controller.destroy();
        page.document.getElementById('reLoginButton').click();
        expect(page.actions.openLogin).toHaveBeenCalledOnce();
        page.dom.window.close();
    });
});

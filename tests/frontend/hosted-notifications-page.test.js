const {
    createPageHarness,
    defaultFetchResponse,
} = require('./helpers/page-harness');

const hostedConfig = {
    provider: 'soniox',
    mode: 'relay',
    logged_in: true,
    setup_required: false,
    relay_available: true,
    server_url: 'https://relay.example',
    llm_refine_available: true,
    translation_mode: 'one_way',
    translation_ui_mode: 'hybrid',
    languages: ['en', 'zh', 'ja'],
};

function storedHostedLogin() {
    return {
        'subtitleServer.v1': JSON.stringify({
            mode: 'relay',
            modeChosen: true,
            token: 'relay-token',
        }),
        'providerSettings.v1': JSON.stringify({
            providerOverride: 'soniox',
            keys: {},
        }),
    };
}

describe('hosted notification flows', () => {
    it('recovers a temporary-key session limit with 20 current-model Credits', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        provider: parsed.searchParams.get('provider') || 'soniox',
                        prepaid_balance: 0,
                        subscriptions: [{ remaining_credits: 15 }],
                        free: { pools: [{ remaining: 5 }] },
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: hostedConfig });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: storedHostedLogin(),
        });
        try {
            const before = page.fetchCalls.length;
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'api_key',
                reason: 'connection closed: Error: 403 - Temporary API key session duration limit exceeded. Create a new temporary API key to start a new session.',
            });
            await page.flush(8);

            const recoveryCalls = page.fetchCalls.slice(before);
            const balanceCall = recoveryCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/balance'
            ));
            expect(new URL(String(balanceCall[0]), 'http://localhost/').searchParams.get('provider'))
                .toBe('soniox');
            expect(recoveryCalls.some(([url, options]) => (
                new URL(String(url), 'http://localhost/').pathname === '/translation-mode'
                && options
                && JSON.parse(options.body).mode === 'fast'
            ))).toBe(true);
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('fast');

            const toast = page.document.getElementById('toast');
            expect(toast.textContent).toContain('Prepaid quota is insufficient');
            expect(toast.querySelector('.toast').classList.contains('error')).toBe(false);
            expect(page.document.getElementById('settingsOverlay').hidden).toBe(true);
            expect(page.document.getElementById('settingsModeBackButton').hidden).toBe(true);

            await new Promise((resolve) => setTimeout(resolve, 550));
            await page.flush(4);
            expect(page.fetchCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/restart'
            ))).toBe(true);
        } finally {
            page.close();
        }
    });

    it('reports insufficient current-model quota below 20 without opening settings', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        prepaid_balance: 9,
                        subscriptions: [{ remaining_credits: 5 }],
                        free: { pools: [{ remaining: 5.99 }] },
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: hostedConfig });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: storedHostedLogin(),
        });
        try {
            const before = page.fetchCalls.length;
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'api_key',
                reason: 'Error: 403 - Temporary API key session duration limit exceeded.',
            });
            await page.flush(8);

            const recoveryCalls = page.fetchCalls.slice(before);
            expect(recoveryCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/balance'
            ))).toBe(true);
            expect(recoveryCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/translation-mode'
            ))).toBe(false);
            expect(recoveryCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/restart'
            ))).toBe(false);
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('hybrid');

            const toast = page.document.getElementById('toast');
            expect(toast.textContent).toContain('speech model has insufficient quota');
            expect(toast.querySelector('.toast').classList.contains('error')).toBe(true);
            expect(page.document.getElementById('settingsOverlay').hidden).toBe(true);
            expect(page.document.getElementById('settingsModeBackButton').hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('offers usable Gemini free quota and switches provider plus translation mode', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                const free = parsed.searchParams.get('provider') === 'gemini'
                    ? { pools: [{ period: 'daily', remaining: 10, max_credits: 20 }] }
                    : { pools: [] };
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ prepaid_balance: 0, subscriptions: [], free }),
                };
            }
            if (parsed.pathname === '/setup') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        provider: 'gemini', mode: 'relay', logged_in: true,
                        setup_required: false, boot_id: 'gemini-boot',
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: hostedConfig });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: storedHostedLogin(),
        });
        try {
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
            });
            await page.flush(6);

            const toast = page.document.getElementById('toast');
            expect(toast.querySelector('.toast-title').textContent).toBe('Soniox quota used up');
            expect(toast.querySelector('.toast-body').textContent).toContain('Gemini still has quota');
            const actions = toast.querySelectorAll('.toast-actions .toast-action');
            expect(actions).toHaveLength(1);
            expect(actions[0].textContent).toBe('Switch to Gemini');
            actions[0].click();
            await page.flush(16);

            const setupCall = page.fetchCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
            ));
            expect(JSON.parse(setupCall[1].body)).toMatchObject({
                provider: 'gemini', mode: 'relay', token: 'relay-token',
            });
            const modeCalls = page.fetchCalls.filter(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/translation-mode'
            ));
            expect(modeCalls.some(([, options]) => JSON.parse(options.body).mode === 'fast')).toBe(true);
            expect(JSON.parse(page.window.localStorage.getItem('providerSettings.v1')).providerOverride)
                .toBe('gemini');
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('fast');
            await new Promise((resolve) => setTimeout(resolve, 550));
            await page.flush(4);
            expect(page.fetchCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/restart'
            ))).toBe(false);
        } finally {
            page.close();
        }
    });

    it('offers Gemini funded by a subscription pool rather than a free pool', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                // Gemini's free pool is spent too; only the subscription bought
                // for it still has quota left.
                const subscriptions = parsed.searchParams.get('provider') === 'gemini'
                    ? [{ period: 'monthly', remaining_credits: 900, quota_credits: 18000 }]
                    : [];
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        prepaid_balance: 0,
                        subscriptions,
                        free: { pools: [] },
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: hostedConfig });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: storedHostedLogin(),
        });
        try {
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
            });
            await page.flush(6);

            const toast = page.document.getElementById('toast');
            expect(toast.textContent).toContain('Switch to Gemini');
            expect(toast.textContent).not.toContain('Your credits and free quota are used up.');
        } finally {
            page.close();
        }
    });

    it('offers Credits, invite rewards, or Gemini when a purchase URL is configured', async () => {
        const config = {
            ...hostedConfig,
            credits_purchase_url: 'https://credits.example/buy',
        };
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                const free = parsed.searchParams.get('provider') === 'gemini'
                    ? { pools: [{ period: 'daily', remaining: 10, max_credits: 20 }] }
                    : { pools: [] };
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ prepaid_balance: 0, subscriptions: [], free }),
                };
            }
            if (parsed.pathname === '/account/web-login-url') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ url: 'https://account.example/invite' }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: config });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: config,
            uiConfig: config,
            localStorage: storedHostedLogin(),
        });
        try {
            page.window.open = vi.fn();
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
            });
            await page.flush(6);

            const toast = page.document.getElementById('toast');
            const actions = toast.querySelectorAll('.toast-action');
            expect([...actions].map((action) => action.textContent)).toEqual([
                'Switch to Gemini',
                'get more Credits',
                'invite friends for rewards',
            ]);
            expect(toast.querySelectorAll('.toast-actions .toast-action')).toHaveLength(3);
            expect(toast.querySelector('.toast-action-separator')).toBeNull();

            actions[2].click();
            await page.flush(4);
            expect(page.window.open).toHaveBeenCalledWith(
                'https://account.example/invite',
                '_blank',
                'noopener,noreferrer',
            );
            expect(page.fetchCalls.some(([url]) => {
                const parsed = new URL(String(url), 'http://localhost/');
                return parsed.pathname === '/account/web-login-url'
                    && parsed.searchParams.get('next') === '/invite';
            })).toBe(true);
            expect(page.fetchCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
            ))).toBe(false);
        } finally {
            page.close();
        }
    });

    it('tells a Gemini user when the daily free pool resets and offers unused Soniox quota', async () => {
        const config = { ...hostedConfig, provider: 'gemini', translation_ui_mode: 'fast' };
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                const free = parsed.searchParams.get('provider') === 'gemini'
                    ? { pools: [{ period: 'daily', used: 450, remaining: 0, max_credits: 450 }] }
                    : { pools: [{ period: 'weekly', used: 0, remaining: 600, max_credits: 600 }] };
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ prepaid_balance: 0, subscriptions: [], free }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: config });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: config,
            uiConfig: config,
            localStorage: storedHostedLogin(),
        });
        try {
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
            });
            await page.flush(6);

            const toast = page.document.getElementById('toast');
            expect(toast.querySelector('.toast-title').textContent)
                .toBe("Gemini's free quota for today is used up");
            const body = toast.querySelector('.toast-body').textContent;
            expect(body).toMatch(/It resets (today|tomorrow) at .+\. Come back then\./);
            expect(body).toContain('Soniox still has quota you can switch to.');
            expect([...toast.querySelectorAll('.toast-action')].map((action) => action.textContent))
                .toEqual(['Switch to Soniox']);
        } finally {
            page.close();
        }
    });

    it('shows the server Soniox reminder as a toast whose action switches to Soniox', async () => {
        const config = { ...hostedConfig, provider: 'gemini', translation_ui_mode: 'fast' };
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/setup') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        provider: 'soniox', mode: 'relay', logged_in: true,
                        setup_required: false, boot_id: 'soniox-boot',
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: config });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: config,
            uiConfig: config,
            localStorage: storedHostedLogin(),
        });
        try {
            await page.emitFrame({
                type: 'server_notice',
                notice: {
                    id: 'soniox_quota',
                    action: { type: 'switch_provider', provider: 'soniox' },
                    timeout_ms: 15000,
                },
            });
            await page.emitFrame({ type: 'server_notice', notice: { id: 'from_a_newer_server' } });
            await page.emitFrame({ type: 'server_notice', notice: { id: 'soniox_quota', period: 'daily' } });
            await page.flush(2);

            const toast = page.document.getElementById('toast');
            // A notice without a period (an older server) reads as weekly.
            const items = toast.querySelectorAll('.toast');
            expect(items).toHaveLength(2);
            const item = items[0];
            expect(item.textContent).toContain("You haven't used this week's free Soniox quota yet.");
            expect(items[1].textContent).toContain("You haven't used today's free Soniox quota yet.");
            expect(item.classList.contains('error')).toBe(false);
            const action = toast.querySelector('.toast-action');
            expect(action.textContent).toBe('Switch to Soniox');
            action.click();
            await page.flush(16);

            const setupCall = page.fetchCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
            ));
            expect(JSON.parse(setupCall[1].body)).toMatchObject({ provider: 'soniox', mode: 'relay' });
            expect(JSON.parse(page.window.localStorage.getItem('providerSettings.v1')).providerOverride)
                .toBe('soniox');
        } finally {
            page.close();
        }
    });

    it('offers Credits or invite rewards when every model quota is exhausted', async () => {
        const config = {
            ...hostedConfig,
            credits_purchase_url: 'https://credits.example/buy',
        };
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 1 }) };
            }
            if (parsed.pathname === '/account/balance') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        prepaid_balance: 0,
                        subscriptions: [],
                        free: { pools: [] },
                    }),
                };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: config });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: config,
            uiConfig: config,
            localStorage: storedHostedLogin(),
        });
        try {
            page.window.open = vi.fn();
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
            });
            await page.flush(6);

            const toast = page.document.getElementById('toast');
            expect(toast.querySelector('.toast-title').textContent).toBe('Out of quota');
            expect(toast.querySelector('.toast-body').textContent).toBe('Your credits and free quota are used up.');
            const actions = toast.querySelectorAll('.toast-action');
            expect([...actions].map((action) => action.textContent)).toEqual([
                'get more Credits',
                'invite friends for rewards',
            ]);

            actions[0].click();
            expect(page.window.open).toHaveBeenCalledWith(
                'https://credits.example/buy',
                '_blank',
                'noopener,noreferrer',
            );
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('shows the weekly invite reward reminder during startup for zero invites', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ successful_invite_count: 0 }) };
            }
            return defaultFetchResponse(url, { ...request, uiConfig: hostedConfig });
        };
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: storedHostedLogin(),
        });
        try {
            const toast = page.document.getElementById('toast');
            expect(toast.textContent).toContain('Invite a friend and you both receive rewards');
            expect(toast.textContent).toContain('only once per week');
            expect(page.window.localStorage.getItem('inviteRewardReminderLastShown')).not.toBeNull();
            const cadenceWrite = page.fetchCalls.find(([url, request]) => (
                new URL(String(url), 'http://localhost/').pathname === '/local-store'
                && request && request.method === 'POST'
                && String(request.body).includes('inviteRewardReminderLastShown')
            ));
            expect(cadenceWrite).toBeTruthy();
            toast.querySelector('.toast-close').click();
            expect(toast.hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});

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
            expect(toast.textContent).toContain('switch to Gemini');
            toast.querySelector('.toast-action').click();
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

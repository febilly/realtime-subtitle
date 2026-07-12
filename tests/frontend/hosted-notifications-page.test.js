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
        } finally {
            page.close();
        }
    });

    it('shows the weekly invite reward reminder during startup for zero invites', async () => {
        const fetch = async (url, request = {}) => {
            const parsed = new URL(String(url), 'http://localhost/');
            if (parsed.pathname === '/account/invite') {
                return { ok: true, status: 200, json: async () => ({ invited_users_count: 0 }) };
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
            expect(page.window.localStorage.getItem('inviteRewardReminderLastShown')).not.toBeNull();
            toast.querySelector('.toast-close').click();
            expect(toast.hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});

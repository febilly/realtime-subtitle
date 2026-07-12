const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted login wiring', () => {
    it('redeems a manual code, stores server credentials, and pushes relay setup', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            loginCodePayload: {
                success: true,
                api_key: 'relay-token',
                display_name: 'Test User',
                trust_rank: 'user',
            },
        });
        try {
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            page.document.getElementById('loginManualToggle').click();
            const input = page.document.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            page.document.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            const loginCall = page.fetchCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/login-code'
            ));
            expect(JSON.parse(loginCall[1].body)).toEqual({ code: 'one-time-code' });
            const setupCall = page.fetchCalls.find(([url, options]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
                && options && options.method === 'POST'
            ));
            expect(JSON.parse(setupCall[1].body)).toMatchObject({
                provider: 'soniox', mode: 'relay', token: 'relay-token',
            });

            const stored = JSON.parse(page.window.localStorage.getItem('subtitleServer.v1'));
            expect(stored).toMatchObject({ mode: 'relay', modeChosen: true });
            expect(stored.servers['https://relay.example']).toMatchObject({
                token: 'relay-token', displayName: 'Test User', trustRank: 'user',
            });
            expect(page.document.getElementById('loginPanel').hidden).toBe(true);
            expect(page.document.getElementById('balanceBar').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});

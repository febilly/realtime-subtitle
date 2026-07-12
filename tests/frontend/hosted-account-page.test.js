const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

describe('full-page hosted account wiring', () => {
    it('opens relay account settings and redeems a code through HostedAccount', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: true,
            credits_purchase_url: 'https://shop.example/buy',
        };
        const balancePayload = {
            prepaid_balance: 100,
            price_per_second: 2,
            free: { pools: [] },
            subscriptions: [],
            first_redeem_bonus_eligible: true,
            first_redeem_bonus_credits: 10,
        };
        const fetch = vi.fn(async (url) => {
            const pathname = new URL(String(url), 'http://localhost/').pathname;
            if (pathname === '/account/redeem') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        granted_credits: 15,
                        new_balance: 115,
                        first_redeem_bonus_credits: 5,
                    }),
                };
            }
            return defaultFetchResponse(url, { uiConfig: hostedConfig, balancePayload });
        });
        const page = await createPageHarness({
            fetch,
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay',
                    modeChosen: true,
                    token: 'relay-token',
                    displayName: 'Test User',
                    trustRank: 'user',
                }),
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
            expect(page.document.getElementById('accountSection').hidden).toBe(false);
            expect(page.document.getElementById('purchaseCreditsHint').hidden).toBe(false);

            const input = page.document.getElementById('redeemInput');
            input.value = ' gift-code ';
            page.document.getElementById('redeemButton').click();
            await page.flush(16);

            const redeemCall = page.fetchCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/redeem'
            ));
            expect(JSON.parse(redeemCall[1].body)).toEqual({ code: 'gift-code' });
            expect(input.value).toBe('');
            expect(page.document.getElementById('toast').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});

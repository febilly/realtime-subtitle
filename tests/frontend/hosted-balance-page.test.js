const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted balance wiring', () => {
    it('shows a signed-in relay balance and applies LLM cost frames', async () => {
        const page = await createPageHarness({
            uiConfig: {
                relay_available: true,
                server_url: 'https://relay.example',
                mode: 'relay',
                logged_in: true,
            },
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay',
                    modeChosen: true,
                    token: 'relay-token',
                }),
            },
            balancePayload: {
                prepaid_balance: 100,
                price_per_second: 2,
                free: { pools: [] },
                subscriptions: [],
            },
        });
        try {
            expect(page.document.getElementById('balanceBar').hidden).toBe(false);
            expect(page.document.getElementById('balanceValue').textContent).toBe('100');
            expect(page.fetchCalls.some(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/balance'
            ))).toBe(true);

            page.document.getElementById('balanceOpenSettingsButton').click();
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
            expect(page.document.getElementById('settingsOverlay').hidden).toBe(false);

            await page.emitFrame({ type: 'session_connected' });
            await page.emitFrame({ type: 'llm_cost', credits: 1.25 });
            expect(page.document.getElementById('balanceValue').textContent).toBe('98.75');
            expect(page.document.getElementById('sessionValue').textContent).toContain('LLM 1.25');

            await page.emitFrame({ type: 'recognition_paused', paused: true });
        } finally {
            page.close();
        }
    });
});

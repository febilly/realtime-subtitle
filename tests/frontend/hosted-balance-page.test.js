const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted balance wiring', () => {
    it('folds to the session line, keeps updating it, and expands after quota termination', async () => {
        const page = await createPageHarness({
            uiConfig: {
                relay_available: true,
                server_url: 'https://relay.example',
                mode: 'relay',
                logged_in: true,
            },
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay', modeChosen: true, token: 'relay-token',
                }),
            },
            balancePayload: {
                prepaid_balance: 100,
                price_per_second: 0.01,
                free: { pools: [{ period: 'weekly', remaining: 5, max_credits: 10 }] },
                subscriptions: [{ period: 'daily', remaining_credits: 3, quota_credits: 10 }],
            },
        });
        try {
            const bar = page.document.getElementById('balanceBar');
            const toggle = page.document.getElementById('balanceToggle');
            expect(bar.hidden).toBe(false);
            expect(bar.classList.contains('is-collapsed')).toBe(false);
            expect(toggle.getAttribute('aria-expanded')).toBe('true');
            expect(toggle.hidden).toBe(true);

            const items = [...bar.querySelectorAll('.balance-item')].filter((item) => !item.hidden);
            expect(items).toHaveLength(4);
            const setRowTops = (tops) => items.forEach((item, index) => {
                item.getBoundingClientRect = () => ({ top: tops[index], width: 80, height: 16 });
            });
            setRowTops([0, 0, 20, 20]);
            page.window.dispatchEvent(new page.window.Event('resize'));
            expect(toggle.hidden).toBe(true);

            setRowTops([0, 20, 40, 40]);
            page.window.dispatchEvent(new page.window.Event('resize'));
            expect(toggle.hidden).toBe(false);
            expect(bar.classList.contains('has-toggle')).toBe(true);

            setRowTops([0, 0, 20, 20]);
            page.window.dispatchEvent(new page.window.Event('resize'));
            expect(toggle.hidden).toBe(false);

            toggle.click();
            expect(toggle.getAttribute('aria-expanded')).toBe('false');
            expect(page.document.getElementById('sessionItem').textContent).toContain('This session');
            for (const item of bar.children) {
                if (item.id === 'sessionItem' || item.id === 'balanceToggle') continue;
                expect(item.hidden, item.id).toBe(true);
            }
            expect(page.document.getElementById('sessionItem').hidden).toBe(false);

            await page.emitFrame({ type: 'llm_cost', credits: 1.25 });
            expect(page.document.getElementById('sessionValue').textContent).toContain('LLM 1.25');
            expect(bar.classList.contains('is-collapsed')).toBe(true);

            await page.emitFrame({
                type: 'session_disconnected', code: 'billing_exhausted', relay_terminal: true,
            });
            expect(bar.classList.contains('is-collapsed')).toBe(false);
            expect(toggle.getAttribute('aria-expanded')).toBe('true');

            toggle.click();
            toggle.click();
            expect(bar.classList.contains('is-collapsed')).toBe(false);
        } finally {
            page.close();
        }
    });

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
            expect(page.document.getElementById('balanceToggle').hidden).toBe(true);
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

const { createPageHarness } = require('./helpers/page-harness');

describe('full-page reset-all controller wiring', () => {
    it('confirms, clears browser state, requests shutdown, and renders the browser fallback', async () => {
        const page = await createPageHarness();
        const realWindowClose = page.window.close.bind(page.window);
        const closeSpy = vi.fn();
        page.window.close = closeSpy;
        try {
            page.window.localStorage.setItem('reset-test', 'local');
            page.window.sessionStorage.setItem('reset-test', 'session');

            page.document.getElementById('settingsButton').click();
            page.document.getElementById('resetAllButton').click();
            expect(page.document.getElementById('confirmDialog').hidden).toBe(false);
            expect(page.document.getElementById('confirmMessage').textContent)
                .toContain('clear all settings');

            page.document.getElementById('confirmOkButton').click();
            await page.flush(12);

            expect(page.window.localStorage.getItem('reset-test')).toBeNull();
            expect(page.window.sessionStorage.getItem('reset-test')).toBeNull();
            expect(page.fetchCalls.some(([url, options]) => (
                new URL(String(url), 'http://localhost/').pathname === '/shutdown'
                && options.method === 'POST'
            ))).toBe(true);
            expect(closeSpy).toHaveBeenCalledOnce();
            expect(page.document.body.textContent)
                .toContain('Settings cleared. You can close this window.');
        } finally {
            page.window.close = realWindowClose;
            page.close();
        }
    });
});

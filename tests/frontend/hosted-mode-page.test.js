const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted mode wiring', () => {
    it('preopens first-launch login and switches to forced own-key settings', async () => {
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
        });
        try {
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            expect(page.document.getElementById('loginOverlay').hidden).toBe(false);
            expect(page.document.getElementById('loginModeBackButton').hidden).toBe(false);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'relay', modeChosen: true });

            page.document.getElementById('loginModeBackButton').click();
            await page.flush(12);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'direct', modeChosen: true });
            expect(page.document.getElementById('loginPanel').hidden).toBe(true);
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});

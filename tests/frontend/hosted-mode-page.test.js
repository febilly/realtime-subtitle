const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted mode wiring', () => {
    it('preopens first-launch login without an own-key switch button', async () => {
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
            expect(page.document.getElementById('loginModeBackButton')).toBeNull();
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'relay', modeChosen: true });
        } finally {
            page.close();
        }
    });
});

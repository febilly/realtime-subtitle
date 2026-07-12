const { createPageHarness } = require('./helpers/page-harness');

describe('full-page settings panel wiring', () => {
    it('discards unsaved relay mode and provider drafts after cancel and reopen', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'direct',
            logged_in: false,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'direct',
                    modeChosen: true,
                }),
                'providerSettings.v1': JSON.stringify({
                    providerOverride: 'soniox',
                    sonioxRegion: 'us',
                    keys: {},
                }),
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            const relay = page.document.querySelector('input[name="connmode"][value="relay"]');
            const gemini = page.document.querySelector('input[name="provider"][value="gemini"]');
            relay.checked = true;
            relay.dispatchEvent(new page.window.Event('change', { bubbles: true }));
            gemini.checked = true;
            gemini.dispatchEvent(new page.window.Event('change', { bubbles: true }));
            expect(page.document.getElementById('accountSection').hidden).toBe(false);
            expect(page.document.getElementById('apiKeySection').hidden).toBe(true);

            page.document.getElementById('settingsCancelButton').click();
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
            page.document.getElementById('settingsButton').click();

            expect(page.document.querySelector('input[name="connmode"][value="direct"]').checked).toBe(true);
            expect(page.document.querySelector('input[name="provider"][value="soniox"]').checked).toBe(true);
            expect(page.document.getElementById('accountSection').hidden).toBe(true);
            expect(page.document.getElementById('apiKeySection').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});

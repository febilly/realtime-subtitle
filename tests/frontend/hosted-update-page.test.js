const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted update wiring', () => {
    it('confirms a forced update escape and delegates the direct-mode transition', async () => {
        const page = await createPageHarness({
            uiConfig: {
                relay_available: true,
                server_url: 'https://relay.example',
                mode: 'relay',
                logged_in: true,
                client_version: '1.0.0',
                client_latest_version: '1.1.0',
                client_minimum_version: '1.1.0',
            },
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay',
                    modeChosen: true,
                    token: 'relay-token',
                }),
            },
        });
        try {
            expect(page.document.getElementById('clientUpdateDirectButton').hidden).toBe(false);
            page.document.getElementById('clientUpdateDirectButton').click();
            await page.flush(2);
            expect(page.document.getElementById('confirmDialog').hidden).toBe(false);

            page.document.getElementById('confirmOkButton').click();
            await page.flush(4);

            const server = JSON.parse(page.window.localStorage.getItem('subtitleServer.v1'));
            expect(server).toMatchObject({ mode: 'direct', modeChosen: true });
            expect(page.document.querySelector('input[name="connmode"][value="direct"]').checked)
                .toBe(true);
            expect(page.document.getElementById('accountSection').hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('shows and dismisses an optional relay update reminder', async () => {
        const page = await createPageHarness({
            uiConfig: {
                relay_available: true,
                server_url: 'https://relay.example',
                mode: 'relay',
                logged_in: true,
                client_version: '1.0.0',
                client_latest_version: '1.1.0',
                client_minimum_version: '0.9.0',
                client_update_url: 'https://example.com/update',
                client_update_notes: 'Release notes',
            },
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay',
                    modeChosen: true,
                    token: 'relay-token',
                }),
            },
        });
        try {
            expect(page.document.getElementById('clientUpdateOverlay').hidden).toBe(false);
            expect(page.document.getElementById('clientUpdateDialog').hidden).toBe(false);
            expect(page.document.getElementById('clientUpdateCurrent').textContent).toBe('1.0.0');
            expect(page.document.getElementById('clientUpdateLatest').textContent).toBe('1.1.0');
            expect(page.document.getElementById('clientUpdateNotes').textContent).toBe('Release notes');

            page.document.getElementById('clientUpdateLaterButton').click();
            await page.flush(4);
            expect(page.document.getElementById('clientUpdateDialog').hidden).toBe(true);
            expect(Number(page.window.localStorage.getItem('clientUpdateReminderLastShown')))
                .toBeGreaterThan(0);
        } finally {
            page.close();
        }
    });
});

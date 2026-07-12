const { createPageHarness } = require('./helpers/page-harness');

describe('full-page session frame controller wiring', () => {
    it('routes recognition pause state through runtime controls', async () => {
        const page = await createPageHarness();
        try {
            const button = page.document.getElementById('pauseButton');

            await page.emitFrame({ type: 'recognition_paused', paused: true });
            expect(button.classList.contains('is-paused')).toBe(true);
            expect(button.title).toBe('Resume recognition');
            expect(button.querySelector('use').getAttribute('href')).toContain('#play');

            await page.emitFrame({ type: 'recognition_paused', paused: false });
            expect(button.classList.contains('is-paused')).toBe(false);
            expect(button.title).toBe('Pause recognition');
            expect(button.querySelector('use').getAttribute('href')).toContain('#pause');
        } finally {
            page.close();
        }
    });

    it('clears an expired relay token and force-opens login on forbidden disconnect', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: true,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay',
                    modeChosen: true,
                    token: 'relay-token',
                }),
            },
        });
        try {
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'forbidden',
                relay_terminal: true,
                reason: 'expired login',
            });

            const stored = JSON.parse(page.window.localStorage.getItem('subtitleServer.v1'));
            expect(stored.servers['https://relay.example'].token).toBe('');
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            expect(page.document.getElementById('loginOverlay').hidden).toBe(false);
            expect(page.document.getElementById('toast').textContent)
                .toContain('Login expired or account not allowed. Please sign in again.');
        } finally {
            page.close();
        }
    });
});

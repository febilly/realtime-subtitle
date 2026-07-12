const { createPageHarness } = require('./helpers/page-harness');

describe('full-page app shell controller wiring', () => {
    it('localizes static shell text during startup', async () => {
        const page = await createPageHarness();
        try {
            expect(page.document.documentElement.lang).toBe('en');
            expect(page.document.getElementById('themeToggle').title).toBe('Toggle theme');
            expect(page.document.getElementById('restartButton').title).toBe('Restart recognition');
            expect(page.document.getElementById('translationLangButton').title)
                .toBe('Translation language');
            expect(page.document.getElementById('settingsButton').title).toBe('Settings');
            expect(page.document.querySelector('#subtitleContainer .empty-state').textContent)
                .toBe('Waiting for subtitles…');
        } finally {
            page.close();
        }
    });

    it('applies backend manual-control locking through the shell policy', async () => {
        const page = await createPageHarness({
            uiConfig: {
                lock_manual_controls: true,
                capabilities: {
                    segment_mode: false,
                    two_way_translation: true,
                },
            },
        });
        try {
            for (const id of [
                'restartButton',
                'pauseButton',
                'audioSourceButton',
                'oscTranslationButton',
                'translationLangButton',
            ]) {
                expect(page.document.getElementById(id).style.display).toBe('none');
            }
        } finally {
            page.close();
        }
    });

    it('routes IPC WebSocket frames to the shell connection indicator', async () => {
        const page = await createPageHarness();
        try {
            const button = page.document.getElementById('ipcStatusButton');
            expect(button.style.display).toBe('none');

            await page.emitFrame({ type: 'ipc_status', connected: true });
            expect(button.style.display).toBe('flex');
            expect(button.classList.contains('ipc-connected')).toBe(true);

            await page.emitFrame({ type: 'ipc_status', connected: false });
            expect(button.style.display).toBe('none');
            expect(button.classList.contains('ipc-connected')).toBe(false);
        } finally {
            page.close();
        }
    });
});

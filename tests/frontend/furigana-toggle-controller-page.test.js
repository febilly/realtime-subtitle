const { createPageHarness } = require('./helpers/page-harness');

describe('full-page furigana toggle controller wiring', () => {
    it('restores the active button from session storage on startup', async () => {
        const page = await createPageHarness({
            sessionStorage: { furiganaEnabled: 'true' },
        });
        try {
            const button = page.document.getElementById('furiganaButton');
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe('Disable furigana');
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');
        } finally {
            page.close();
        }
    });

    it('toggles the active button and persisted session state on click', async () => {
        const page = await createPageHarness();
        try {
            const button = page.document.getElementById('furiganaButton');
            expect(button.classList.contains('active')).toBe(false);
            expect(button.title).toBe('Enable furigana for Japanese');

            button.click();
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe('Disable furigana');
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');

            button.click();
            expect(button.classList.contains('active')).toBe(false);
            expect(button.title).toBe('Enable furigana for Japanese');
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('false');
        } finally {
            page.close();
        }
    });
});

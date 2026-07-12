const { createPageHarness } = require('./helpers/page-harness');

describe('full-page settings and controls smoke', () => {
    it('opens settings, operates a picker, closes, and toggles appearance controls', async () => {
        const page = await createPageHarness({ autoRestartEnabled: false });
        try {
            const settingsPanel = page.document.getElementById('settingsPanel');
            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(settingsPanel.hidden).toBe(false);
            expect(page.document.getElementById('settingsOverlay').hidden).toBe(false);

            const autoRestartTrigger = page.document.querySelector('#autoRestartPicker .lang-picker-button');
            expect(autoRestartTrigger).not.toBeNull();
            autoRestartTrigger.click();
            const pickerOptions = [...page.document.querySelectorAll('.lang-select-menu .lang-select-option')];
            expect(pickerOptions.length).toBeGreaterThan(0);
            pickerOptions.at(-1).click();
            expect(autoRestartTrigger.getAttribute('aria-expanded')).toBe('false');

            page.document.getElementById('settingsCancelButton').click();
            expect(settingsPanel.hidden).toBe(true);

            expect(page.document.body.classList.contains('dark-theme')).toBe(true);
            page.document.getElementById('themeToggle').click();
            expect(page.document.body.classList.contains('dark-theme')).toBe(false);
            expect(page.window.localStorage.getItem('theme')).toBe('light');

            page.document.getElementById('displayModeButton').click();
            expect(page.window.localStorage.getItem('displayMode')).toBe('original');

            const furiganaButton = page.document.getElementById('furiganaButton');
            furiganaButton.click();
            expect(furiganaButton.classList.contains('active')).toBe(true);
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');
        } finally {
            page.close();
        }
    });
});

const { createPageHarness } = require('./helpers/page-harness');

describe('full-page furigana toggle controller wiring', () => {
    it('restores the active button from session storage on startup', async () => {
        const page = await createPageHarness({
            sessionStorage: { furiganaEnabled: 'true' },
        });
        try {
            const button = page.document.getElementById('furiganaButton');
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe(
                'Disable phonetic annotations (Active: Japanese furigana, Chinese pinyin, Korean romanization · change in Settings)',
            );
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');
        } finally {
            page.close();
        }
    });

    it('toggles the active button, persisted session state, and dynamic language tooltip', async () => {
        const page = await createPageHarness({
            localStorage: {
                'featurePacks.v1': JSON.stringify({
                    version: 1,
                    confirmed: true,
                    isUpgrade: false,
                    enabled: { vrchat: false, learning: true, obs: false, transcript: false },
                }),
            },
        });
        try {
            const button = page.document.getElementById('furiganaButton');
            expect(button.classList.contains('active')).toBe(false);
            expect(button.title).toBe(
                'Enable phonetic annotations: Japanese furigana, Chinese pinyin, Korean romanization (change in Settings)',
            );

            button.click();
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe(
                'Disable phonetic annotations (Active: Japanese furigana, Chinese pinyin, Korean romanization · change in Settings)',
            );
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');

            // Toggling Chinese pinyin off in settings does not apply until Save is clicked
            page.document.getElementById('settingsButton').click();
            const zhSwitch = page.document.querySelector('#learningLanguagesList [data-learning-lang="zh"] .settings-switch');
            zhSwitch.click();
            expect(button.title).toBe(
                'Disable phonetic annotations (Active: Japanese furigana, Chinese pinyin, Korean romanization · change in Settings)',
            );
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);
            expect(button.title).toBe(
                'Disable phonetic annotations (Active: Japanese furigana, Korean romanization · change in Settings)',
            );

            // Disabling all remaining languages and saving shows the empty-selection settings hint
            page.document.getElementById('settingsButton').click();
            page.document.querySelector('#learningLanguagesList [data-learning-lang="ja"] .settings-switch').click();
            page.document.querySelector('#learningLanguagesList [data-learning-lang="ko"] .settings-switch').click();
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);
            expect(button.title).toBe(
                'Phonetic annotations enabled, but no languages are selected (enable in Settings → Language learning)',
            );

            button.click();
            expect(button.classList.contains('active')).toBe(false);
            expect(button.title).toBe(
                'Enable phonetic annotations (select languages in Settings → Language learning)',
            );
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('false');
        } finally {
            page.close();
        }
    });
});

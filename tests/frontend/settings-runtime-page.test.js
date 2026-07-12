const { createPageHarness } = require('./helpers/page-harness');

function chooseOption(page, hostSelector, index) {
    const trigger = page.document.querySelector(`${hostSelector} .lang-picker-button`);
    expect(trigger).not.toBeNull();
    trigger.click();
    const options = [...page.document.querySelectorAll('body > .lang-select-menu .lang-select-option')];
    expect(options.length).toBeGreaterThan(index);
    options[index].click();
}

describe('full-page runtime settings wiring', () => {
    it('applies picker drafts in the existing save order', async () => {
        const page = await createPageHarness({
            uiConfig: {
                env_key_present: { soniox: true, gemini: false },
                key_source: 'env',
                mode: 'direct',
                setup_required: false,
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            await page.flush(2);

            chooseOption(page, '#autoRestartPicker', 1);
            chooseOption(page, '#speakerLabelsPicker', 1);
            chooseOption(page, '#segmentModePicker', 1);
            chooseOption(page, '#translationModePicker', 0);

            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            const savePosts = page.fetchCalls.slice(beforeSave)
                .filter(([, options]) => options && options.method === 'POST')
                .map(([url]) => new URL(String(url), 'http://localhost/').pathname);
            expect(savePosts).toEqual([
                '/subtitle-font',
                '/speaker-labels',
                '/segment-mode',
                '/translation-mode',
            ]);
            expect(page.window.localStorage.getItem('autoRestartEnabled')).toBe('false');
            expect(page.window.localStorage.getItem('segmentMode')).toBe('endpoint');
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('fast');
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});

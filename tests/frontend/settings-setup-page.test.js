const { createPageHarness } = require('./helpers/page-harness');

describe('full-page setup wiring', () => {
    it('pushes an exact direct setup payload for a changed Soniox region', async () => {
        const page = await createPageHarness({
            uiConfig: {
                env_key_present: { soniox: true, gemini: false },
                key_source: 'env',
                mode: 'direct',
                soniox_region: 'us',
                setup_required: false,
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            await page.flush(2);

            page.document.querySelector('#sonioxRegionPicker .lang-picker-button').click();
            const options = [...page.document.querySelectorAll(
                'body > .lang-select-menu .lang-select-option',
            )];
            expect(options).toHaveLength(3);
            options[1].click();

            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            const setupCall = page.fetchCalls.slice(beforeSave).find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
            ));
            expect(setupCall).toBeDefined();
            expect(JSON.parse(setupCall[1].body)).toEqual({
                provider: 'soniox',
                mode: 'direct',
                soniox_region: 'eu',
            });
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});

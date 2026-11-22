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

    it('renders local model dropdowns as unified custom pickers instead of native selects', async () => {
        const page = await createPageHarness();
        try {
            page.document.getElementById('settingsButton').click();
            for (const id of ['localInferenceBackend', 'localAsrDevice', 'localEncoderDevice', 'localTranslationDevice']) {
                const host = page.document.getElementById(id);
                expect(host.tagName).toBe('DIV');
                expect(host.querySelector('.custom-select .custom-select-button')).not.toBeNull();
            }
        } finally {
            page.close();
        }
    });

    it('detects unsaved local device picker changes and resets them after Cancel and reopen', async () => {
        const page = await createPageHarness({
            uiConfig: { provider: 'local', mode: 'direct', setup_required: false },
            localStorage: {
                'providerSettings.v1': JSON.stringify({
                    providerOverride: 'local',
                    localConfig: { backend: 'local', asr_device: 'auto' },
                    keys: {},
                }),
            },
        });
        try {
            const doc = page.document;
            doc.getElementById('settingsButton').click();
            await page.flush(8);

            // Opening local settings without edits closes cleanly without prompting
            doc.getElementById('settingsCloseButton').click();
            expect(doc.getElementById('settingsPanel').hidden).toBe(true);
            expect(doc.getElementById('confirmDialog').hidden).toBe(true);

            // Changing localAsrDevice to CPU triggers unsaved-changes prompt on close
            doc.getElementById('settingsButton').click();
            await page.flush(8);
            const trigger = doc.querySelector('#localAsrDevice .custom-select-button');
            trigger.click();
            const cpuOption = [...doc.querySelectorAll('body > .custom-select-menu .custom-select-option')]
                .find((opt) => opt.textContent === 'CPU');
            cpuOption.click();
            expect(doc.getElementById('localAsrDevice').value).toBe('cpu');

            doc.getElementById('settingsCloseButton').click();
            expect(doc.getElementById('confirmDialog').hidden).toBe(false);
            doc.getElementById('confirmCancelButton').click();
            await page.flush(4);

            // Cancel button discards the edit and reopening restores auto
            doc.getElementById('settingsCancelButton').click();
            expect(doc.getElementById('settingsPanel').hidden).toBe(true);
            doc.getElementById('settingsButton').click();
            await page.flush(8);
            expect(doc.getElementById('localAsrDevice').value).toBe('auto');
        } finally {
            page.close();
        }
    });
});

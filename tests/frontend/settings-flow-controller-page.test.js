const { createPageHarness } = require('./helpers/page-harness');

describe('full-page settings flow controller wiring', () => {
    it('opens and closes an ordinary settings flow through the real controls', async () => {
        const page = await createPageHarness();
        try {
            const overlay = page.document.getElementById('settingsOverlay');
            page.document.getElementById('settingsButton').click();
            expect(overlay.hidden).toBe(false);

            page.document.getElementById('settingsCancelButton').click();
            expect(overlay.hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('force-opens direct settings when startup still requires setup', async () => {
        const page = await createPageHarness({
            uiConfig: { relay_available: false, setup_required: true },
        });
        try {
            const overlay = page.document.getElementById('settingsOverlay');
            const closeButton = page.document.getElementById('settingsCloseButton');
            const cancelButton = page.document.getElementById('settingsCancelButton');
            expect(overlay.hidden).toBe(false);
            expect(closeButton.style.display).toBe('none');
            expect(cancelButton.style.display).toBe('none');

            cancelButton.click();
            expect(overlay.hidden).toBe(false);
        } finally {
            page.close();
        }
    });

    it('closes immediately on close button or overlay when untouched, and prompts on unsaved changes', async () => {
        const page = await createPageHarness({
            localStorage: {
                'I18N.lang': 'zh',
                'featurePacks.v1': JSON.stringify({
                    version: 1,
                    confirmed: true,
                    isUpgrade: false,
                    enabled: { vrchat: true, learning: true, obs: false, transcript: true },
                }),
            },
            uiConfig: {
                env_key_present: { soniox: true, gemini: false },
                key_source: 'env',
                mode: 'direct',
                setup_required: false,
                transcript_desktop_available: true,
                transcript_settings: { auto_save: false, format: 'srt', directory: 'C:\\Transcripts' },
            },
        });
        try {
            const doc = page.document;
            const panel = doc.getElementById('settingsPanel');
            const confirmDialog = doc.getElementById('confirmDialog');
            const confirmMessage = doc.getElementById('confirmMessage');
            const confirmOk = doc.getElementById('confirmOkButton');
            const confirmAlt = doc.getElementById('confirmAltButton');
            const confirmCancel = doc.getElementById('confirmCancelButton');

            // 1. Open and close immediately with no edits -> no prompt
            doc.getElementById('settingsButton').click();
            expect(panel.hidden).toBe(false);
            doc.getElementById('settingsCloseButton').click();
            expect(panel.hidden).toBe(true);
            expect(confirmDialog.hidden).toBe(true);

            // 2. Toggle a switch and revert it back -> still clean, no prompt
            doc.getElementById('settingsButton').click();
            const autoRestart = doc.querySelector('#autoRestartPicker .settings-switch');
            autoRestart.click();
            autoRestart.click();
            doc.getElementById('settingsCloseButton').click();
            expect(panel.hidden).toBe(true);
            expect(confirmDialog.hidden).toBe(true);

            // 3. Modify auto-restart switch and click close button -> prompts with 3 choices
            doc.getElementById('settingsButton').click();
            doc.querySelector('#autoRestartPicker .settings-switch').click();
            doc.getElementById('settingsCloseButton').click();
            expect(confirmDialog.hidden).toBe(false);
            expect(confirmMessage.textContent).toBe('Some settings have been changed. Do you want to save them?');
            expect(confirmOk.textContent).toBe('Save');
            expect(confirmAlt.textContent).toBe("Don't save");
            expect(confirmCancel.textContent).toBe('Cancel');

            // 3a. Choosing Cancel keeps settings panel open with unsaved edit intact
            confirmCancel.click();
            await page.flush(4);
            expect(confirmDialog.hidden).toBe(true);
            expect(panel.hidden).toBe(false);
            expect(doc.querySelector('#autoRestartPicker .settings-switch').getAttribute('aria-checked')).toBe('true');

            // 3b. Clicking overlay also prompts; choosing Don't save discards & closes
            doc.getElementById('settingsOverlay').click();
            expect(confirmDialog.hidden).toBe(false);
            confirmAlt.click();
            await page.flush(4);
            expect(confirmDialog.hidden).toBe(true);
            expect(panel.hidden).toBe(true);
            expect(page.window.localStorage.getItem('autoRestartEnabled')).toBe('false');

            // 4. Modify transcript format + learning toggle, click close, choose Save -> applies & closes
            doc.getElementById('settingsButton').click();
            await page.flush(4);
            doc.querySelector('#transcriptAutoSavePicker .settings-switch').click();
            doc.querySelector('#learningLanguagesList [data-learning-lang="ja"] .settings-switch').click();
            doc.getElementById('settingsCloseButton').click();
            expect(confirmDialog.hidden).toBe(false);
            confirmOk.click();
            await page.flush(12);
            expect(confirmDialog.hidden).toBe(true);
            expect(panel.hidden).toBe(true);
            expect(JSON.parse(page.window.localStorage.getItem('transcriptSettings.v1')).autoSave).toBe(true);
            expect(JSON.parse(page.window.localStorage.getItem('learningLanguages.v1')).ja).toBe(false);
        } finally {
            page.close();
        }
    });
});

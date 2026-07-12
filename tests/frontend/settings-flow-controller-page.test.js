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
});

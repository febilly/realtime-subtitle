const { createPageHarness } = require('./helpers/page-harness');

function chooseFlow(page, index) {
    page.document.querySelector('#subtitleFlowPicker .custom-select-button').click();
    const options = [...page.document.querySelectorAll('body > .custom-select-menu .custom-select-option')];
    expect(options).toHaveLength(2);
    options[index].click();
}

describe('main-window subtitle flow setting', () => {
    it('defaults to upward flow and applies a downward choice on save', async () => {
        const page = await createPageHarness();
        try {
            expect(page.document.getElementById('subtitleFlowButton')).toBeNull();

            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(page.document.querySelector('#subtitleFlowPicker .custom-select-label').textContent)
                .toBe('Upward (newest at the bottom)');

            chooseFlow(page, 1);
            // Nothing changes until the settings are saved.
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBeNull();

            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBe('down');
        } finally {
            page.close();
        }
    });

    it('restores a saved downward flow into the setting', async () => {
        const page = await createPageHarness({
            localStorage: { subtitleFlowDirection: 'down' },
        });
        try {
            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(page.document.querySelector('#subtitleFlowPicker .custom-select-label').textContent)
                .toBe('Downward (newest at the top)');
        } finally {
            page.close();
        }
    });
});

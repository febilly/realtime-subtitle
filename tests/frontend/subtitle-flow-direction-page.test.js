const { createPageHarness } = require('./helpers/page-harness');

function chooseFlow(page, index) {
    page.document.querySelector('#subtitleFlowPicker .lang-picker-button').click();
    const options = [...page.document.querySelectorAll('body > .lang-select-menu .lang-select-option')];
    expect(options).toHaveLength(2);
    options[index].click();
}

describe('main-window subtitle flow setting', () => {
    it('defaults to upward flow and applies a downward choice on save', async () => {
        const page = await createPageHarness();
        try {
            const toast = page.document.getElementById('toast');
            expect(page.document.getElementById('subtitleFlowButton')).toBeNull();
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);

            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(page.document.querySelector('#subtitleFlowPicker .lang-picker-label').textContent)
                .toBe('Upward (newest at the bottom)');

            chooseFlow(page, 1);
            // Nothing changes until the settings are saved.
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBeNull();

            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBe('down');
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);
        } finally {
            page.close();
        }
    });

    it('docks notifications at the old-subtitle end regardless of subtitle count', async () => {
        const page = await createPageHarness();
        try {
            const toast = page.document.getElementById('toast');
            const subtitle = (index) => ({
                text: `Sentence ${index}.`,
                is_final: true,
                translation_status: 'original',
                language: 'en',
                speaker: '1',
                llm_sentence_id: `flow-${index}`,
            });
            const separator = () => ({ text: '', is_final: true, is_separator: true });

            await page.emitFrame({
                type: 'update',
                final_tokens: [subtitle(1), separator(), subtitle(2), separator(), subtitle(3), separator()],
                non_final_tokens: [],
            });
            expect(page.document.querySelectorAll('.sentence-block')).toHaveLength(3);
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);

            await page.emitFrame({
                type: 'update',
                final_tokens: [subtitle(4)],
                non_final_tokens: [],
            });
            expect(page.document.querySelectorAll('.sentence-block')).toHaveLength(4);
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);
        } finally {
            page.close();
        }
    });

    it('restores a saved downward flow into the setting and the notification dock', async () => {
        const page = await createPageHarness({
            localStorage: { subtitleFlowDirection: 'down' },
        });
        try {
            expect(page.document.getElementById('toast').classList.contains('toast-container--bottom'))
                .toBe(true);
            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(page.document.querySelector('#subtitleFlowPicker .lang-picker-label').textContent)
                .toBe('Downward (newest at the top)');
        } finally {
            page.close();
        }
    });
});

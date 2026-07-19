const { createPageHarness } = require('./helpers/page-harness');

describe('main-window subtitle flow control', () => {
    it('defaults to upward flow and persists an independent downward choice', async () => {
        const page = await createPageHarness();
        try {
            const button = page.document.getElementById('subtitleFlowButton');
            const use = page.document.querySelector('#subtitleFlowIcon use');
            const toast = page.document.getElementById('toast');

            expect(button.title).toBe('Subtitles flow upward (click to flow downward)');
            expect(use.getAttribute('href')).toContain('#arrow-up-from-line');
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);

            button.click();
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBe('down');
            expect(button.title).toBe('Subtitles flow downward (click to flow upward)');
            expect(use.getAttribute('href')).toContain('#arrow-down-to-line');
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);
        } finally {
            page.close();
        }
    });

    it('uses the bottom for up to three subtitles, then follows the flow direction', async () => {
        const page = await createPageHarness();
        try {
            const toast = page.document.getElementById('toast');
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);

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
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);

            await page.emitFrame({
                type: 'update',
                final_tokens: [subtitle(4)],
                non_final_tokens: [],
            });

            expect(page.document.querySelectorAll('.sentence-block')).toHaveLength(4);
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);

            page.document.getElementById('subtitleFlowButton').click();
            expect(toast.classList.contains('toast-container--bottom')).toBe(true);
        } finally {
            page.close();
        }
    });

    it('restores downward flow without using the Qt overlay settings', async () => {
        const page = await createPageHarness({
            localStorage: { subtitleFlowDirection: 'down' },
        });
        try {
            const button = page.document.getElementById('subtitleFlowButton');
            expect(button.title).toBe('Subtitles flow downward (click to flow upward)');
            expect(page.document.querySelector('#subtitleFlowIcon use').getAttribute('href'))
                .toContain('#arrow-down-to-line');
            expect(page.document.getElementById('toast').classList.contains('toast-container--bottom'))
                .toBe(true);
        } finally {
            page.close();
        }
    });
});

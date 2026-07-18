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
            expect(toast.classList.contains('toast-container--bottom')).toBe(false);

            button.click();
            expect(page.window.localStorage.getItem('subtitleFlowDirection')).toBe('down');
            expect(button.title).toBe('Subtitles flow downward (click to flow upward)');
            expect(use.getAttribute('href')).toContain('#arrow-down-to-line');
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

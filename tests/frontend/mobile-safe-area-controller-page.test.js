const { createPageHarness } = require('./helpers/page-harness');

describe('full-page mobile safe area controller wiring', () => {
    it('hides the optional control on desktop', async () => {
        const page = await createPageHarness();
        try {
            expect(page.document.getElementById('bottomSafeAreaButton').style.display).toBe('none');
        } finally {
            page.close();
        }
    });

    it('toggles persisted spacing and icon state for a mobile user agent', async () => {
        const page = await createPageHarness({
            userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile',
        });
        try {
            const button = page.document.getElementById('bottomSafeAreaButton');
            const container = page.document.getElementById('subtitleContainer');
            expect(button.style.display).toBe('');
            expect(container.classList.contains('mobile-bottom-safe-area')).toBe(false);

            button.click();
            expect(page.window.localStorage.getItem('bottomSafeAreaEnabled')).toBe('true');
            expect(container.classList.contains('mobile-bottom-safe-area')).toBe(true);
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe('Hide extra bottom space (mobile)');
            expect(button.querySelector('use').getAttribute('href')).toContain('#arrow-up-from-line');
        } finally {
            page.close();
        }
    });
});

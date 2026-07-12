const { createPageHarness } = require('./helpers/page-harness');

function pathOf(url) {
    return new URL(String(url), 'http://localhost/').pathname;
}

describe('full-page theme controller wiring', () => {
    it('cycles through chroma and synchronizes native window stacking once per state', async () => {
        const page = await createPageHarness({
            localStorage: { theme: 'dark' },
            uiConfig: { enable_chroma_theme: true },
        });
        try {
            const windowPosts = () => page.fetchCalls.filter(([url, options]) => (
                pathOf(url) === '/window-on-top' && options && options.method === 'POST'
            ));
            expect(windowPosts()).toHaveLength(1);
            expect(JSON.parse(windowPosts()[0][1].body)).toEqual({ on_top: true });

            const toggle = page.document.getElementById('themeToggle');
            toggle.click();
            expect(page.window.localStorage.getItem('theme')).toBe('light');
            expect(windowPosts()).toHaveLength(1);

            toggle.click();
            expect(page.window.localStorage.getItem('theme')).toBe('chroma');
            expect(page.document.body.classList.contains('chroma-theme')).toBe(true);
            expect(windowPosts()).toHaveLength(2);
            expect(JSON.parse(windowPosts()[1][1].body)).toEqual({ on_top: false });
        } finally {
            page.close();
        }
    });
});

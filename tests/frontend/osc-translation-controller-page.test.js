const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

function pathOf(url) {
    return new URL(String(url), 'http://localhost/').pathname;
}

describe('full-page OSC translation controller wiring', () => {
    it('loads status and toggles through the real button wiring', async () => {
        const page = await createPageHarness({
            fetch: async (url, options = {}) => {
                if (pathOf(url) !== '/osc-translation') return defaultFetchResponse(url);
                if (options.method === 'POST') {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ enabled: JSON.parse(options.body).enabled }),
                    };
                }
                return { ok: true, status: 200, json: async () => ({ enabled: false }) };
            },
        });
        try {
            const button = page.document.getElementById('oscTranslationButton');
            expect(button.classList.contains('active')).toBe(false);
            button.click();
            await page.flush(3);
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe('Sending translation to VRChat (click to disable)');
            const post = page.fetchCalls.find(([url, options]) => (
                pathOf(url) === '/osc-translation' && options && options.method === 'POST'
            ));
            expect(JSON.parse(post[1].body)).toEqual({ enabled: true });
        } finally {
            page.close();
        }
    });
});

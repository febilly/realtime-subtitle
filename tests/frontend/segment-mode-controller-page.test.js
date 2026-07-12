const { createPageHarness } = require('./helpers/page-harness');

function pathOf(url) {
    return new URL(String(url), 'http://localhost/').pathname;
}

describe('full-page segment mode controller wiring', () => {
    it('pushes a stored browser preference over a different backend mode', async () => {
        const page = await createPageHarness({
            localStorage: { segmentMode: 'endpoint' },
            uiConfig: { segment_mode: 'punctuation' },
        });
        try {
            const segmentPosts = page.fetchCalls.filter(([url, options]) => (
                pathOf(url) === '/segment-mode' && options && options.method === 'POST'
            ));
            expect(segmentPosts).toHaveLength(1);
            expect(JSON.parse(segmentPosts[0][1].body)).toEqual({ mode: 'endpoint' });
            expect(page.window.localStorage.getItem('segmentMode')).toBe('endpoint');
        } finally {
            page.close();
        }
    });

    it('applies WebSocket mode changes through the controller', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({ type: 'segment_mode_changed', mode: 'endpoint' });
            expect(page.window.localStorage.getItem('segmentMode')).toBe('endpoint');
        } finally {
            page.close();
        }
    });
});

const { createPageHarness } = require('./helpers/page-harness');

function pathOf(url) {
    return new URL(String(url), 'http://localhost/').pathname;
}

function postPaths(page) {
    return page.fetchCalls
        .filter(([, options]) => options && options.method === 'POST')
        .map(([url]) => pathOf(url));
}

describe('full-page translation mode controller wiring', () => {
    it('pushes the stored mode once during startup and skips the unlocked legacy endpoint', async () => {
        const page = await createPageHarness({ translationUiMode: 'accurate' });
        try {
            const requestedPaths = page.fetchCalls.map(([url]) => pathOf(url));
            expect(postPaths(page).filter((path) => path === '/translation-mode')).toHaveLength(1);
            expect(requestedPaths).not.toContain('/llm-refine');
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('accurate');
            expect(page.window.localStorage.getItem('llmRefineMode')).toBe('translate');
        } finally {
            page.close();
        }
    });

    it('keeps saved preferences when the backend reports LLM translation unavailable', async () => {
        const page = await createPageHarness({
            translationUiMode: 'accurate',
            uiConfig: { llm_refine_available: false },
        });
        try {
            expect(postPaths(page)).not.toContain('/translation-mode');
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('accurate');
            expect(page.window.localStorage.getItem('llmRefineMode')).toBe('translate');

            page.document.getElementById('settingsButton').click();
            await page.flush(2);
            expect(page.document.getElementById('translationModeSection').hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('routes a prepaid fallback through the normal persistence and backend path', async () => {
        const page = await createPageHarness({ translationUiMode: 'accurate' });
        try {
            const before = page.fetchCalls.length;
            await page.emitFrame({
                type: 'translation_mode_fallback',
                needs_restart: false,
            });
            await page.flush(4);

            const fallbackCalls = page.fetchCalls.slice(before);
            expect(fallbackCalls.some(([url, options]) => (
                pathOf(url) === '/translation-mode'
                && options
                && JSON.parse(options.body).mode === 'fast'
            ))).toBe(true);
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('fast');
            expect(page.window.localStorage.getItem('llmRefineMode')).toBe('off');
            expect(fallbackCalls.map(([url]) => pathOf(url))).not.toContain('/restart');
        } finally {
            page.close();
        }
    });
});

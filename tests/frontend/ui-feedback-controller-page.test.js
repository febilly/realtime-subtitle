const {
    createPageHarness,
    defaultFetchResponse,
} = require('./helpers/page-harness');

function fetchWithApiStatus(payload) {
    return async (url, options = {}) => {
        const pathname = new URL(String(url), 'http://localhost/').pathname;
        if (pathname === '/api-key-status') {
            return {
                ok: true,
                status: 200,
                statusText: 'OK',
                json: async () => payload,
                text: async () => JSON.stringify(payload),
            };
        }
        return defaultFetchResponse(url, {
            translationUiMode: 'hybrid',
            ...options,
        });
    };
}

describe('full-page UI feedback controller wiring', () => {
    it('renders a localized fatal overlay from startup API key status', async () => {
        const page = await createPageHarness({
            fetch: fetchWithApiStatus({
                status: 'error',
                message: 'Manual restart is disabled by server config',
            }),
        });
        try {
            const overlay = page.document.querySelector('.error-message-overlay');
            expect(overlay).not.toBeNull();
            expect(overlay.querySelector('.error-title').textContent).toBe('Error');
            expect(overlay.querySelector('.error-text').textContent)
                .toBe('Manual restart is disabled by server config');
            expect(overlay.querySelector('.error-suggestion').textContent)
                .toContain('API key');
        } finally {
            page.close();
        }
    });

    it('renders a safe WebSocket error and force-opens settings for an API key code', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({
                type: 'error',
                code: 'api_key',
                message: '<img src=x onerror=alert(1)>',
            });

            const overlay = page.document.querySelector('.error-message-overlay');
            expect(overlay.querySelector('.error-text').textContent)
                .toBe('<img src=x onerror=alert(1)>');
            expect(overlay.querySelector('img')).toBeNull();
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
            expect(page.document.getElementById('settingsOverlay').hidden).toBe(false);
        } finally {
            page.close();
        }
    });

    it('opens settings through the billing-exhausted toast action', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({
                type: 'session_disconnected',
                code: 'billing_exhausted',
                relay_terminal: true,
                reason: 'quota exhausted',
            });

            const toast = page.document.getElementById('toast');
            const toastItem = toast.querySelector('.toast');
            expect(toast.hidden).toBe(false);
            expect(toastItem.classList.contains('error')).toBe(true);
            expect(toast.textContent).toContain('Credits or free quota exhausted.');
            const action = toast.querySelector('.toast-action');
            expect(action.textContent).toBe('Open settings');

            action.click();

            expect(toast.hidden).toBe(true);
            expect(toast.querySelectorAll('.toast')).toHaveLength(0);
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});

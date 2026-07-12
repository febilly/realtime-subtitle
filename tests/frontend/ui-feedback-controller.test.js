const { JSDOM } = require('jsdom');
const UiFeedbackController = require('../../static/js/ui-feedback-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM(`<!doctype html>
        <main id="subtitles">seed subtitles</main>
        <div id="toast" hidden></div>`);
    pages.push(dom);
    const { document } = dom.window;
    const subtitleContainer = document.getElementById('subtitles');
    const toast = options.withoutToast ? null : document.getElementById('toast');
    const logger = { error: vi.fn() };
    const timerTokens = [];
    const schedule = vi.fn((callback, delay) => {
        const token = { callback, delay };
        timerTokens.push(token);
        return token;
    });
    const cancel = vi.fn();
    const fetchImpl = options.fetch || vi.fn(async () => ({
        ok: true,
        statusText: 'OK',
        json: async () => ({ status: 'ok' }),
    }));
    const localizeBackendMessage = options.localizeBackendMessage
        || vi.fn((message) => `localized:${message}`);
    const escapeHtml = (value) => {
        const element = document.createElement('div');
        element.textContent = value;
        return element.innerHTML;
    };
    const controller = UiFeedbackController.create({
        document,
        fetch: fetchImpl,
        subtitleContainer,
        toast,
        t: (key) => ({
            error_title: '<Error>',
            error_suggestion_api: 'Check <API>',
        })[key] || `label:${key}`,
        localizeBackendMessage,
        escapeHtml,
        console: logger,
        setTimeout: schedule,
        clearTimeout: cancel,
    });
    return {
        cancel,
        controller,
        fetchImpl,
        localizeBackendMessage,
        logger,
        schedule,
        subtitleContainer,
        timerTokens,
        toast,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('UiFeedbackController toast lifecycle', () => {
    it('renders toast text safely and applies error state with the default timeout', () => {
        const page = createHarness();

        page.controller.showToast('<strong>unsafe</strong>', true);

        expect(page.toast.hidden).toBe(false);
        expect(page.toast.classList.contains('error')).toBe(true);
        expect(page.toast.textContent).toBe('<strong>unsafe</strong>');
        expect(page.toast.querySelector('strong')).toBeNull();
        expect(page.schedule).toHaveBeenCalledWith(expect.any(Function), 4000);

        page.timerTokens[0].callback();
        expect(page.toast.hidden).toBe(true);
    });

    it('replaces the previous timer and preserves timeout fallback semantics', () => {
        const page = createHarness();
        page.controller.showToast('first', false, { timeoutMs: 250 });
        const firstTimer = page.timerTokens[0];

        page.controller.showToast('second', false, { timeoutMs: 0 });

        expect(page.cancel).toHaveBeenCalledOnce();
        expect(page.cancel).toHaveBeenCalledWith(firstTimer);
        expect(page.schedule).toHaveBeenNthCalledWith(1, expect.any(Function), 250);
        expect(page.schedule).toHaveBeenNthCalledWith(2, expect.any(Function), 4000);
        expect(page.toast.textContent).toBe('second');
    });

    it('hides an action toast before invoking its callback and leaves its timer intact', () => {
        const page = createHarness();
        const actionStates = [];
        const onAction = vi.fn(() => actionStates.push(page.toast.hidden));
        page.controller.showToast('quota exhausted', true, {
            actionLabel: 'Open settings',
            onAction,
            timeoutMs: 8000,
        });

        const action = page.toast.querySelector('.toast-action');
        expect(action.textContent).toBe('Open settings');
        action.click();

        expect(page.toast.hidden).toBe(true);
        expect(actionStates).toEqual([true]);
        expect(onAction).toHaveBeenCalledOnce();
        expect(page.cancel).not.toHaveBeenCalled();
    });

    it('does nothing when the optional toast element is absent', () => {
        const page = createHarness({ withoutToast: true });

        expect(page.controller.showToast('ignored', true)).toBeUndefined();
        expect(page.schedule).not.toHaveBeenCalled();
    });
});

describe('UiFeedbackController error presentation and API status', () => {
    it('localizes and escapes the fatal error overlay without changing model state', () => {
        const page = createHarness({
            localizeBackendMessage: vi.fn(() => '<img src=x onerror=alert(1)>'),
        });
        page.subtitleContainer.scrollTop = 50;

        page.controller.displayErrorMessage('backend failure');

        expect(page.localizeBackendMessage).toHaveBeenCalledWith('backend failure');
        expect(page.subtitleContainer.scrollTop).toBe(0);
        expect(page.subtitleContainer.querySelector('.error-title').textContent).toBe('<Error>');
        expect(page.subtitleContainer.querySelector('.error-text').textContent)
            .toBe('<img src=x onerror=alert(1)>');
        expect(page.subtitleContainer.querySelector('.error-suggestion').textContent)
            .toBe('Check <API>');
        expect(page.subtitleContainer.querySelector('img')).toBeNull();
    });

    it('shows an error payload returned by the API key status endpoint', async () => {
        const page = createHarness({
            fetch: vi.fn(async () => ({
                ok: true,
                statusText: 'OK',
                json: async () => ({ status: 'error', message: 'bad key' }),
            })),
        });

        await page.controller.fetchApiKeyStatus();

        expect(page.fetchImpl).toHaveBeenCalledWith('/api-key-status');
        expect(page.subtitleContainer.querySelector('.error-text').textContent)
            .toBe('localized:bad key');
    });

    it('leaves subtitles unchanged for a healthy API key status', async () => {
        const page = createHarness();

        await page.controller.fetchApiKeyStatus();

        expect(page.subtitleContainer.textContent).toBe('seed subtitles');
        expect(page.localizeBackendMessage).not.toHaveBeenCalled();
        expect(page.logger.error).not.toHaveBeenCalled();
    });

    it('logs non-success responses without replacing subtitles', async () => {
        const page = createHarness({
            fetch: vi.fn(async () => ({
                ok: false,
                statusText: 'Service Unavailable',
            })),
        });

        await page.controller.fetchApiKeyStatus();

        expect(page.logger.error).toHaveBeenCalledWith(
            'Failed to fetch API key status:', 'Service Unavailable',
        );
        expect(page.subtitleContainer.textContent).toBe('seed subtitles');
    });

    it.each([
        ['fetch failure', vi.fn(async () => { throw new Error('offline'); })],
        ['JSON failure', vi.fn(async () => ({
            ok: true,
            statusText: 'OK',
            json: async () => { throw new Error('invalid JSON'); },
        }))],
    ])('logs a %s without replacing subtitles', async (_label, fetchImpl) => {
        const page = createHarness({ fetch: fetchImpl });

        await page.controller.fetchApiKeyStatus();

        expect(page.logger.error).toHaveBeenCalledWith(
            'Error fetching API key status:', expect.any(Error),
        );
        expect(page.subtitleContainer.textContent).toBe('seed subtitles');
    });
});

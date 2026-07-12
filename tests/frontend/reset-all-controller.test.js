const { JSDOM } = require('jsdom');
const ResetAllController = require('../../static/js/reset-all-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><body><main>application</main></body>');
    pages.push(dom);
    if (options.dark) dom.window.document.body.classList.add('dark-theme');
    const events = [];
    const showConfirm = vi.fn(async (...args) => {
        events.push(`confirm:${args[0]}`);
        return options.confirmed === true;
    });
    const localStorage = {
        clear: vi.fn(() => {
            events.push('localStorage');
            if (options.localStorageError) throw options.localStorageError;
        }),
    };
    const sessionStorage = {
        clear: vi.fn(() => {
            events.push('sessionStorage');
            if (options.sessionStorageError) throw options.sessionStorageError;
        }),
    };
    const fetchImpl = vi.fn(async (...args) => {
        events.push('fetch');
        if (options.fetchError) throw options.fetchError;
        return { ok: true, args };
    });
    const windowRef = {
        close: vi.fn(() => {
            events.push('close');
            if (options.closeError) throw options.closeError;
        }),
    };
    const controller = ResetAllController.create({
        document: dom.window.document,
        window: windowRef,
        fetch: fetchImpl,
        localStorage,
        sessionStorage,
        t: (key) => `label:${key}`,
        showConfirm,
    });
    return {
        controller,
        document: dom.window.document,
        events,
        fetchImpl,
        localStorage,
        sessionStorage,
        showConfirm,
        windowRef,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('ResetAllController confirmation and side effects', () => {
    it('stops after presenting the exact destructive confirmation when cancelled', async () => {
        const page = createHarness({ confirmed: false });

        await page.controller.handle();

        expect(page.showConfirm).toHaveBeenCalledWith('label:reset_all_confirm', {
            okLabel: 'label:reset_all',
            cancelLabel: 'label:cancel',
            danger: true,
        });
        expect(page.events).toEqual(['confirm:label:reset_all_confirm']);
        expect(page.document.body.textContent).toContain('application');
    });

    it('clears both storage realms, requests shutdown, closes, then shows the light fallback', async () => {
        const page = createHarness({ confirmed: true });

        await page.controller.handle();

        expect(page.events).toEqual([
            'confirm:label:reset_all_confirm',
            'localStorage',
            'sessionStorage',
            'fetch',
            'close',
        ]);
        expect(page.fetchImpl).toHaveBeenCalledWith('/shutdown', { method: 'POST' });
        expect(page.document.body.textContent).toBe('label:reset_all_done');
        expect(page.document.body.firstElementChild.style.color).toBe('rgb(31, 41, 55)');
    });

    it('uses the light-on-dark fallback color for a dark theme', async () => {
        const page = createHarness({ confirmed: true, dark: true });

        await page.controller.handle();

        expect(page.document.body.firstElementChild.style.color).toBe('rgb(229, 231, 235)');
    });

    it('still renders the exit fallback when storage, shutdown, and close all throw', async () => {
        const page = createHarness({
            confirmed: true,
            localStorageError: new Error('local blocked'),
            sessionStorageError: new Error('session blocked'),
            fetchError: new Error('server closed'),
            closeError: new Error('browser denied'),
        });

        await expect(page.controller.handle()).resolves.toBeUndefined();

        expect(page.events).toEqual([
            'confirm:label:reset_all_confirm',
            'localStorage',
            'sessionStorage',
            'fetch',
            'close',
        ]);
        expect(page.document.body.textContent).toBe('label:reset_all_done');
    });
});

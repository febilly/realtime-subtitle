const { JSDOM } = require('jsdom');
const OscTranslationController = require('../../static/js/osc-translation-controller');

const pages = [];

function response(body = {}, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: options.json || vi.fn(async () => body),
    };
}

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><button id="osc"><svg id="icon"></svg></button>');
    pages.push(dom);
    const button = options.withoutButton ? null : dom.window.document.getElementById('osc');
    const icon = options.withoutIcon ? null : dom.window.document.getElementById('icon');
    const fetch = options.fetch || vi.fn(async () => response());
    const logger = { log: vi.fn(), error: vi.fn() };
    const controller = OscTranslationController.create({
        fetch,
        button,
        icon,
        t: (key) => `label:${key}`,
        console: logger,
    });
    return { button, controller, fetch, icon, logger };
}

async function flush() {
    await Promise.resolve();
    await Promise.resolve();
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('OscTranslationController lifecycle and display', () => {
    it('validates fetch and tolerates an unavailable optional control', async () => {
        expect(() => OscTranslationController.create({ fetch: 42 })).toThrow(
            'OscTranslationController.create requires fetch',
        );
        const page = createHarness({ withoutButton: true });
        expect(page.controller.updateButton()).toBe(false);
        await expect(page.controller.fetchStatus()).resolves.toBe(false);
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('binds once, renders initial state, and removes its listener', async () => {
        const page = createHarness({
            fetch: vi.fn(async (_url, options) => response({
                enabled: JSON.parse(options.body).enabled,
            })),
        });
        expect(page.controller.updateButton()).toBe(true);
        expect(page.button.title).toBe('label:osc_off');
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);

        page.button.click();
        await flush();
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.button.classList.contains('active')).toBe(true);

        page.controller.destroy();
        page.fetch.mockClear();
        page.button.click();
        expect(page.fetch).not.toHaveBeenCalled();
    });
});

describe('OscTranslationController network state', () => {
    it('posts the inverse state and trusts the authoritative response', async () => {
        const page = createHarness({
            fetch: vi.fn(async () => response({ enabled: true })),
        });
        await expect(page.controller.toggle()).resolves.toBe(true);
        expect(page.fetch).toHaveBeenCalledWith('/osc-translation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: true }),
        });
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.button.title).toBe('label:osc_on');
        expect(page.logger.log).toHaveBeenCalledWith('OSC translation enabled');
    });

    it('reports parse and non-OK failures without mutating state', async () => {
        const parseFailure = new Error('invalid JSON');
        const page = createHarness({
            fetch: vi.fn(async () => response({}, {
                ok: false,
                status: 503,
                json: vi.fn(async () => { throw parseFailure; }),
            })),
        });
        await expect(page.controller.toggle()).resolves.toBe(false);
        expect(page.logger.error).toHaveBeenCalledWith(
            'Failed to parse OSC translation toggle response:', parseFailure,
        );
        expect(page.logger.error).toHaveBeenCalledWith(
            'Failed to toggle OSC translation:', 503, undefined,
        );
        expect(page.controller.isEnabled()).toBe(false);
    });

    it('reports toggle network failures', async () => {
        const failure = new Error('offline');
        const page = createHarness({ fetch: vi.fn(async () => { throw failure; }) });
        await expect(page.controller.toggle()).resolves.toBe(false);
        expect(page.logger.error).toHaveBeenCalledWith(
            'Error toggling OSC translation:', failure,
        );
    });

    it('loads initial status and updates the button', async () => {
        const page = createHarness({
            fetch: vi.fn(async () => response({ enabled: true })),
        });
        await expect(page.controller.fetchStatus()).resolves.toBe(true);
        expect(page.fetch).toHaveBeenCalledWith('/osc-translation');
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.button.classList.contains('active')).toBe(true);
    });

    it('handles status non-OK, parse, and network failures', async () => {
        const nonOk = createHarness({
            fetch: vi.fn(async () => response({}, { ok: false })),
        });
        await expect(nonOk.controller.fetchStatus()).resolves.toBe(false);

        const parseFailure = new Error('bad JSON');
        const invalid = createHarness({
            fetch: vi.fn(async () => response({}, {
                json: vi.fn(async () => { throw parseFailure; }),
            })),
        });
        await expect(invalid.controller.fetchStatus()).resolves.toBe(false);
        expect(invalid.logger.error).toHaveBeenCalledWith(
            'Error fetching OSC translation status:', parseFailure,
        );
    });
});

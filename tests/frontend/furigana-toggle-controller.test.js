const { JSDOM } = require('jsdom');
const FuriganaToggleController = require('../../static/js/furigana-toggle-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><button id="furigana"><svg id="icon"></svg></button>');
    pages.push(dom);
    const values = new Map();
    if (options.storedValue !== undefined) {
        values.set(FuriganaToggleController.STORAGE_KEY, String(options.storedValue));
    }
    const storage = {
        getItem: vi.fn((key) => {
            if (options.readFailure) throw options.readFailure;
            return values.get(key) ?? null;
        }),
        setItem: vi.fn((key, value) => {
            if (options.writeFailure) throw options.writeFailure;
            values.set(key, String(value));
        }),
    };
    const logger = { log: vi.fn(), warn: vi.fn() };
    const onChange = options.onChange || vi.fn();
    const button = dom.window.document.getElementById('furigana');
    const icon = dom.window.document.getElementById('icon');
    const controller = FuriganaToggleController.create({
        storage,
        button,
        icon,
        t: (key) => `label:${key}`,
        console: logger,
        onChange,
    });
    return {
        button,
        controller,
        icon,
        logger,
        onChange,
        storage,
        values,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('FuriganaToggleController stored state and display', () => {
    it.each([
        ['true', true],
        ['false', false],
    ])('initializes a stored %s preference', (storedValue, expected) => {
        const page = createHarness({ storedValue });

        expect(page.storage.getItem).toHaveBeenCalledWith('furiganaEnabled');
        expect(page.controller.isEnabled()).toBe(expected);
    });

    it('falls back to disabled when sessionStorage cannot be read', () => {
        const failure = new Error('storage blocked');
        const page = createHarness({ readFailure: failure });

        expect(page.controller.isEnabled()).toBe(false);
        expect(page.logger.warn).toHaveBeenCalledWith(
            'Unable to access sessionStorage for furigana preference:', failure,
        );
    });

    it('keeps the active class and localized title in sync with state', () => {
        const page = createHarness({ storedValue: true });

        expect(page.controller.updateButton()).toBe(true);
        expect(page.button.classList.contains('active')).toBe(true);
        expect(page.button.title).toBe('label:furigana_on');

        expect(page.controller.toggle()).toBe(false);
        expect(page.button.classList.contains('active')).toBe(false);
        expect(page.button.title).toBe('label:furigana_off');
    });
});

describe('FuriganaToggleController toggling and lifecycle', () => {
    it('persists a click and invokes onChange after storage and button updates', () => {
        let page;
        const onChange = vi.fn((enabled) => {
            expect(enabled).toBe(true);
            expect(page.storage.setItem).toHaveBeenCalledWith('furiganaEnabled', true);
            expect(page.values.get('furiganaEnabled')).toBe('true');
            expect(page.controller.isEnabled()).toBe(true);
            expect(page.button.classList.contains('active')).toBe(true);
            expect(page.button.title).toBe('label:furigana_on');
        });
        page = createHarness({ onChange });
        page.controller.updateButton();
        page.controller.init();

        page.button.click();

        expect(onChange).toHaveBeenCalledOnce();
        expect(page.storage.setItem.mock.invocationCallOrder[0]).toBeLessThan(
            onChange.mock.invocationCallOrder[0],
        );
        expect(onChange.mock.invocationCallOrder[0]).toBeLessThan(
            page.logger.log.mock.invocationCallOrder[0],
        );
        expect(page.logger.log).toHaveBeenCalledWith('Furigana enabled');
    });

    it('keeps runtime and display state changed when persistence fails', () => {
        const failure = new Error('storage disabled');
        const page = createHarness({ writeFailure: failure });
        page.controller.updateButton();

        expect(page.controller.toggle()).toBe(true);
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.button.classList.contains('active')).toBe(true);
        expect(page.button.title).toBe('label:furigana_on');
        expect(page.onChange).toHaveBeenCalledWith(true);
        expect(page.logger.warn).toHaveBeenCalledWith(
            'Unable to persist furigana preference:', failure,
        );
    });

    it('binds and destroys the click listener idempotently', () => {
        const page = createHarness();

        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        page.button.click();
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.onChange).toHaveBeenCalledOnce();

        expect(page.controller.destroy()).toBe(true);
        expect(page.controller.destroy()).toBe(false);
        page.button.click();
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.onChange).toHaveBeenCalledOnce();
    });
});

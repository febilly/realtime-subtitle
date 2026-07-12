const { JSDOM } = require('jsdom');
const SettingsUI = require('../../static/js/settings-ui');
const ThemeController = require('../../static/js/theme-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><body><button id="toggle"><svg id="icon"><use></use></svg></button></body>');
    pages.push(dom);
    const { document } = dom.window;
    const values = new Map();
    if (options.savedTheme !== undefined) values.set('theme', options.savedTheme);
    const storage = {
        getItem: vi.fn((key) => values.get(key) ?? null),
        setItem: vi.fn((key, value) => values.set(key, String(value))),
    };
    const fetch = options.fetch || vi.fn(async () => ({ ok: true }));
    const setControlIcon = vi.fn();
    const settingsUi = SettingsUI.create({
        document,
        window: dom.window,
        storage,
    });
    const controller = ThemeController.create({
        settingsUi,
        fetch,
        storage,
        toggle: document.getElementById('toggle'),
        themeIcon: document.getElementById('icon'),
        setControlIcon,
    });
    return {
        controller,
        document,
        dom,
        fetch,
        setControlIcon,
        settingsUi,
        storage,
        toggle: document.getElementById('toggle'),
        values,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('ThemeController lifecycle and theme cycling', () => {
    it('validates its orchestration dependencies', () => {
        expect(() => ThemeController.create()).toThrow(
            'ThemeController.create requires settingsUi',
        );
        expect(() => ThemeController.create({
            settingsUi: { getAvailableThemes() {}, applyTheme() {} },
            fetch: vi.fn(),
            storage: { getItem() {} },
        })).toThrow('ThemeController.create requires a theme toggle');
    });

    it('initializes once, cycles dark/light, and removes its listener on destroy', () => {
        const page = createHarness({ savedTheme: 'light' });

        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        expect(page.controller.getCurrentTheme()).toBe('light');
        expect(page.document.body.classList.contains('dark-theme')).toBe(false);
        expect(page.setControlIcon).toHaveBeenLastCalledWith(
            page.document.getElementById('icon'), 'sun',
        );

        page.toggle.click();
        expect(page.controller.getCurrentTheme()).toBe('dark');
        expect(page.document.body.classList.contains('dark-theme')).toBe(true);
        expect(page.values.get('theme')).toBe('dark');

        expect(page.controller.destroy()).toBe(true);
        expect(page.controller.destroy()).toBe(false);
        page.toggle.click();
        expect(page.controller.getCurrentTheme()).toBe('dark');
    });

    it('preserves the existing disabled-chroma normalization at startup', () => {
        const page = createHarness({ savedTheme: 'chroma' });

        page.controller.init();
        expect(page.controller.getCurrentTheme()).toBe('dark');
        expect(page.values.get('theme')).toBe('dark');
        expect(page.fetch).not.toHaveBeenCalled();
    });
});

describe('ThemeController chroma and window-on-top synchronization', () => {
    it('adds chroma to the cycle and de-duplicates identical window state', () => {
        const page = createHarness({ savedTheme: 'dark' });
        page.controller.init();
        page.fetch.mockClear();

        expect(page.controller.setChromaEnabled(true)).toBe('dark');
        expect(page.controller.getAvailableThemes()).toEqual(['dark', 'light', 'chroma']);
        expect(page.fetch).toHaveBeenCalledTimes(1);
        expect(JSON.parse(page.fetch.mock.calls[0][1].body)).toEqual({ on_top: true });

        page.toggle.click();
        expect(page.controller.getCurrentTheme()).toBe('light');
        expect(page.fetch).toHaveBeenCalledTimes(1);

        page.toggle.click();
        expect(page.controller.getCurrentTheme()).toBe('chroma');
        expect(page.document.body.classList.contains('chroma-theme')).toBe(true);
        expect(page.fetch).toHaveBeenCalledTimes(2);
        expect(JSON.parse(page.fetch.mock.calls[1][1].body)).toEqual({ on_top: false });

        page.controller.setChromaEnabled(true);
        expect(page.fetch).toHaveBeenCalledTimes(2);
    });

    it('restores a saved chroma choice when the capability first becomes available', () => {
        const page = createHarness({ savedTheme: 'dark' });
        page.controller.init();
        page.values.set('theme', 'chroma');
        page.fetch.mockClear();

        expect(page.controller.setChromaEnabled(true)).toBe('chroma');
        expect(page.document.body.classList.contains('chroma-theme')).toBe(true);
        expect(page.fetch).toHaveBeenCalledOnce();
        expect(JSON.parse(page.fetch.mock.calls[0][1].body)).toEqual({ on_top: false });
    });

    it('falls back to dark when chroma is disabled with the existing sync semantics', () => {
        const page = createHarness({ savedTheme: 'dark' });
        page.controller.init();
        page.controller.setChromaEnabled(true);
        page.controller.apply('chroma');
        page.fetch.mockClear();

        expect(page.controller.setChromaEnabled(false)).toBe('dark');
        expect(page.document.body.classList.contains('dark-theme')).toBe(true);
        expect(page.controller.getAvailableThemes()).toEqual(['dark', 'light']);
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('swallows endpoint failures after recording the requested state', async () => {
        const failure = new Error('not supported');
        const page = createHarness({
            fetch: vi.fn(async () => { throw failure; }),
        });
        page.controller.init();

        await expect(page.controller.syncWindowOnTop('chroma')).resolves.toBe(true);
        await expect(page.controller.syncWindowOnTop('chroma')).resolves.toBe(false);
        expect(page.fetch).toHaveBeenCalledOnce();
        expect(page.controller.getDebugState().lastWindowOnTopState).toBe(false);
    });
});

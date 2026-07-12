const { JSDOM } = require('jsdom');
const MobileSafeAreaController = require('../../static/js/mobile-safe-area-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><button id="safe"><svg id="icon"></svg></button><main id="subtitles"></main>');
    pages.push(dom);
    const storage = {
        setItem: vi.fn(() => {
            if (options.storageFailure) throw options.storageFailure;
        }),
    };
    const settingsStore = {
        loadBottomSafeAreaEnabled: vi.fn(() => !!options.enabled),
    };
    const logger = { log: vi.fn(), warn: vi.fn() };
    const setControlIcon = vi.fn();
    const button = options.withoutButton ? null : dom.window.document.getElementById('safe');
    const icon = options.withoutIcon ? null : dom.window.document.getElementById('icon');
    const container = options.withoutContainer
        ? null
        : dom.window.document.getElementById('subtitles');
    const controller = MobileSafeAreaController.create({
        settingsStore,
        storage,
        button,
        icon,
        container,
        isMobile: options.isMobile,
        userAgent: options.userAgent,
        t: (key) => `label:${key}`,
        setControlIcon,
        console: logger,
    });
    return {
        button,
        container,
        controller,
        icon,
        logger,
        setControlIcon,
        settingsStore,
        storage,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('MobileSafeAreaController device policy', () => {
    it('recognizes supported mobile user agents', () => {
        expect(MobileSafeAreaController.isMobileUserAgent('Mozilla/5.0 (iPhone)')).toBe(true);
        expect(MobileSafeAreaController.isMobileUserAgent('Android Mobile')).toBe(true);
        expect(MobileSafeAreaController.isMobileUserAgent('Mozilla/5.0 (Windows NT 10.0)')).toBe(false);
    });

    it('validates state dependencies', () => {
        expect(() => MobileSafeAreaController.create({
            storage: { setItem() {} },
        })).toThrow('MobileSafeAreaController.create requires settingsStore');
    });

    it('hides the control and refuses state changes on desktop', () => {
        const page = createHarness({ isMobile: false, enabled: true });
        expect(page.controller.updateButton()).toBe(true);
        expect(page.button.style.display).toBe('none');
        expect(page.controller.apply()).toBe(false);
        expect(page.container.classList.contains('mobile-bottom-safe-area')).toBe(false);
        expect(page.controller.toggle()).toBe(false);
        expect(page.storage.setItem).not.toHaveBeenCalled();
    });
});

describe('MobileSafeAreaController mobile lifecycle', () => {
    it('renders a saved preference and toggles class, icon, persistence, and log', () => {
        const page = createHarness({ isMobile: true, enabled: false });
        expect(page.controller.updateButton()).toBe(true);
        expect(page.button.style.display).toBe('');
        expect(page.button.title).toBe('label:bottom_safe_area_off');
        expect(page.setControlIcon).toHaveBeenLastCalledWith(
            page.icon, 'arrow-down-to-line',
        );

        expect(page.controller.toggle()).toBe(true);
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.storage.setItem).toHaveBeenCalledWith('bottomSafeAreaEnabled', true);
        expect(page.container.classList.contains('mobile-bottom-safe-area')).toBe(true);
        expect(page.button.classList.contains('active')).toBe(true);
        expect(page.button.title).toBe('label:bottom_safe_area_on');
        expect(page.setControlIcon).toHaveBeenLastCalledWith(page.icon, 'arrow-up-from-line');
        expect(page.logger.log).toHaveBeenCalledWith('Mobile bottom safe area enabled');
    });

    it('applies an enabled saved preference immediately', () => {
        const page = createHarness({ isMobile: true, enabled: true });
        expect(page.controller.apply()).toBe(true);
        expect(page.container.classList.contains('mobile-bottom-safe-area')).toBe(true);
    });

    it('keeps the runtime state when persistence fails', () => {
        const failure = new Error('storage disabled');
        const page = createHarness({ isMobile: true, storageFailure: failure });
        expect(page.controller.toggle()).toBe(true);
        expect(page.controller.isEnabled()).toBe(true);
        expect(page.logger.warn).toHaveBeenCalledWith(
            'Unable to persist bottom safe area preference:', failure,
        );
        expect(page.container.classList.contains('mobile-bottom-safe-area')).toBe(true);
    });

    it('binds once and destroy removes the click listener', () => {
        const page = createHarness({ isMobile: true });
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        page.button.click();
        expect(page.controller.isEnabled()).toBe(true);
        page.controller.destroy();
        page.button.click();
        expect(page.controller.isEnabled()).toBe(true);
    });
});

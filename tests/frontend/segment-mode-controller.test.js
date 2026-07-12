const { JSDOM } = require('jsdom');
const SegmentModeController = require('../../static/js/segment-mode-controller');

const pages = [];

function response(body = {}, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: vi.fn(async () => body),
    };
}

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><button id="segment"></button>');
    pages.push(dom);
    const values = new Map();
    if (options.storedPreference !== undefined) {
        values.set('segmentMode', options.storedPreference);
    }
    const runtime = {
        lockManualControls: false,
        segmentModeSupported: true,
        ...(options.runtime || {}),
    };
    let translateMode = !!options.translateMode;
    const storage = {
        getItem: vi.fn((key) => values.get(key) ?? null),
        setItem: vi.fn((key, value) => values.set(key, String(value))),
    };
    const settingsStore = {
        loadSegmentMode: vi.fn(() => options.initialMode || 'punctuation'),
    };
    const fetch = options.fetch || vi.fn(async () => response());
    const logger = { error: vi.fn() };
    const renderPicker = vi.fn();
    const button = dom.window.document.getElementById('segment');
    const controller = SegmentModeController.create({
        fetch,
        storage,
        settingsStore,
        button,
        t: (key) => `label:${key}`,
        console: logger,
        getRuntimeState: () => runtime,
        isTranslateMode: () => translateMode,
        renderPicker,
    });
    return {
        button,
        controller,
        fetch,
        logger,
        renderPicker,
        runtime,
        setTranslateMode(value) { translateMode = !!value; },
        settingsStore,
        storage,
        values,
    };
}

async function flush() {
    await Promise.resolve();
    await Promise.resolve();
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('SegmentModeController state and presentation', () => {
    it('normalizes modes, loads a safe fallback, and validates dependencies', () => {
        expect(SegmentModeController.normalizeSegmentMode(' endpoint ')).toBe('endpoint');
        expect(SegmentModeController.normalizeSegmentMode('invalid')).toBeNull();
        const page = createHarness({ initialMode: 'invalid' });
        expect(page.controller.getMode()).toBe('punctuation');

        expect(() => SegmentModeController.create({
            fetch: vi.fn(), storage: { getItem() {}, setItem() {} }, settingsStore: {},
        })).toThrow('SegmentModeController.create requires settingsStore');
    });

    it('updates ordinary and translate-locked button labels', () => {
        const page = createHarness({ initialMode: 'endpoint' });
        expect(page.controller.updateButton()).toBe(true);
        expect(page.button.title).toBe('label:segment_endpoint');

        page.setTranslateMode(true);
        page.controller.updateButton();
        expect(page.button.title).toBe('label:segment_endpoint_no_translation');

        page.controller.handleBackendChanged({ mode: 'punctuation' });
        expect(page.button.title).toBe('label:segment_punctuation_no_translation');
        expect(page.controller.getAvailableModes()).toEqual(['endpoint', 'punctuation']);
    });

    it('binds the button once and removes the cycle listener on destroy', async () => {
        const page = createHarness({ initialMode: 'punctuation' });
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        page.button.click();
        await flush();
        expect(JSON.parse(page.fetch.mock.calls[0][1].body)).toEqual({ mode: 'translation' });

        page.fetch.mockClear();
        expect(page.controller.destroy()).toBe(true);
        page.button.click();
        expect(page.fetch).not.toHaveBeenCalled();
    });
});

describe('SegmentModeController backend mutations', () => {
    it('posts, persists, and re-renders a successful user change', async () => {
        const page = createHarness({ initialMode: 'punctuation' });

        await expect(page.controller.setMode('endpoint')).resolves.toBe(true);
        expect(page.fetch).toHaveBeenCalledWith('/segment-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'endpoint' }),
        });
        expect(page.controller.getMode()).toBe('endpoint');
        expect(page.values.get('segmentMode')).toBe('endpoint');
        expect(page.renderPicker).toHaveBeenCalledOnce();
    });

    it.each([
        [{ lockManualControls: true }, false, 'endpoint'],
        [{ segmentModeSupported: false }, false, 'endpoint'],
        [{}, true, 'translation'],
    ])('rejects disallowed changes for runtime=%o translate=%s', async (runtime, translate, mode) => {
        const page = createHarness({ runtime, translateMode: translate });
        await expect(page.controller.setMode(mode)).resolves.toBe(false);
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('reports non-OK and network failures without changing the mode', async () => {
        const nonOk = createHarness({
            initialMode: 'punctuation',
            fetch: vi.fn(async () => response({}, { ok: false })),
        });
        await expect(nonOk.controller.setMode('endpoint')).resolves.toBe(false);
        expect(nonOk.logger.error).toHaveBeenCalledWith('Failed to set segment mode');
        expect(nonOk.controller.getMode()).toBe('punctuation');

        const failure = new Error('offline');
        const network = createHarness({
            fetch: vi.fn(async () => { throw failure; }),
        });
        await expect(network.controller.setMode('endpoint')).resolves.toBe(false);
        expect(network.logger.error).toHaveBeenCalledWith(
            'Error setting segment mode:', failure,
        );
    });

    it('forces translation segmentation to punctuation at the mode boundary', async () => {
        const page = createHarness({ initialMode: 'translation', translateMode: true });

        expect(page.controller.enforceTranslateMode()).toBe(true);
        expect(page.controller.getMode()).toBe('punctuation');
        expect(page.values.get('segmentMode')).toBe('punctuation');
        await flush();
        expect(JSON.parse(page.fetch.mock.calls[0][1].body)).toEqual({ mode: 'punctuation' });
        expect(page.controller.enforceTranslateMode()).toBe(false);
    });

    it.each([
        ['locked backend wins', { lockManualControls: true }, 'endpoint', 'translation', 'translation', 0],
        ['stored preference wins', {}, 'endpoint', 'punctuation', 'endpoint', 1],
        ['backend fills empty storage', {}, undefined, 'endpoint', 'endpoint', 0],
        ['invalid values keep initial state', {}, 'invalid', 'invalid', 'punctuation', 0],
    ])('%s in UI-config priority resolution', async (
        _label, runtime, storedPreference, backendMode, expected, expectedPosts,
    ) => {
        const page = createHarness({ runtime, storedPreference, initialMode: 'punctuation' });

        expect(page.controller.applyBackendConfig({ segment_mode: backendMode })).toBe(expected);
        await flush();
        expect(page.controller.getMode()).toBe(expected);
        expect(page.fetch).toHaveBeenCalledTimes(expectedPosts);
    });

    it('accepts valid WebSocket state and ignores malformed frames', () => {
        const page = createHarness({ initialMode: 'punctuation' });
        expect(page.controller.handleBackendChanged(null)).toBe(false);
        expect(page.controller.handleBackendChanged({ mode: 'endpoint' })).toBe(true);
        expect(page.controller.getMode()).toBe('endpoint');
        expect(page.values.get('segmentMode')).toBe('endpoint');
        expect(page.renderPicker).toHaveBeenCalledOnce();
    });
});

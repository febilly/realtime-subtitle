const { JSDOM } = require('jsdom');
const SpeakerLabelController = require('../../static/js/speaker-label-controller');

const pages = [];

function response(body = {}, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: vi.fn(async () => body),
    };
}

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><main id="subtitles"></main>');
    pages.push(dom);
    const runtime = {
        lockManualControls: false,
        translationProvider: 'soniox',
        ...(options.runtime || {}),
    };
    const fetch = options.fetch || vi.fn(async () => response(options.responseBody || {}));
    const renderPicker = vi.fn();
    const renderSubtitles = vi.fn();
    const logger = { error: vi.fn() };
    const container = dom.window.document.getElementById('subtitles');
    const controller = SpeakerLabelController.create({
        fetch,
        container,
        console: logger,
        getRuntimeState: () => runtime,
        getStoredPreference: () => options.storedPreference ?? null,
        renderPicker,
        renderSubtitles,
    });
    return {
        container,
        controller,
        fetch,
        logger,
        renderPicker,
        renderSubtitles,
        runtime,
    };
}

async function flush() {
    await Promise.resolve();
    await Promise.resolve();
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('SpeakerLabelController state and backend writes', () => {
    it('validates dependencies and owns the visibility class', () => {
        expect(() => SpeakerLabelController.create({ fetch: vi.fn() })).toThrow(
            'SpeakerLabelController.create requires a container',
        );
        const page = createHarness();
        expect(page.controller.getState()).toEqual({
            diarizationEnabled: true,
            labelsHidden: false,
        });
        expect(page.controller.applyVisibility()).toBe(false);
        expect(page.container.classList.contains('hide-speaker-labels')).toBe(false);
        page.controller.handleBackendChanged({ hide_speaker_labels: true });
        expect(page.container.classList.contains('hide-speaker-labels')).toBe(true);
    });

    it('rejects user writes while manual controls are locked', async () => {
        const page = createHarness({ runtime: { lockManualControls: true } });
        await expect(page.controller.setHidden(true)).resolves.toBe(false);
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('uses the authoritative response and refreshes picker and subtitles', async () => {
        const page = createHarness({
            fetch: vi.fn(async () => response({ hide_speaker_labels: false })),
        });
        await expect(page.controller.setHidden(true)).resolves.toBe(true);
        expect(page.fetch).toHaveBeenCalledWith('/speaker-labels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hide_speaker_labels: true }),
        });
        expect(page.controller.isHidden()).toBe(false);
        expect(page.renderPicker).toHaveBeenCalledOnce();
        expect(page.renderSubtitles).toHaveBeenCalledOnce();

        page.fetch.mockResolvedValueOnce(response({}));
        await page.controller.setHidden(true);
        expect(page.controller.isHidden()).toBe(true);
        expect(page.container.classList.contains('hide-speaker-labels')).toBe(true);
    });

    it('reports non-OK and network errors without mutating state', async () => {
        const nonOk = createHarness({
            fetch: vi.fn(async () => response({}, { ok: false })),
        });
        await expect(nonOk.controller.setHidden(true)).resolves.toBe(false);
        expect(nonOk.logger.error).toHaveBeenCalledWith('Failed to set speaker labels');
        expect(nonOk.controller.isHidden()).toBe(false);

        const failure = new Error('offline');
        const network = createHarness({
            fetch: vi.fn(async () => { throw failure; }),
        });
        await expect(network.controller.setHidden(true)).resolves.toBe(false);
        expect(network.logger.error).toHaveBeenCalledWith(
            'Error setting speaker labels:', failure,
        );
    });
});

describe('SpeakerLabelController config and WebSocket reconciliation', () => {
    it.each([
        ['unlocked soniox', { lockManualControls: false, translationProvider: 'soniox' }, false, false, 1],
        ['locked soniox', { lockManualControls: true, translationProvider: 'soniox' }, false, true, 0],
        ['unlocked gemini', { lockManualControls: false, translationProvider: 'gemini' }, false, true, 0],
        ['no stored value', { lockManualControls: false, translationProvider: 'soniox' }, null, true, 0],
    ])('applies the stored-preference matrix: %s', async (
        _label, runtime, storedPreference, expectedHidden, expectedPosts,
    ) => {
        const page = createHarness({ runtime, storedPreference });
        expect(page.controller.applyBackendConfig({
            speaker_diarization_enabled: false,
            hide_speaker_labels: true,
        })).toEqual({
            diarizationEnabled: false,
            labelsHidden: expectedHidden,
        });
        await flush();
        expect(page.controller.isHidden()).toBe(expectedHidden);
        expect(page.fetch).toHaveBeenCalledTimes(expectedPosts);
        expect(page.container.classList.contains('hide-speaker-labels')).toBe(expectedHidden);
    });

    it('supports both WebSocket payload shapes and still refreshes malformed frames', () => {
        const page = createHarness();
        expect(page.controller.handleBackendChanged({ enabled: false })).toBe(true);
        expect(page.controller.handleBackendChanged({ enabled: true })).toBe(false);
        expect(page.controller.handleBackendChanged({})).toBe(false);
        expect(page.renderPicker).toHaveBeenCalledTimes(3);
        expect(page.renderSubtitles).toHaveBeenCalledTimes(3);
    });
});

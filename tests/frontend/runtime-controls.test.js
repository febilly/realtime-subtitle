const { JSDOM } = require('jsdom');
const RuntimeControls = require('../../static/js/runtime-controls');

function jsonResponse(data, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: vi.fn().mockResolvedValue(data),
    };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <button id="display"></button>
        <button id="pause"><svg id="pauseIcon"></svg></button>
        <button id="audio"><svg id="audioIcon"></svg></button>
        <button id="overlay"></button>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const state = {
        displayMode: 'both',
        isPaused: false,
        audioSource: 'system',
        overlayOpen: false,
        lockManualControls: false,
        ...(overrides.state || {}),
    };
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(jsonResponse({ open: false, available: true }));
    const setControlIcon = vi.fn();
    const renderSubtitles = vi.fn();
    const sessionCostPause = vi.fn();
    const logger = { log: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const controller = RuntimeControls.create({
        elements: {
            displayModeButton: document.getElementById('display'),
            pauseButton: document.getElementById('pause'),
            pauseIcon: document.getElementById('pauseIcon'),
            audioSourceButton: document.getElementById('audio'),
            audioSourceIcon: document.getElementById('audioIcon'),
            overlayButton: document.getElementById('overlay'),
        },
        getState: () => state,
        updateState: (patch) => Object.assign(state, patch),
        fetch,
        storage: dom.window.localStorage,
        t: (key, params) => params ? `${key}:${params.current}>${params.next}` : key,
        setControlIcon,
        renderSubtitles,
        sessionCostPause,
        console: logger,
    });
    return {
        dom, document, state, fetch, setControlIcon, renderSubtitles,
        sessionCostPause, logger, controller,
    };
}

describe('RuntimeControls', () => {
    it('normalizes and cycles display/audio modes in the existing order', () => {
        expect(RuntimeControls.getNextDisplayMode('both')).toBe('original');
        expect(RuntimeControls.getNextDisplayMode('original')).toBe('translation');
        expect(RuntimeControls.getNextDisplayMode('translation')).toBe('both');
        expect(RuntimeControls.getNextDisplayMode('bogus')).toBe('both');
        expect(RuntimeControls.normalizeAudioSource(' MIX ')).toBe('mix');
        expect(RuntimeControls.normalizeAudioSource('bogus')).toBe('system');
        expect(RuntimeControls.getNextAudioSource('system')).toBe('microphone');
        expect(RuntimeControls.getNextAudioSource('microphone')).toBe('mix');
        expect(RuntimeControls.getNextAudioSource('mix')).toBe('system');
    });

    it('cycles display mode, persists it, updates title, and rerenders', () => {
        const page = setup();
        page.controller.init({ refreshOverlay: false });
        const button = page.document.getElementById('display');
        button.click();
        expect(page.state.displayMode).toBe('original');
        expect(page.dom.window.localStorage.getItem('displayMode')).toBe('original');
        expect(button.title).toBe('display_mode_format:display_mode_original>display_mode_translation');
        button.click();
        button.click();
        expect(page.state.displayMode).toBe('both');
        expect(page.renderSubtitles).toHaveBeenCalledTimes(3);
        page.dom.window.close();
    });

    it('blocks pause, audio source, and overlay endpoints while manual controls are locked', async () => {
        const page = setup({ state: { lockManualControls: true } });
        page.controller.init({ refreshOverlay: false });
        page.document.getElementById('pause').click();
        page.document.getElementById('audio').click();
        page.document.getElementById('overlay').click();
        await Promise.resolve();
        expect(page.fetch).not.toHaveBeenCalled();
        expect(page.state).toMatchObject({ isPaused: false, audioSource: 'system', overlayOpen: false });
        page.dom.window.close();
    });

    it('updates pause state only after successful pause/resume endpoints', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(jsonResponse({}))
            .mockResolvedValueOnce(jsonResponse({}, { ok: false, status: 503 }))
            .mockResolvedValueOnce(jsonResponse({}));
        const page = setup({ fetch });
        expect(await page.controller.togglePause()).toBe(true);
        expect(fetch).toHaveBeenNthCalledWith(1, '/pause', { method: 'POST' });
        expect(page.state.isPaused).toBe(true);
        expect(page.sessionCostPause).toHaveBeenCalledOnce();
        expect(await page.controller.togglePause()).toBe(false);
        expect(page.state.isPaused).toBe(true);
        expect(await page.controller.togglePause()).toBe(true);
        expect(fetch).toHaveBeenNthCalledWith(3, '/resume', { method: 'POST' });
        expect(page.state.isPaused).toBe(false);
        page.dom.window.close();
    });

    it('updates audio source only when the endpoint returns an accepted source', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ source: 'microphone', message: 'changed' }))
            .mockResolvedValueOnce(jsonResponse({ message: 'rejected' }, { ok: false, status: 409 }));
        const page = setup({ fetch });
        expect(await page.controller.switchAudioSource()).toBe(true);
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ source: 'microphone' });
        expect(page.state.audioSource).toBe('microphone');
        expect(page.dom.window.localStorage.getItem('audioSource')).toBe('microphone');
        expect(page.setControlIcon).toHaveBeenLastCalledWith(page.document.getElementById('audioIcon'), 'mic');
        expect(await page.controller.switchAudioSource()).toBe(false);
        expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ source: 'mix' });
        expect(page.state.audioSource).toBe('microphone');
        expect(page.logger.error).toHaveBeenCalledWith('Failed to switch audio source:', 'rejected');
        page.dom.window.close();
    });

    it('loads stored audio source first and then synchronizes the server value', async () => {
        const fetch = vi.fn().mockResolvedValue(jsonResponse({ source: 'mix' }));
        const page = setup({ fetch });
        page.dom.window.localStorage.setItem('audioSource', 'microphone');
        expect(await page.controller.fetchInitialAudioSource()).toBe(true);
        expect(fetch).toHaveBeenCalledWith('/audio-source');
        expect(page.state.audioSource).toBe('mix');
        expect(page.dom.window.localStorage.getItem('audioSource')).toBe('mix');
        page.dom.window.close();
    });

    it('synchronizes overlay frames and handles available/unavailable endpoint results', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ available: true, open: true }))
            .mockResolvedValueOnce(jsonResponse({ available: false }));
        const page = setup({ fetch });
        page.controller.syncOverlayState(true);
        const button = page.document.getElementById('overlay');
        expect(page.state.overlayOpen).toBe(true);
        expect(button.classList.contains('active')).toBe(true);
        expect(button.title).toBe('overlay_close');
        expect(await page.controller.toggleOverlay()).toBe(true);
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ action: 'toggle' });
        expect(page.state.overlayOpen).toBe(true);
        expect(await page.controller.refreshOverlayState()).toBe(false);
        expect(button.style.display).toBe('none');
        page.controller.syncOverlayState(false);
        expect(button.classList.contains('active')).toBe(false);
        expect(button.title).toBe('overlay_open');
        page.dom.window.close();
    });

    it('synchronizes recognition pause frames without calling an endpoint', () => {
        const page = setup();
        page.controller.syncPauseState(true);
        expect(page.state.isPaused).toBe(true);
        expect(page.document.getElementById('pause').classList.contains('is-paused')).toBe(true);
        expect(page.document.getElementById('pause').title).toBe('resume');
        expect(page.fetch).not.toHaveBeenCalled();
        page.dom.window.close();
    });
});

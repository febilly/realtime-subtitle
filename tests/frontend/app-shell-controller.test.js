const { JSDOM } = require('jsdom');
const AppShellController = require('../../static/js/app-shell-controller');

const pages = [];

function createHarness(options = {}) {
    const dom = new JSDOM(`<!doctype html><html><body>
        <button id="theme"></button>
        <button id="restart"></button>
        <button id="pause"></button>
        <button id="audio"></button>
        <button id="overlay"></button>
        <button id="settings"></button>
        <button id="translation"></button>
        <button id="segment"></button>
        <button id="osc"></button>
        <button id="ipc"></button>
        <main id="subtitles"><div class="empty-state">old</div></main>
    </body></html>`);
    pages.push(dom);
    const { document } = dom.window;
    const byId = (id) => document.getElementById(id);
    const state = {
        lockManualControls: false,
        segmentModeSupported: true,
        autoRestartEnabled: false,
        ...(options.state || {}),
    };
    const events = [];
    const updateState = vi.fn((patch) => {
        events.push(`state:${JSON.stringify(patch)}`);
        Object.assign(state, patch);
    });
    const actions = {
        updatePauseButtonUi: vi.fn(() => events.push('pause')),
        updateOverlayButton: vi.fn(() => events.push('overlay')),
        updateAutoRestartButton: vi.fn(() => {
            events.push(`auto:${state.autoRestartEnabled}`);
        }),
    };
    const elements = options.withoutElements ? {} : {
        themeToggle: byId('theme'),
        restartButton: byId('restart'),
        pauseButton: byId('pause'),
        audioSourceButton: byId('audio'),
        overlayButton: byId('overlay'),
        settingsButton: byId('settings'),
        translationLangButton: byId('translation'),
        segmentModeButton: byId('segment'),
        oscTranslationButton: byId('osc'),
        subtitleContainer: byId('subtitles'),
        ipcStatusButton: byId('ipc'),
    };
    const controller = AppShellController.create({
        document,
        window: options.window || { I18N: { lang: options.lang || 'ja' } },
        t: (key) => `label:${key}`,
        elements,
        getState: () => state,
        updateState,
        actions,
    });
    return {
        actions,
        controller,
        document,
        elements,
        events,
        state,
        updateState,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('AppShellController static presentation', () => {
    it('applies document language, control titles, delegated state labels, and empty text', () => {
        const page = createHarness({ lang: 'ja' });

        page.controller.applyStaticText();

        expect(page.document.documentElement.lang).toBe('ja');
        expect(page.elements.themeToggle.title).toBe('label:theme_toggle');
        expect(page.elements.restartButton.title).toBe('label:restart');
        expect(page.elements.translationLangButton.title).toBe('label:translation_language');
        expect(page.elements.settingsButton.title).toBe('label:settings');
        expect(page.elements.subtitleContainer.querySelector('.empty-state').textContent)
            .toBe('label:empty_state');
        expect(page.actions.updatePauseButtonUi).toHaveBeenCalledOnce();
        expect(page.actions.updateOverlayButton).toHaveBeenCalledOnce();
    });

    it('falls back to English when the i18n language is absent', () => {
        const page = createHarness({ window: {} });

        page.controller.applyStaticText();

        expect(page.document.documentElement.lang).toBe('en');
    });

    it('tolerates missing optional shell elements', () => {
        const page = createHarness({ withoutElements: true });

        expect(() => page.controller.applyStaticText()).not.toThrow();
        expect(() => page.controller.applyManualControlPolicy()).not.toThrow();
        expect(page.controller.syncIpcStatus(true)).toBe(false);
    });
});

describe('AppShellController manual-control policy and IPC status', () => {
    const manualControlKeys = [
        'restartButton',
        'pauseButton',
        'audioSourceButton',
        'oscTranslationButton',
        'translationLangButton',
    ];

    it('shows supported unlocked controls and refreshes auto-restart presentation', () => {
        const page = createHarness();

        page.controller.applyManualControlPolicy();

        for (const key of manualControlKeys) {
            expect(page.elements[key].style.display).toBe('');
        }
        expect(page.elements.segmentModeButton.style.display).toBe('');
        expect(page.updateState).not.toHaveBeenCalled();
        expect(page.actions.updateAutoRestartButton).toHaveBeenCalledOnce();
    });

    it('hides locked controls and enables auto restart before refreshing its button', () => {
        const page = createHarness({
            state: { lockManualControls: true, autoRestartEnabled: false },
        });

        page.controller.applyManualControlPolicy();

        for (const key of manualControlKeys) {
            expect(page.elements[key].style.display).toBe('none');
        }
        expect(page.elements.segmentModeButton.style.display).toBe('none');
        expect(page.updateState).toHaveBeenCalledWith({ autoRestartEnabled: true });
        expect(page.state.autoRestartEnabled).toBe(true);
        expect(page.events).toEqual([
            'state:{"autoRestartEnabled":true}',
            'auto:true',
        ]);
    });

    it('hides only the unsupported segment-mode control while unlocked', () => {
        const page = createHarness({ state: { segmentModeSupported: false } });

        page.controller.applyManualControlPolicy();

        expect(page.elements.segmentModeButton.style.display).toBe('none');
        for (const key of manualControlKeys) {
            expect(page.elements[key].style.display).toBe('');
        }
    });

    it('synchronizes IPC visibility and connection class in both directions', () => {
        const page = createHarness();

        expect(page.controller.syncIpcStatus(true)).toBe(true);
        expect(page.elements.ipcStatusButton.style.display).toBe('flex');
        expect(page.elements.ipcStatusButton.classList.contains('ipc-connected')).toBe(true);

        expect(page.controller.syncIpcStatus(false)).toBe(true);
        expect(page.elements.ipcStatusButton.style.display).toBe('none');
        expect(page.elements.ipcStatusButton.classList.contains('ipc-connected')).toBe(false);
    });
});

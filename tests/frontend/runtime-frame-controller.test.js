const RuntimeFrameController = require('../../static/js/runtime-frame-controller');

const ACTION_NAMES = [
    'applyBundledCjkFontPreference',
    'syncOverlayState',
    'syncIpcStatus',
    'displayErrorMessage',
    'openSettings',
    'handleApiKeyFailure',
    'addLlmCost',
    'setTranslationUiMode',
    'renderTranslationModePicker',
    'showToast',
    'restartRecognition',
    'handleSegmentModeChanged',
    'handleSpeakerLabelsChanged',
];

function createHarness(options = {}) {
    const state = { lockManualControls: false, ...(options.state || {}) };
    const events = [];
    const actions = Object.fromEntries(ACTION_NAMES.map((name) => [
        name,
        vi.fn((...args) => events.push(`${name}:${JSON.stringify(args)}`)),
    ]));
    Object.assign(actions, options.actions || {});
    const controller = RuntimeFrameController.create({
        t: (key) => `label:${key}`,
        getState: () => state,
        actions,
    });
    return { actions, controller, events, state };
}

describe('RuntimeFrameController frame boundary', () => {
    it('exports exactly the runtime-owned frame types', () => {
        expect(RuntimeFrameController.FRAME_TYPES).toEqual([
            'subtitle_font_preference',
            'overlay_visibility',
            'ipc_status',
            'error',
            'llm_cost',
            'translation_mode_fallback',
            'segment_mode_changed',
            'speaker_labels_changed',
        ]);
        expect(Object.isFrozen(RuntimeFrameController.FRAME_TYPES)).toBe(true);
    });

    it('declines frames owned by other controllers', () => {
        const page = createHarness();

        expect(page.controller.handle({ type: 'update' })).toBe(false);
        expect(page.events).toEqual([]);
    });
});

describe('RuntimeFrameController direct runtime delegation', () => {
    it.each([
        [true, true],
        [0, false],
    ])('persists normalized subtitle font preference %p', (input, expected) => {
        const page = createHarness();

        expect(page.controller.handle({
            type: 'subtitle_font_preference',
            use_bundled_cjk_fonts: input,
        })).toBe(true);
        expect(page.actions.applyBundledCjkFontPreference).toHaveBeenCalledWith(
            expected, { persist: true },
        );
    });

    it('delegates overlay and IPC state values unchanged', () => {
        const page = createHarness();

        page.controller.handle({ type: 'overlay_visibility', visible: 'shown' });
        page.controller.handle({ type: 'ipc_status', connected: 1 });

        expect(page.actions.syncOverlayState).toHaveBeenCalledWith('shown');
        expect(page.actions.syncIpcStatus).toHaveBeenCalledWith(1);
    });

    it('always displays errors and force-opens settings only for an unlocked API-key error', () => {
        const unlocked = createHarness();
        const frame = { type: 'error', code: 'api_key', message: 'bad key' };

        expect(unlocked.controller.handle(frame)).toBe(true);
        expect(unlocked.actions.displayErrorMessage).toHaveBeenCalledWith('bad key');
        expect(unlocked.actions.openSettings).toHaveBeenCalledWith({ forced: true });

        const locked = createHarness({ state: { lockManualControls: true } });
        locked.controller.handle(frame);
        expect(locked.actions.displayErrorMessage).toHaveBeenCalledWith('bad key');
        expect(locked.actions.openSettings).not.toHaveBeenCalled();

        const other = createHarness();
        other.controller.handle({ type: 'error', code: 'network', message: 'offline' });
        expect(other.actions.openSettings).not.toHaveBeenCalled();
    });

    it('lets a recoverable API-key failure bypass the error overlay and forced settings', () => {
        const handleApiKeyFailure = vi.fn(() => true);
        const page = createHarness({ actions: { handleApiKeyFailure } });
        const frame = { type: 'error', code: 'api_key', message: 'temporary session expired' };

        expect(page.controller.handle(frame)).toBe(true);
        expect(handleApiKeyFailure).toHaveBeenCalledWith(frame);
        expect(page.actions.displayErrorMessage).not.toHaveBeenCalled();
        expect(page.actions.openSettings).not.toHaveBeenCalled();
    });

    it('forwards LLM cost credits to hosted balance', () => {
        const page = createHarness();

        expect(page.controller.handle({ type: 'llm_cost', credits: 1.25 })).toBe(true);
        expect(page.actions.addLlmCost).toHaveBeenCalledWith(1.25);
    });

    it('applies translation fallback in persistence, UI, toast, then optional restart order', () => {
        const page = createHarness();

        expect(page.controller.handle({
            type: 'translation_mode_fallback',
            needs_restart: true,
        })).toBe(true);

        expect(page.actions.setTranslationUiMode).toHaveBeenCalledWith(
            'fast', { restartIfNeeded: false },
        );
        expect(page.actions.renderTranslationModePicker).toHaveBeenCalledOnce();
        expect(page.actions.showToast).toHaveBeenCalledWith(
            'label:translation_mode_fallback_toast', false,
        );
        expect(page.actions.restartRecognition).toHaveBeenCalledWith({ auto: true });
        expect(page.events.map((event) => event.split(':')[0])).toEqual([
            'setTranslationUiMode',
            'renderTranslationModePicker',
            'showToast',
            'restartRecognition',
        ]);

        const noRestart = createHarness();
        noRestart.controller.handle({ type: 'translation_mode_fallback' });
        expect(noRestart.actions.restartRecognition).not.toHaveBeenCalled();
    });

    it('forwards segment and speaker label frames to their state owners', () => {
        const page = createHarness();
        const segmentFrame = { type: 'segment_mode_changed', mode: 'endpoint' };
        const speakerFrame = { type: 'speaker_labels_changed', enabled: false };

        page.controller.handle(segmentFrame);
        page.controller.handle(speakerFrame);

        expect(page.actions.handleSegmentModeChanged).toHaveBeenCalledWith(segmentFrame);
        expect(page.actions.handleSpeakerLabelsChanged).toHaveBeenCalledWith(speakerFrame);
    });
});

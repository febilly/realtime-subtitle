const SubtitleRuntimeController = require('../../static/js/subtitle-runtime-controller');

function setup(overrides = {}) {
    const events = [];
    const state = {
        translateMode: false,
        translationUiMode: 'hybrid',
        ...(overrides.state || {}),
    };
    const session = overrides.session || {
        finalizeCurrentNonFinalTokens: vi.fn(() => ({ changed: true })),
        clear: vi.fn((options) => events.push(['session.clear', options])),
    };
    const renderer = overrides.renderer || {
        render: vi.fn(() => {
            events.push(['renderer.render']);
            return 'rendered';
        }),
        invalidateAll: vi.fn(() => events.push(['renderer.invalidateAll'])),
        clearSession: vi.fn(() => events.push(['renderer.clearSession'])),
    };
    const controller = SubtitleRuntimeController.create({
        session,
        renderer,
        getState: () => state,
    });
    return { controller, events, renderer, session, state };
}

describe('SubtitleRuntimeController dependencies and rendering', () => {
    it('requires the session and renderer boundaries', () => {
        expect(() => SubtitleRuntimeController.create({
            renderer: { render() {} },
        })).toThrow('SubtitleRuntimeController requires a subtitle session');
        expect(() => SubtitleRuntimeController.create({
            session: { finalizeCurrentNonFinalTokens() {} },
        })).toThrow('SubtitleRuntimeController requires a subtitle renderer');
    });

    it('returns the renderer result through the stable render port', () => {
        const env = setup();
        expect(env.controller.render()).toBe('rendered');
        expect(env.renderer.render).toHaveBeenCalledOnce();
    });
});

describe('SubtitleRuntimeController session coordination', () => {
    it('does nothing when finalization reports no state change', () => {
        const env = setup({
            session: {
                finalizeCurrentNonFinalTokens: vi.fn(() => ({ changed: false })),
                clear: vi.fn(),
            },
        });
        expect(env.controller.finalize()).toBe(false);
        expect(env.renderer.invalidateAll).not.toHaveBeenCalled();
        expect(env.renderer.render).not.toHaveBeenCalled();
    });

    it('invalidates before rendering a changed finalization and supports deferred render', () => {
        const env = setup();
        expect(env.controller.finalize()).toBe(true);
        expect(env.events).toEqual([
            ['renderer.invalidateAll'],
            ['renderer.render'],
        ]);

        env.events.length = 0;
        expect(env.controller.finalize({ render: false })).toBe(true);
        expect(env.events).toEqual([['renderer.invalidateAll']]);
    });

    it('clears the model with current mode state before resetting renderer caches', () => {
        const env = setup({ state: { translateMode: true, translationUiMode: 'accurate' } });
        env.controller.clear();
        expect(env.events).toEqual([
            ['session.clear', { translateMode: true, translationUiMode: 'accurate' }],
            ['renderer.clearSession'],
        ]);

        env.state.translationUiMode = 'fast';
        env.controller.clear();
        expect(env.session.clear).toHaveBeenLastCalledWith({
            translateMode: true,
            translationUiMode: 'fast',
        });
    });
});

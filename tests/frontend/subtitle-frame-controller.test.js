const SubtitleFrameController = require('../../static/js/subtitle-frame-controller');

function createHarness(options = {}) {
    const session = {
        markSpecPending: vi.fn(() => ({ changed: true })),
        applySpecTranslation: vi.fn(() => ({ changed: true })),
        applyRefineResult: vi.fn(() => ({ changed: true })),
        retract: vi.fn(() => ({ removed: true })),
        revealPendingForRestart: vi.fn(() => ({
            translateChanged: false,
            hybridChanged: false,
        })),
        applyUpdateFrame: vi.fn(() => ({ changed: true })),
        ...(options.session || {}),
    };
    const renderer = {
        invalidateAll: vi.fn(),
        ...(options.renderer || {}),
    };
    const logger = { log: vi.fn() };
    const renderSubtitles = vi.fn();
    const finalizeCurrentNonFinalTokens = vi.fn();
    const clearSubtitleState = vi.fn();
    const state = { translateMode: true, ...(options.state || {}) };
    const controller = SubtitleFrameController.create({
        session,
        renderer,
        console: logger,
        getState: () => state,
        renderSubtitles,
        finalizeCurrentNonFinalTokens,
        clearSubtitleState,
    });
    return {
        clearSubtitleState,
        controller,
        finalizeCurrentNonFinalTokens,
        logger,
        renderer,
        renderSubtitles,
        session,
        state,
    };
}

describe('SubtitleFrameController frame boundary', () => {
    it('exports exactly the subtitle-owned frame types and validates collaborators', () => {
        expect(SubtitleFrameController.FRAME_TYPES).toEqual([
            'spec_translation_pending',
            'spec_translation',
            'refine_result',
            'subtitle_retract',
            'clear',
            'update',
        ]);
        expect(Object.isFrozen(SubtitleFrameController.FRAME_TYPES)).toBe(true);
        expect(() => SubtitleFrameController.create({ renderer: {} }))
            .toThrow('SubtitleFrameController.create requires a session');
        expect(() => SubtitleFrameController.create({ session: {} }))
            .toThrow('SubtitleFrameController.create requires a renderer');
    });

    it('declines frames owned by other controllers without side effects', () => {
        const page = createHarness();

        expect(page.controller.handle({ type: 'llm_cost', credits: 3 })).toBe(false);

        for (const method of Object.values(page.session)) {
            expect(method).not.toHaveBeenCalled();
        }
        expect(page.renderSubtitles).not.toHaveBeenCalled();
    });
});

describe('SubtitleFrameController speculative and refine frames', () => {
    it('normalizes a pending source and renders only when session state changes', () => {
        const page = createHarness();

        expect(page.controller.handle({
            type: 'spec_translation_pending',
            source: '  Hello.  ',
            target_lang: 'zh',
        })).toBe(true);
        expect(page.session.markSpecPending).toHaveBeenCalledWith('Hello.', 'zh');
        expect(page.renderSubtitles).toHaveBeenCalledOnce();

        page.session.markSpecPending.mockReturnValueOnce({ changed: false });
        page.controller.handle({ type: 'spec_translation_pending', source: '' });
        expect(page.session.markSpecPending).toHaveBeenLastCalledWith('', undefined);
        expect(page.renderSubtitles).toHaveBeenCalledOnce();
    });

    it('applies speculative translations and renders only on change', () => {
        const page = createHarness();
        const frame = {
            type: 'spec_translation',
            source: 'Hello.',
            translation: '你好。',
            target_lang: 'zh',
        };

        expect(page.controller.handle(frame)).toBe(true);
        expect(page.session.applySpecTranslation).toHaveBeenCalledWith(frame);
        expect(page.renderSubtitles).toHaveBeenCalledOnce();

        page.session.applySpecTranslation.mockReturnValueOnce({ changed: false });
        page.controller.handle(frame);
        expect(page.renderSubtitles).toHaveBeenCalledOnce();
    });

    it('passes current translate mode to refine and renders only on change', () => {
        const page = createHarness({ state: { translateMode: false } });
        const frame = { type: 'refine_result', sentence_id: 's1' };

        expect(page.controller.handle(frame)).toBe(true);
        expect(page.session.applyRefineResult).toHaveBeenCalledWith(frame, {
            translateMode: false,
        });
        expect(page.renderSubtitles).toHaveBeenCalledOnce();

        page.session.applyRefineResult.mockReturnValueOnce({ changed: false });
        page.controller.handle(frame);
        expect(page.renderSubtitles).toHaveBeenCalledOnce();
    });
});

describe('SubtitleFrameController retract, clear, and update frames', () => {
    it('invalidates and renders only when a trimmed sentence id is removed', () => {
        const page = createHarness();

        expect(page.controller.handle({
            type: 'subtitle_retract',
            sentence_id: '  gone  ',
        })).toBe(true);
        expect(page.session.retract).toHaveBeenCalledWith('gone');
        expect(page.renderer.invalidateAll).toHaveBeenCalledOnce();
        expect(page.renderSubtitles).toHaveBeenCalledOnce();

        page.session.retract.mockReturnValueOnce({ removed: false });
        page.controller.handle({ type: 'subtitle_retract' });
        expect(page.session.retract).toHaveBeenLastCalledWith('');
        expect(page.renderer.invalidateAll).toHaveBeenCalledOnce();
        expect(page.renderSubtitles).toHaveBeenCalledOnce();
    });

    it('clears the session without rendering when existing content is not preserved', () => {
        const page = createHarness();

        expect(page.controller.handle({ type: 'clear' })).toBe(true);

        expect(page.logger.log).toHaveBeenCalledWith('Clearing all subtitles...');
        expect(page.clearSubtitleState).toHaveBeenCalledOnce();
        expect(page.finalizeCurrentNonFinalTokens).not.toHaveBeenCalled();
        expect(page.renderSubtitles).not.toHaveBeenCalled();
    });

    it('finalizes and reveals both pending boundaries in the preserved-clear order', () => {
        const events = [];
        const session = {
            revealPendingForRestart: vi.fn(() => {
                events.push('reveal');
                return { translateChanged: true, hybridChanged: true };
            }),
        };
        const page = createHarness({ session });
        page.logger.log.mockImplementation(() => events.push('log'));
        page.finalizeCurrentNonFinalTokens.mockImplementation(() => events.push('finalize'));
        page.renderSubtitles.mockImplementation(() => events.push('render'));

        expect(page.controller.handle({
            type: 'clear',
            preserve_existing: true,
        })).toBe(true);

        expect(events).toEqual(['log', 'finalize', 'reveal', 'render', 'render']);
        expect(page.clearSubtitleState).not.toHaveBeenCalled();
    });

    it('applies every update frame before rendering it', () => {
        const page = createHarness();
        const frame = { type: 'update', final_tokens: [], non_final_tokens: [] };

        expect(page.controller.handle(frame)).toBe(true);

        expect(page.session.applyUpdateFrame).toHaveBeenCalledWith(frame);
        expect(page.session.applyUpdateFrame.mock.invocationCallOrder[0]).toBeLessThan(
            page.renderSubtitles.mock.invocationCallOrder[0],
        );
    });
});

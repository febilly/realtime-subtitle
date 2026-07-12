const TokenStream = require('../../static/js/token-stream');
const RenderModel = require('../../static/js/render-model');
const RefineState = require('../../static/js/refine-state');
const SubtitleSession = require('../../static/js/subtitle-session');

function create(options = {}) {
    return SubtitleSession.create({ TokenStream, RenderModel, RefineState, ...options });
}

function token(text, options = {}) {
    return {
        text,
        is_final: options.is_final ?? true,
        speaker: options.speaker ?? '1',
        language: options.language ?? 'en',
        translation_status: options.translation_status ?? 'original',
        ...options,
    };
}

describe('SubtitleSession update stream', () => {
    it('filters end markers, assigns sequence indices, and merges new finals', () => {
        const session = create();
        const result = session.applyUpdateFrame({
            final_tokens: [token('Hello'), token(' world'), token('<end>')],
            non_final_tokens: [token('draft', { is_final: false }), token('<end>', { is_final: false })],
        });

        expect(result).toEqual({ changed: true, hasNewFinalContent: true });
        const state = session.getDebugState();
        expect(state.allFinalTokens).toHaveLength(1);
        expect(state.allFinalTokens[0]).toMatchObject({ text: 'Hello world', _sequenceIndex: 0 });
        expect(state.currentNonFinalTokens).toHaveLength(1);
        expect(state.currentNonFinalTokens[0]).toMatchObject({ text: 'draft', _sequenceIndex: 2 });
        expect(state.tokenSequenceCounter).toBe(3);
    });

    it('replaces the authoritative non-final tail without merging old drafts', () => {
        const session = create();
        session.applyUpdateFrame({ non_final_tokens: [token('old', { is_final: false })] });
        session.applyUpdateFrame({ non_final_tokens: [token('new', { is_final: false })] });

        expect(session.getDebugState().currentNonFinalTokens).toEqual([
            expect.objectContaining({ text: 'new', _sequenceIndex: 1 }),
        ]);
        expect(session.getDebugState().tokenSequenceCounter).toBe(2);
    });

    it('does not run final-token merging when a frame has no new final content', () => {
        const session = create();
        session.applyUpdateFrame({ final_tokens: [token('A')] });
        const mergedIndex = session.getDebugState().lastMergedIndex;
        const result = session.applyUpdateFrame({
            final_tokens: [token('<end>')],
            non_final_tokens: [],
        });
        expect(result.hasNewFinalContent).toBe(false);
        expect(session.getDebugState().lastMergedIndex).toBe(mergedIndex);
    });

    it('finalizes a pending tail and appends a reconnect separator', () => {
        const session = create();
        session.applyUpdateFrame({
            non_final_tokens: [token('pending', { is_final: false })],
        });

        expect(session.finalizeCurrentNonFinalTokens()).toEqual({
            changed: true,
            invalidateRenderCache: true,
        });
        const state = session.getDebugState();
        expect(state.currentNonFinalTokens).toEqual([]);
        expect(state.allFinalTokens[0]).toMatchObject({ text: 'pending', is_final: true });
        expect(state.allFinalTokens.at(-1)).toMatchObject({
            is_separator: true,
            separator_type: 'reconnect',
        });
        expect(session.finalizeCurrentNonFinalTokens().changed).toBe(false);
    });

    it('builds render tokens without mutating the session sequence counter', () => {
        const session = create();
        session.applyUpdateFrame({
            non_final_tokens: [token('Sentence. Next', { is_final: false })],
        });
        const before = session.getDebugState().tokenSequenceCounter;
        const first = session.buildRenderTokens();
        const second = session.buildRenderTokens();
        expect(first).not.toBe(second);
        expect(session.getDebugState().tokenSequenceCounter).toBe(before);
    });
});

describe('SubtitleSession refine and retract state', () => {
    it('applies refine and speculative translation state through query methods', () => {
        const session = create();
        expect(session.markSpecPending('hello', 'zh').changed).toBe(true);
        expect(session.isSpecPending('hello')).toBe(true);
        expect(session.getSpecPendingLanguage('hello')).toBe('zh');
        expect(session.applySpecTranslation({
            source: 'hello', translation: '你好', target_lang: 'zh',
        }).changed).toBe(true);
        expect(session.isSpecPending('hello')).toBe(false);
        expect(session.getSpecTranslation('hello')).toEqual({ text: '你好', lang: 'zh' });

        expect(session.applyRefineResult({
            source: 'hello', sentence_id: 's1', refined_translation: '您好', target_lang: 'zh',
        }, { translateMode: true }).changed).toBe(true);
        expect(session.getRefinedTranslation('s1')).toBe('您好');
        expect(session.getTranslationOverride('s1')).toBe('您好');
        expect(session.getTranslationLanguage('s1')).toBe('zh');
    });

    it('removes visible sentence tokens and requests render-cache invalidation', () => {
        const session = create();
        session.applyUpdateFrame({
            final_tokens: [
                token('one', { llm_sentence_id: 's1' }),
                { is_separator: true, is_final: true },
                token('two', { llm_sentence_id: 's2', language: 'ja' }),
            ],
        });

        expect(session.retract('s1')).toEqual({
            changed: true,
            removed: true,
            invalidateRenderCache: true,
        });
        expect(session.getDebugState().allFinalTokens.some(
            (entry) => entry.llm_sentence_id === 's1',
        )).toBe(false);
    });

    it('marks an invisible sentence retracted so late refine results are ignored', () => {
        const session = create();
        expect(session.retract('missing')).toEqual({
            changed: false,
            removed: false,
            invalidateRenderCache: false,
        });
        expect(session.applyRefineResult({
            source: 'late', sentence_id: 'missing', refined_translation: 'too late',
        }).changed).toBe(false);
        expect(session.getRefinedTranslation('missing')).toBeUndefined();
    });

    it('records no-change confirmation', () => {
        const session = create();
        session.applyRefineResult({ source: 'same', sentence_id: 's1', no_change: true });
        expect(session.getConfirmed('s1')).toBe(true);
    });
});

describe('SubtitleSession mode boundaries and clearing', () => {
    it('tracks translate and hybrid entry boundaries from the next sequence', () => {
        const session = create({ translationUiMode: 'fast' });
        session.applyUpdateFrame({ final_tokens: [token('one')] });
        expect(session.applyLlmMode('translate', 'refine')).toBe(2);
        expect(session.noteHybridBoundary('hybrid', 'fast')).toBe(2);
        expect(session.applyLlmMode('off', 'translate')).toBeNull();
        expect(session.noteHybridBoundary('fast', 'hybrid')).toBeNull();
    });

    it('reveals old pending text at the current restart boundary', () => {
        const session = create({ translateMode: true, translationUiMode: 'hybrid' });
        session.applyUpdateFrame({ final_tokens: [token('one')] });
        expect(session.revealPendingForRestart()).toEqual({
            translateChanged: true,
            hybridChanged: true,
        });
        expect(session.getLlmTranslateHideAfterSequence()).toBe(1);
        expect(session.getHybridInterimAfterSequence()).toBe(1);
    });

    it('clears tokens, refine data, counters, and resets active mode boundaries', () => {
        const session = create();
        session.applyUpdateFrame({ final_tokens: [token('one')] });
        session.applyRefineResult({
            source: 'one', sentence_id: 's1', refined_translation: 'refined',
        });

        expect(session.clear({ translateMode: true, translationUiMode: 'hybrid' })).toEqual({
            changed: true,
            invalidateRenderCache: true,
        });
        expect(session.getDebugState()).toMatchObject({
            allFinalTokens: [],
            currentNonFinalTokens: [],
            lastMergedIndex: 0,
            tokenSequenceCounter: 0,
            llmTranslateHideAfterSequence: 0,
            hybridInterimAfterSequence: 0,
        });
        expect(session.getRefinedTranslation('s1')).toBeUndefined();
    });
});

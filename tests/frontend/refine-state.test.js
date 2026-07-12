const RefineState = require('../../static/js/refine-state');

function refineResult(overrides = {}) {
    return {
        sentence_id: 'sentence-1',
        source: 'Hello.',
        original_translation: '你好。',
        refined_translation: '您好。',
        target_lang: 'ZH',
        no_change: false,
        ...overrides,
    };
}

describe('RefineState', () => {
    it('stores an applied result and accurate-mode override by sentence id', () => {
        const state = RefineState.createRefineState();
        state.markSpecPending(' Hello. ', 'ZH');

        expect(state.applyRefineResult(refineResult(), { translateMode: true })).toBe(true);
        expect(state.lookup('sentence-1')).toEqual({
            sentenceId: 'sentence-1',
            refinedTranslation: '您好。',
            confirmed: false,
            retracted: false,
            translationOverride: '您好。',
            translationLanguage: 'zh',
        });
        expect(state.isSpecPending('Hello.')).toBe(false);
    });

    it('marks no-change results as confirmed without inventing a replacement', () => {
        const state = RefineState.createRefineState();
        state.markSpecPending('Hello.', 'zh');

        expect(state.applyRefineResult(refineResult({
            refined_translation: '',
            no_change: true,
        }))).toBe(true);
        expect(state.isConfirmed('sentence-1')).toBe(true);
        expect(state.getRefinedTranslation('sentence-1')).toBeUndefined();
        expect(state.getTranslationOverride('sentence-1')).toBeUndefined();
        expect(state.isSpecPending('Hello.')).toBe(false);
    });

    it('cleans id caches on retraction and ignores a late result', () => {
        const state = RefineState.createRefineState();
        state.applyRefineResult(refineResult(), { translateMode: true });
        expect(state.markRetracted('sentence-1')).toBe(true);

        expect(state.lookup('sentence-1')).toEqual({
            sentenceId: 'sentence-1',
            refinedTranslation: undefined,
            confirmed: false,
            retracted: true,
            translationOverride: undefined,
            translationLanguage: undefined,
        });

        state.markSpecPending('Hello.', 'zh');
        expect(state.applyRefineResult(refineResult({ refined_translation: '迟到结果' }), {
            translateMode: true,
        })).toBe(false);
        expect(state.getRefinedTranslation('sentence-1')).toBeUndefined();
        expect(state.isSpecPending('Hello.')).toBe(true);
    });

    it('clears all id and speculative state for a fresh session', () => {
        const state = RefineState.createRefineState();
        state.applyRefineResult(refineResult(), { translateMode: true });
        state.applyRefineResult(refineResult({
            sentence_id: 'sentence-2',
            source: 'World.',
            refined_translation: '',
            no_change: true,
        }));
        state.markRetracted('sentence-3');
        state.markSpecPending('pending', 'JA');
        state.setSpecTranslation('ready', '翻訳', 'JA');

        state.clear();

        for (const id of ['sentence-1', 'sentence-2', 'sentence-3']) {
            expect(state.lookup(id)).toEqual({
                sentenceId: id,
                refinedTranslation: undefined,
                confirmed: false,
                retracted: false,
                translationOverride: undefined,
                translationLanguage: undefined,
            });
        }
        expect(state.isSpecPending('pending')).toBe(false);
        expect(state.getSpecTranslation('ready')).toBeUndefined();
    });

    it('bounds speculative pending and result maps with FIFO eviction', () => {
        const state = RefineState.createRefineState({ specMapMax: 2 });

        state.markSpecPending('first', 'EN');
        state.markSpecPending('second', 'JA');
        state.markSpecPending('third', 'ZH');
        expect(state.isSpecPending('first')).toBe(false);
        expect(state.getSpecPendingLanguage('second')).toBe('ja');
        expect(state.getSpecPendingLanguage('third')).toBe('zh');

        state.setSpecTranslation('one', '1', 'EN');
        state.setSpecTranslation('two', '2', 'JA');
        state.setSpecTranslation('three', '3', 'ZH');
        expect(state.getSpecTranslation('one')).toBeUndefined();
        expect(state.getSpecTranslation('two')).toEqual({ text: '2', lang: 'ja' });
        expect(state.getSpecTranslation('three')).toEqual({ text: '3', lang: 'zh' });
    });

    it('removes retracted sentence tokens and orphaned edge separators', () => {
        const leading = { text: 'old', llm_sentence_id: 7 };
        const firstSeparator = { is_separator: true };
        const kept = { text: 'keep', llm_sentence_id: 'other' };
        const secondSeparator = { is_separator: true };
        const trailing = { text: 'also old', llm_sentence_id: '7' };
        const original = [leading, firstSeparator, kept, secondSeparator, trailing];

        const result = RefineState.removeSentenceTokens(original, '7');
        expect(result).toEqual({ tokens: [kept], removed: true });
        expect(original).toHaveLength(5);

        const untouched = RefineState.removeSentenceTokens(original, 'missing');
        expect(untouched).toEqual({ tokens: original, removed: false });
        expect(untouched.tokens).toBe(original);
    });
});

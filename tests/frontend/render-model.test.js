const RenderModel = require('../../static/js/render-model');

function original(text, overrides = {}) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'en',
        translation_status: 'original',
        ...overrides,
    };
}

function translation(text, overrides = {}) {
    return original(text, {
        language: 'zh',
        source_language: 'en',
        translation_status: 'translation',
        ...overrides,
    });
}

function separator(overrides = {}) {
    return {
        is_separator: true,
        is_final: true,
        ...overrides,
    };
}

describe('RenderModel sentence construction', () => {
    it('keeps a mixed-language original with one LLM id in one sentence', () => {
        const tokens = [
            original('Say ', { llm_sentence_id: 'same', _sequenceIndex: 4 }),
            original('大丈夫。', { language: 'ja', llm_sentence_id: 'same', _sequenceIndex: 5 }),
        ];

        const model = RenderModel.buildRenderModel({ tokens });

        expect(model.sentences).toHaveLength(1);
        expect(model.sentences[0].originalTokens).toEqual(tokens);
        expect(model.sentences[0].llmSentenceId).toBe('same');
        expect(model.sentences[0].renderKey).toBe('sent-4');
    });

    it('splits a language change carrying a different LLM id', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [
                original('English', { llm_sentence_id: 'one' }),
                original('日本語', { language: 'ja', llm_sentence_id: 'two' }),
            ],
        });

        expect(model.sentences.map((sentence) => sentence.llmSentenceId)).toEqual(['one', 'two']);
        expect(model.sentences.map((sentence) => sentence.originalLang)).toEqual(['en', 'ja']);
    });

    it('keeps the old no-id non-final language-change split', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [
                original('live ', { is_final: false }),
                original('字幕', { is_final: false, language: 'ja' }),
            ],
        });

        expect(model.sentences).toHaveLength(2);
        expect(model.sentences.map((sentence) => sentence.llmSentenceId)).toEqual([null, null]);
    });

    it('reattaches a late translation to the earlier sentence by id', () => {
        const first = original('First.', { llm_sentence_id: 'first' });
        const second = original('Second.', { llm_sentence_id: 'second' });
        const late = translation('第一句。', { llm_sentence_id: 'first' });
        const model = RenderModel.buildRenderModel({
            tokens: [first, separator(), second, late],
        });

        expect(model.sentences).toHaveLength(2);
        expect(model.sentences[0].translationTokens).toEqual([late]);
        expect(model.sentences[1].translationTokens).toEqual([]);
    });

    it('groups adjacent sentences into speaker blocks and splits on speaker change', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [
                original('one', { speaker: '1' }),
                separator(),
                original('two', { speaker: '1' }),
                separator(),
                original('three', { speaker: '2' }),
            ],
        });

        expect(model.speakerBlocks).toHaveLength(2);
        expect(model.speakerBlocks.map((block) => block.speaker)).toEqual(['1', '2']);
        expect(model.speakerBlocks[0].sentences).toHaveLength(2);
    });

    it('does not create sentences or empty blocks from separators', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [separator(), separator()],
        });

        expect(model.sentences).toEqual([]);
        expect(model.speakerBlocks).toEqual([]);
    });

    it('filters a translation-only sentence from original-only display without creating an empty block', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [translation('只有译文', { speaker: '2', llm_sentence_id: 'translation-only' })],
            displayMode: 'original',
        });

        expect(model.sentences).toHaveLength(1);
        expect(model.sentences[0].isTranslationOnly).toBe(true);
        expect(model.speakerBlocks).toEqual([]);
    });

    it('exposes the fields required by the display layer with distinct ids', () => {
        const source = original('Hello.', {
            llm_sentence_id: 17,
            _sequenceIndex: 9,
        });
        const target = translation('你好。', { llm_sentence_id: 17 });
        const model = RenderModel.buildRenderModel({
            tokens: [source, target, separator()],
        });
        const sentence = model.sentences[0];

        expect(sentence).toMatchObject({
            speaker: '1',
            llmSentenceId: '17',
            renderKey: 'sent-9',
            originalLang: 'en',
            translationLang: 'zh',
            requiresTranslation: true,
            isTranslationOnly: false,
            isClosed: true,
            hasFakeTranslation: false,
        });
        expect(sentence).not.toHaveProperty('sentenceId');
        expect(sentence.originalTokens).toEqual([source]);
        expect(sentence.translationTokens).toEqual([target]);
        expect(model).toMatchObject({ showOriginal: true, showTranslation: true });
    });

    it('forces original visibility when translation display is suppressed', () => {
        const model = RenderModel.buildRenderModel({
            tokens: [original('source')],
            displayMode: 'translation',
            suppressTranslationDisplay: true,
        });

        expect(model.showOriginal).toBe(true);
        expect(model.showTranslation).toBe(false);
        expect(model.speakerBlocks).toHaveLength(1);
    });
});

describe('RenderModel speculative token stream', () => {
    it('inserts a speculative separator after an ordinary completed sentence', () => {
        const first = original('Done.', { is_final: false });
        const second = original(' Next', { is_final: false });
        const tokens = RenderModel.buildRenderTokens({
            currentNonFinalTokens: [first, second],
        });

        expect(tokens).toEqual([
            first,
            { is_separator: true, is_final: false, separator_type: 'speculative' },
            second,
        ]);
    });

    it('splits after a period on a fragmented word suffix using full context', () => {
        const first = original(' her', { is_final: false });
        const suffix = original('e.', { is_final: false });
        const next = original(' So', { is_final: false });
        const tokens = RenderModel.buildRenderTokens({
            currentNonFinalTokens: [first, suffix, next],
        });

        expect(tokens).toEqual([
            first,
            suffix,
            { is_separator: true, is_final: false, separator_type: 'speculative' },
            next,
        ]);
    });

    it('keeps a streamed e.g. abbreviation together', () => {
        const values = [
            original('For e.', { is_final: false }),
            original('g.', { is_final: false }),
            original(' example', { is_final: false }),
        ];
        const tokens = RenderModel.buildRenderTokens({ currentNonFinalTokens: values });

        expect(tokens).toEqual(values);
    });

    it('does not split punctuation inside an opener from an earlier token', () => {
        const values = [
            original('「だめ、', { is_final: false, language: 'ja' }),
            original('だめ。', { is_final: false, language: 'ja' }),
            original('それ、スズメバチ」', { is_final: false, language: 'ja' }),
        ];
        const tokens = RenderModel.buildRenderTokens({ currentNonFinalTokens: values });

        expect(tokens).toEqual(values);
        expect(tokens.some((token) => token.is_separator)).toBe(false);
    });

    it('carries open-quote context forward from the finalized tail', () => {
        const finalized = original('「まだ', { language: 'ja' });
        const values = [
            original('途中。', { is_final: false, language: 'ja' }),
            original('続く」', { is_final: false, language: 'ja' }),
        ];
        const tokens = RenderModel.buildRenderTokens({
            allFinalTokens: [finalized],
            currentNonFinalTokens: values,
        });

        expect(tokens).toEqual([finalized, ...values]);
    });

    it('does not split a streamed ASCII ellipsis dot run', () => {
        const values = [
            original('.', { is_final: false }),
            original('..', { is_final: false }),
            original(' still speaking', { is_final: false }),
        ];
        const tokens = RenderModel.buildRenderTokens({ currentNonFinalTokens: values });

        expect(tokens).toEqual(values);
        expect(tokens.some((token) => token.is_separator)).toBe(false);
    });

    it('does not split a unicode ellipsis or a punctuation-plus-ellipsis run', () => {
        const values = [
            original('等等。', { is_final: false }),
            original('…', { is_final: false }),
            original('好', { is_final: false }),
        ];
        const tokens = RenderModel.buildRenderTokens({ currentNonFinalTokens: values });

        expect(tokens).toEqual(values);
    });

    it('does not add speculative separators when the non-final tail contains translation', () => {
        const values = [
            original('Done.', { is_final: false }),
            translation('完成。', { is_final: false }),
            original('Next', { is_final: false }),
        ];
        expect(RenderModel.buildRenderTokens({ currentNonFinalTokens: values })).toEqual(values);
    });
});

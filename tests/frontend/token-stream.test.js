const TokenStream = require('../../static/js/token-stream');

function token(text, overrides = {}) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'en',
        translation_status: 'original',
        source_language: 'en',
        ...overrides,
    };
}

describe('TokenStream', () => {
    it('joins token text without normalizing whitespace', () => {
        expect(TokenStream.joinTokenText(null)).toBe('');
        expect(TokenStream.joinTokenText([token(' hello'), null, token(' world ')])).toBe(' hello world ');
    });

    it('assigns a stable sequence index', () => {
        const value = token('a');
        expect(TokenStream.assignSequenceIndex(value, 7)).toBe(8);
        expect(value._sequenceIndex).toBe(7);
        expect(TokenStream.assignSequenceIndex(value, 8)).toBe(8);
        expect(value._sequenceIndex).toBe(7);
    });

    it('inserts the same token object and advances the counter', () => {
        const tokens = [];
        const value = token('a');
        expect(TokenStream.insertFinalToken(tokens, value, 3)).toBe(4);
        expect(tokens).toEqual([value]);
        expect(value._sequenceIndex).toBe(3);
    });

    it('merges adjacent compatible final tokens', () => {
        const tokens = [token('Hello', { llm_sentence_id: 12 }), token('.', { llm_sentence_id: 12 })];
        const lastMergedIndex = TokenStream.mergeFinalTokens(tokens, 0);
        expect(lastMergedIndex).toBe(1);
        expect(tokens).toHaveLength(1);
        expect(tokens[0]).toMatchObject({ text: 'Hello.', llm_sentence_id: '12', _merged: true });
    });

    it('keeps separators, non-final tokens, and incompatible metadata apart', () => {
        const separator = token('', { is_separator: true });
        const tokens = [
            token('a'),
            separator,
            token('b'),
            token('c', { is_final: false }),
            token('d', { speaker: '2' }),
            token('e', { language: 'ja' }),
            token('f', { translation_status: 'translation' }),
            token('g', { source_language: 'ja' }),
        ];
        TokenStream.mergeFinalTokens(tokens, 0);
        expect(tokens.map((value) => value.text)).toEqual(['a', '', 'b', 'c', 'd', 'e', 'f', 'g']);
    });

    it('treats a missing translation status as original', () => {
        const tokens = [token('a', { translation_status: undefined }), token('b')];
        TokenStream.mergeFinalTokens(tokens, 0);
        expect(tokens.map((value) => value.text)).toEqual(['ab']);
    });

    it('locks the current cross-id merge behavior and removes an ambiguous id', () => {
        const tokens = [token('first', { llm_sentence_id: 'a' }), token('second', { llm_sentence_id: 'b' })];
        TokenStream.mergeFinalTokens(tokens, 0);
        expect(tokens).toHaveLength(1);
        expect(tokens[0].text).toBe('firstsecond');
        expect(tokens[0]).not.toHaveProperty('llm_sentence_id');
    });

    it('looks back one token when merging an incremental tail', () => {
        const tokens = [token('old', { _merged: true }), token('new')];
        expect(TokenStream.mergeFinalTokens(tokens, 1)).toBe(1);
        expect(tokens[0].text).toBe('oldnew');
    });

    it('leaves the merge cursor unchanged for an empty stream', () => {
        expect(TokenStream.mergeFinalTokens([], 9)).toBe(9);
    });
});

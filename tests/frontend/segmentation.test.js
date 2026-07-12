const fs = require('node:fs');
const path = require('node:path');
const Segmentation = require('../../static/js/segmentation');

const fixture = JSON.parse(fs.readFileSync(
    path.resolve(__dirname, '..', 'fixtures', 'segmentation-cases.json'),
    'utf8',
));

const functions = {
    is_sentence_ender_at: Segmentation.isSentenceEnderAt,
    text_ends_with_ellipsis: Segmentation.textEndsWithEllipsis,
    text_has_unclosed_quote: Segmentation.textHasUnclosedQuote,
    text_continues_abbreviation: Segmentation.textContinuesAbbreviation,
    token_text_continues_decimal: Segmentation.tokenTextContinuesDecimal,
    token_text_starts_with_closing_quote: Segmentation.tokenTextStartsWithClosingQuote,
    text_ends_with_closing_quote_after_sentence_punctuation: (
        Segmentation.textEndsWithClosingQuoteAfterSentencePunctuation
    ),
    has_sentence_ending_punctuation: Segmentation.hasSentenceEndingPunctuation,
    is_sentence_ending_punctuation: Segmentation.isSentenceEndingPunctuation,
    split_text_at_sentence_boundaries: Segmentation.splitTextAtSentenceBoundaries,
    split_into_sentence_lines: Segmentation.splitIntoSentenceLines,
};

describe('shared sentence segmentation cases', () => {
    it.each(fixture.cases.map((testCase) => [testCase.id, testCase]))('%s', (_id, testCase) => {
        const actual = functions[testCase.function](...testCase.args);
        expect(actual).toEqual(testCase.expected);
        if (testCase.function === 'split_text_at_sentence_boundaries') {
            expect(actual.join('')).toBe(testCase.args[0]);
        }
    });
});

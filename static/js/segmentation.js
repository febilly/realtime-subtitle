(function (root) {
    'use strict';

    // Keep this table in sync with sentence_segmentation.py. Ellipsis glyphs
    // remain in the set so punctuation runs stay intact, but never end a line.
    const SENTENCE_END_CHARS = new Set(['。', '！', '？', '.', '!', '?', '︒', '︕', '︖', '…']);
    const CLOSING_QUOTE_CHARS = new Set(['"', "'", '”', '’', '»', '›', '」', '』', '》']);
    const QUOTE_PAIRS = new Map([
        ['「', '」'], ['『', '』'], ['“', '”'], ['（', '）'], ['【', '】'], ['《', '》'],
    ]);
    const SENTENCE_END_ABBREVIATION_EXCEPTIONS = ['a.m.', 'p.m.', 'e.g.', 'i.e.', 'u.s.', 'u.k.'];
    const SENTENCE_END_ABBREVIATION_PREFIXES = SENTENCE_END_ABBREVIATION_EXCEPTIONS.flatMap((abbr) => {
        const prefixes = [];
        for (let index = 0; index < abbr.length - 1; index += 1) {
            if (abbr[index] === '.') prefixes.push(abbr.slice(0, index + 1));
        }
        return prefixes;
    });

    function isDigit(value) {
        return !!value && /\p{N}/u.test(value);
    }

    function isAlphabetic(value) {
        return !!value && /\p{L}/u.test(value);
    }

    function isSentenceEnderAt(value, index) {
        const ch = value[index];
        if (ch === '…') return false;
        if (ch === '.') {
            const previous = index > 0 ? value[index - 1] : '';
            const next = index + 1 < value.length ? value[index + 1] : '';
            if (previous === '.' || next === '.') return false;
            if (isDigit(previous) && isDigit(next)) return false;
        }
        return SENTENCE_END_CHARS.has(ch);
    }

    function textEndsWithEllipsis(text) {
        let value = String(text || '').trimEnd();
        while (value && CLOSING_QUOTE_CHARS.has(value.at(-1))) {
            value = value.slice(0, -1).trimEnd();
        }
        return value.endsWith('…') || value.endsWith('..');
    }

    function textHasUnclosedQuote(text) {
        const value = String(text || '');
        for (const [opener, closer] of QUOTE_PAIRS) {
            if (value.split(opener).length > value.split(closer).length) return true;
        }
        return false;
    }

    function textEndsWithAbbreviationSegment(text, segment) {
        const value = String(text || '').trimEnd().toLowerCase();
        if (!value.endsWith(segment)) return false;
        const start = value.length - segment.length;
        return start === 0 || !isAlphabetic(value[start - 1]);
    }

    function textEndsWithAbbreviationException(text) {
        return SENTENCE_END_ABBREVIATION_EXCEPTIONS.some(
            (abbr) => textEndsWithAbbreviationSegment(text, abbr),
        );
    }

    function textEndsWithAbbreviationPrefix(text) {
        return SENTENCE_END_ABBREVIATION_PREFIXES.some(
            (prefix) => textEndsWithAbbreviationSegment(text, prefix),
        );
    }

    function textContinuesAbbreviation(previousContext, nextText) {
        const combined = `${previousContext || ''}${nextText || ''}`;
        return textEndsWithAbbreviationException(combined) || textEndsWithAbbreviationPrefix(combined);
    }

    function tokenTextContinuesDecimal(previousText, nextText) {
        if (!previousText || !nextText) return false;
        return !/\s$/u.test(previousText) && !/^\s/u.test(nextText) && isDigit(nextText[0]);
    }

    function tokenTextStartsWithClosingQuote(previousText, nextText) {
        if (!previousText || !nextText) return false;
        return !/\s$/u.test(previousText) && CLOSING_QUOTE_CHARS.has(nextText[0]);
    }

    function textEndsWithClosingQuoteAfterSentencePunctuation(text) {
        const value = String(text || '').trimEnd();
        if (!value || !CLOSING_QUOTE_CHARS.has(value.at(-1))) return false;
        let index = value.length - 1;
        while (index >= 0 && CLOSING_QUOTE_CHARS.has(value[index])) index -= 1;
        return index >= 0 && isSentenceEnderAt(value, index);
    }

    function isSentenceEndingPunctuation(text) {
        let value = String(text || '').trim();
        if (!value) return false;
        while (value && CLOSING_QUOTE_CHARS.has(value.at(-1))) {
            value = value.slice(0, -1).trimEnd();
        }
        if (!value || textEndsWithAbbreviationException(value) || textEndsWithAbbreviationPrefix(value)) {
            return false;
        }
        for (let index = value.length - 1; index >= 0; index -= 1) {
            if (SENTENCE_END_CHARS.has(value[index])) return isSentenceEnderAt(value, index);
            if (!/\s/u.test(value[index])) return false;
        }
        return false;
    }

    function splitTextAtSentenceBoundaries(text) {
        const value = text || '';
        if (!value) return [];
        const segments = [];
        const quoteDepth = new Map([...QUOTE_PAIRS.keys()].map((opener) => [opener, 0]));
        const closerToOpener = new Map([...QUOTE_PAIRS].map(([opener, closer]) => [closer, opener]));
        let start = 0;
        let index = 0;
        while (index < value.length) {
            const ch = value[index];
            if (quoteDepth.has(ch)) {
                quoteDepth.set(ch, quoteDepth.get(ch) + 1);
                index += 1;
                continue;
            }
            if (closerToOpener.has(ch)) {
                const opener = closerToOpener.get(ch);
                quoteDepth.set(opener, Math.max(0, quoteDepth.get(opener) - 1));
            }
            if (!SENTENCE_END_CHARS.has(ch) || !isSentenceEnderAt(value, index)) {
                index += 1;
                continue;
            }
            if ([...quoteDepth.values()].some(Boolean)) {
                index += 1;
                continue;
            }
            if (index + 1 < value.length && SENTENCE_END_CHARS.has(value[index + 1])) {
                index += 1;
                continue;
            }
            let end = index + 1;
            while (end < value.length && CLOSING_QUOTE_CHARS.has(value[end])) end += 1;
            const segment = value.slice(start, end);
            if (textEndsWithAbbreviationException(segment) || textEndsWithAbbreviationPrefix(segment)) {
                index += 1;
                continue;
            }
            segments.push(segment);
            start = end;
            index = end;
        }
        if (start < value.length) segments.push(value.slice(start));
        return segments;
    }

    function splitIntoSentenceLines(text) {
        return splitTextAtSentenceBoundaries(text).map((piece) => piece.trim()).filter(Boolean);
    }

    const api = {
        SENTENCE_END_CHARS,
        CLOSING_QUOTE_CHARS,
        QUOTE_PAIRS,
        SENTENCE_END_ABBREVIATION_EXCEPTIONS,
        SENTENCE_END_ABBREVIATION_PREFIXES,
        isSentenceEnderAt,
        textEndsWithEllipsis,
        textHasUnclosedQuote,
        textEndsWithAbbreviationSegment,
        textEndsWithAbbreviationException,
        textEndsWithAbbreviationPrefix,
        textContinuesAbbreviation,
        tokenTextContinuesDecimal,
        tokenTextStartsWithClosingQuote,
        textEndsWithClosingQuoteAfterSentencePunctuation,
        isSentenceEndingPunctuation,
        endsWithSentenceEnding: isSentenceEndingPunctuation,
        splitTextAtSentenceBoundaries,
        splitIntoSentenceLines,
        splitIntoSentenceSegments: splitIntoSentenceLines,
    };

    root.Segmentation = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

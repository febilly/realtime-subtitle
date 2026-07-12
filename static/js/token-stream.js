(function (root) {
    'use strict';

    function joinTokenText(tokens) {
        if (!tokens || tokens.length === 0) return '';
        return tokens.map((token) => (token && token.text ? String(token.text) : '')).join('');
    }

    function assignSequenceIndex(token, nextSequenceIndex) {
        if (!token || token._sequenceIndex !== undefined) return nextSequenceIndex;
        token._sequenceIndex = nextSequenceIndex;
        return nextSequenceIndex + 1;
    }

    function insertFinalToken(tokens, token, nextSequenceIndex) {
        const next = assignSequenceIndex(token, nextSequenceIndex);
        tokens.push(token);
        return next;
    }

    function mergeFinalTokens(tokens, previousMergedIndex = 0) {
        if (!tokens.length) return previousMergedIndex;
        const safeStart = Math.max(0, previousMergedIndex - 1);
        const startIndex = Math.min(safeStart, tokens.length - 1);
        let writeIndex = startIndex;
        let readIndex = startIndex;

        while (readIndex < tokens.length) {
            const currentToken = tokens[readIndex];
            if (currentToken.is_separator || !currentToken.is_final) {
                tokens[writeIndex] = currentToken;
                writeIndex += 1;
                readIndex += 1;
                continue;
            }

            let mergedText = currentToken.text || '';
            const mergedToken = { ...currentToken };
            let nextIndex = readIndex + 1;
            while (nextIndex < tokens.length) {
                const nextToken = tokens[nextIndex];
                if (
                    !nextToken.is_separator
                    && nextToken.is_final
                    && nextToken.speaker === currentToken.speaker
                    && nextToken.language === currentToken.language
                    && (nextToken.translation_status || 'original') === (currentToken.translation_status || 'original')
                    && nextToken.source_language === currentToken.source_language
                ) {
                    mergedText += nextToken.text || '';
                    nextIndex += 1;
                } else {
                    break;
                }
            }

            mergedToken.text = mergedText;
            mergedToken._merged = true;
            const sentenceIds = new Set();
            for (let index = readIndex; index < nextIndex; index += 1) {
                const id = tokens[index] && tokens[index].llm_sentence_id;
                if (id) sentenceIds.add(String(id));
            }
            if (sentenceIds.size === 1) {
                [mergedToken.llm_sentence_id] = sentenceIds;
            } else if (sentenceIds.size > 1) {
                delete mergedToken.llm_sentence_id;
            }

            tokens[writeIndex] = mergedToken;
            writeIndex += 1;
            readIndex = nextIndex;
        }

        tokens.length = writeIndex;
        return tokens.length;
    }

    const api = { assignSequenceIndex, insertFinalToken, joinTokenText, mergeFinalTokens };
    root.TokenStream = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

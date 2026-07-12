(function (root, factory) {
    'use strict';

    const segmentation = root.Segmentation || (
        typeof module !== 'undefined' && module.exports
            ? require('./segmentation')
            : null
    );
    const api = factory(segmentation);
    root.RenderModel = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function createRenderModel(Segmentation) {
    'use strict';

    if (!Segmentation) {
        throw new Error('RenderModel requires Segmentation');
    }

    function ensureSpeakerValue(speaker) {
        return (speaker === null || speaker === undefined) ? 'undefined' : speaker;
    }

    function tokenContextStatus(token) {
        return token && token.translation_status === 'translation' ? 'translation' : 'original';
    }

    function getContext(contexts, token) {
        const speaker = ensureSpeakerValue(token && token.speaker);
        const byStatus = contexts.get(speaker);
        return byStatus ? (byStatus.get(tokenContextStatus(token)) || '') : '';
    }

    function setContext(contexts, token, text) {
        const speaker = ensureSpeakerValue(token && token.speaker);
        let byStatus = contexts.get(speaker);
        if (!byStatus) {
            byStatus = new Map();
            contexts.set(speaker, byStatus);
        }
        byStatus.set(tokenContextStatus(token), text);
    }

    function appendTokenContext(contexts, token) {
        if (!token) return '';
        if (token.is_separator) {
            contexts.clear();
            return '';
        }
        const context = `${getContext(contexts, token)}${token.text || ''}`;
        setContext(contexts, token, context);
        return context;
    }

    function startsPunctuationRun(nextText) {
        const value = String(nextText || '');
        return !!value && !!Segmentation.SENTENCE_END_CHARS
            && Segmentation.SENTENCE_END_CHARS.has(value[0]);
    }

    function shouldSpeculativelySplitAfterToken(text, nextText, contextText = text) {
        const value = String(text || '');
        const following = String(nextText || '');
        const context = String(contextText || '');

        if (!Segmentation.hasSentenceEndingPunctuation(value)) return false;

        // The backend makes these decisions against the accumulated sentence,
        // not only the latest token. This matters when an opener or the first
        // ellipsis dot arrived in an earlier token.
        if (Segmentation.textEndsWithEllipsis(context)) return false;
        if (Segmentation.textHasUnclosedQuote(context)) return false;
        if (Segmentation.textEndsWithAbbreviationException(context)) return false;
        if (Segmentation.textEndsWithAbbreviationPrefix(context)) return false;

        // Keep a punctuation run together. In particular, "." + ".." and
        // "。" + "…" are trailing-off runs rather than sentence boundaries.
        if (startsPunctuationRun(following)) return false;
        if (Segmentation.tokenTextStartsWithClosingQuote(value, following)) return false;
        if (Segmentation.tokenTextContinuesDecimal(value, following)) return false;
        if (Segmentation.textContinuesAbbreviation(context, following)) return false;
        return true;
    }

    /**
     * Return the flat stream consumed by sentence construction. Final tokens
     * already contain authoritative separators; speculative separators are
     * added only to an original-only non-final tail.
     */
    function buildRenderTokens({
        allFinalTokens = [],
        currentNonFinalTokens = [],
    } = {}) {
        const nonFinal = currentNonFinalTokens || [];
        const hasNonFinalTranslation = nonFinal.some(
            (token) => (token.translation_status || 'original') === 'translation',
        );
        if (hasNonFinalTranslation) return [...(allFinalTokens || []), ...nonFinal];

        const finalTokens = allFinalTokens || [];
        const tokens = [...finalTokens];
        const contexts = new Map();
        finalTokens.forEach((token) => appendTokenContext(contexts, token));

        nonFinal.forEach((token, index) => {
            tokens.push(token);
            const contextText = appendTokenContext(contexts, token);
            const isLast = index === nonFinal.length - 1;
            const nextToken = nonFinal[index + 1];
            const nextText = nextToken ? nextToken.text : '';
            if (
                !isLast
                && token
                && !token.is_separator
                && shouldSpeculativelySplitAfterToken(token.text, nextText, contextText)
            ) {
                tokens.push({
                    is_separator: true,
                    is_final: false,
                    separator_type: 'speculative',
                });
                contexts.clear();
            }
        });
        return tokens;
    }

    function resolveLlmSentenceId(sentence) {
        const tokens = [
            ...(sentence && Array.isArray(sentence.originalTokens) ? sentence.originalTokens : []),
            ...(sentence && Array.isArray(sentence.translationTokens) ? sentence.translationTokens : []),
        ];
        for (const token of tokens) {
            if (token && token.llm_sentence_id) return String(token.llm_sentence_id);
        }
        return null;
    }

    function getSentenceRenderKey(sentence, fallbackIndex) {
        const anchorToken = sentence.originalTokens[0] || sentence.translationTokens[0];
        if (anchorToken && anchorToken._sequenceIndex !== undefined) {
            return `sent-${anchorToken._sequenceIndex}`;
        }
        return `sent-fallback-${fallbackIndex}`;
    }

    function buildSentences(tokens = []) {
        const sentences = [];
        // app.js historically stores the first id-bearing *original* token on
        // the sentence for attachment/language-boundary decisions, while its
        // display helper later scans original + translation tokens. Keep those
        // concepts separate so a translation-only id cannot affect grouping.
        const sourceSentenceIds = new WeakMap();
        let currentSentence = null;

        const startSentence = (speaker, options = {}) => {
            const sentence = {
                speaker: ensureSpeakerValue(speaker),
                llmSentenceId: null,
                renderKey: null,
                originalTokens: [],
                translationTokens: [],
                originalLang: null,
                translationLang: null,
                requiresTranslation: options.requiresTranslation !== undefined
                    ? options.requiresTranslation
                    : null,
                isTranslationOnly: !!options.translationOnly,
                isClosed: false,
                hasFakeTranslation: false,
            };
            sentences.push(sentence);
            sourceSentenceIds.set(sentence, null);
            if (!sentence.isTranslationOnly) currentSentence = sentence;
            return sentence;
        };

        const findLastSentenceForSpeaker = (speaker, predicate = () => true, options = {}) => {
            const normalizedSpeaker = ensureSpeakerValue(speaker);
            const stopOnFakeTranslation = !!options.stopOnFakeTranslation;
            for (let index = sentences.length - 1; index >= 0; index -= 1) {
                const sentence = sentences[index];
                if (sentence.speaker === normalizedSpeaker && predicate(sentence)) return sentence;
                if (
                    stopOnFakeTranslation
                    && sentence.speaker === normalizedSpeaker
                    && sentence.hasFakeTranslation
                ) {
                    break;
                }
            }
            return null;
        };

        const findNearestOriginalSentenceForSpeaker = (speaker) => findLastSentenceForSpeaker(
            speaker,
            (sentence) => !sentence.isTranslationOnly,
            { stopOnFakeTranslation: false },
        );

        tokens.forEach((token) => {
            if (!token) return;
            if (token.is_separator) {
                if (
                    currentSentence
                    && currentSentence.requiresTranslation !== false
                    && currentSentence.translationTokens.length === 0
                ) {
                    currentSentence.hasFakeTranslation = true;
                }
                if (currentSentence) currentSentence.isClosed = true;
                currentSentence = null;
                return;
            }

            const speaker = ensureSpeakerValue(token.speaker);
            const translationStatus = token.translation_status || 'original';

            if (translationStatus === 'translation') {
                let targetSentence = null;
                const tokenSentenceId = token.llm_sentence_id ? String(token.llm_sentence_id) : '';
                if (tokenSentenceId) {
                    for (let index = sentences.length - 1; index >= 0; index -= 1) {
                        const candidate = sentences[index];
                        if (
                            sourceSentenceIds.get(candidate) === tokenSentenceId
                            && !candidate.isTranslationOnly
                        ) {
                            targetSentence = candidate;
                            break;
                        }
                    }
                }
                if (!targetSentence) targetSentence = findNearestOriginalSentenceForSpeaker(speaker);
                if (!targetSentence) {
                    targetSentence = startSentence(speaker, { translationOnly: true });
                }
                if (targetSentence.translationLang === null && token.language) {
                    targetSentence.translationLang = token.language;
                }
                if (!targetSentence.originalLang && token.source_language) {
                    targetSentence.originalLang = token.source_language;
                }
                targetSentence.translationTokens.push(token);
                return;
            }

            const tokenRequiresTranslation = translationStatus !== 'none';
            let shouldStartNew = false;
            if (!currentSentence) shouldStartNew = true;
            else if (currentSentence.speaker !== speaker) shouldStartNew = true;
            else if (currentSentence.isTranslationOnly) shouldStartNew = true;
            else if (
                currentSentence.requiresTranslation !== null
                && currentSentence.requiresTranslation !== tokenRequiresTranslation
            ) {
                shouldStartNew = true;
            }

            if (shouldStartNew) {
                currentSentence = startSentence(speaker, {
                    requiresTranslation: tokenRequiresTranslation,
                });
            }
            if (currentSentence.requiresTranslation === null) {
                currentSentence.requiresTranslation = tokenRequiresTranslation;
            }

            if (currentSentence.originalLang === null && token.language) {
                currentSentence.originalLang = token.language;
            } else if (
                currentSentence.originalLang
                && token.language
                && currentSentence.originalLang !== token.language
            ) {
                const tokenLlmId = token.llm_sentence_id ? String(token.llm_sentence_id) : '';
                if (!(tokenLlmId && sourceSentenceIds.get(currentSentence) === tokenLlmId)) {
                    currentSentence = startSentence(speaker, {
                        requiresTranslation: tokenRequiresTranslation,
                    });
                    currentSentence.originalLang = token.language;
                }
            }

            if (!sourceSentenceIds.get(currentSentence) && token.llm_sentence_id) {
                sourceSentenceIds.set(currentSentence, String(token.llm_sentence_id));
            }
            currentSentence.originalTokens.push(token);
        });

        // Preserve getLlmSentenceId's original-first resolution for sentences
        // whose id came only from a translation token.
        sentences.forEach((sentence) => {
            sentence.llmSentenceId = resolveLlmSentenceId(sentence);
        });
        return sentences;
    }

    function getDisplayFlags(displayMode, suppressTranslationDisplay) {
        return {
            showTranslation: !suppressTranslationDisplay
                && (displayMode === 'both' || displayMode === 'translation'),
            showOriginal: suppressTranslationDisplay
                ? true
                : (displayMode === 'both' || displayMode === 'original'),
        };
    }

    function buildSpeakerBlocks(sentences = [], {
        displayMode = 'both',
        suppressTranslationDisplay = false,
    } = {}) {
        const { showOriginal, showTranslation } = getDisplayFlags(
            displayMode,
            suppressTranslationDisplay,
        );
        const speakerBlocks = [];
        let currentBlock = null;
        let fallbackCounter = 0;

        sentences.forEach((sentence) => {
            sentence.renderKey = null;
            const hasOriginal = showOriginal && sentence.originalTokens.length > 0;
            const hasTranslation = showTranslation && sentence.translationTokens.length > 0;
            if (!hasOriginal && !hasTranslation) return;

            sentence.renderKey = getSentenceRenderKey(sentence, fallbackCounter);
            fallbackCounter += 1;
            if (!currentBlock || currentBlock.speaker !== sentence.speaker) {
                currentBlock = { speaker: sentence.speaker, sentences: [] };
                speakerBlocks.push(currentBlock);
            }
            currentBlock.sentences.push(sentence);
        });

        return { speakerBlocks, showOriginal, showTranslation };
    }

    function buildRenderModel({
        tokens = [],
        displayMode = 'both',
        suppressTranslationDisplay = false,
    } = {}) {
        const sentences = buildSentences(tokens);
        const display = buildSpeakerBlocks(sentences, {
            displayMode,
            suppressTranslationDisplay,
        });
        return { tokens, sentences, ...display };
    }

    return {
        ensureSpeakerValue,
        shouldSpeculativelySplitAfterToken,
        buildRenderTokens,
        resolveLlmSentenceId,
        getSentenceRenderKey,
        buildSentences,
        buildSpeakerBlocks,
        buildRenderModel,
    };
});

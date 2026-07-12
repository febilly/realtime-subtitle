(function (root) {
    'use strict';

    function create(options = {}) {
        const tokenStream = options.TokenStream || root.TokenStream;
        const renderModel = options.RenderModel || root.RenderModel;
        const refineStateApi = options.RefineState || root.RefineState;
        if (!tokenStream || !renderModel || !refineStateApi) {
            throw new TypeError('SubtitleSession.create requires TokenStream, RenderModel, and RefineState');
        }

        let allFinalTokens = [];
        let currentNonFinalTokens = [];
        let lastMergedIndex = 0;
        let tokenSequenceCounter = 0;
        let llmTranslateHideAfterSequence = options.translateMode ? 0 : null;
        let hybridInterimAfterSequence = options.translationUiMode === 'hybrid' ? 0 : null;
        const refineState = refineStateApi.createRefineState(options.refineStateOptions);

        function assignSequenceIndex(token) {
            tokenSequenceCounter = tokenStream.assignSequenceIndex(token, tokenSequenceCounter);
            return token;
        }

        function assignRenderTokenSequences(tokens) {
            for (const token of tokens || []) assignSequenceIndex(token);
            return tokens;
        }

        function insertFinalToken(token) {
            tokenSequenceCounter = tokenStream.insertFinalToken(
                allFinalTokens,
                token,
                tokenSequenceCounter,
            );
            return token;
        }

        function mergeFinalTokens() {
            lastMergedIndex = tokenStream.mergeFinalTokens(allFinalTokens, lastMergedIndex);
            return allFinalTokens;
        }

        function applyUpdateFrame(data = {}) {
            let hasNewFinalContent = false;
            if (Array.isArray(data.final_tokens)) {
                for (const token of data.final_tokens) {
                    if (!token || token.text === '<end>') continue;
                    hasNewFinalContent = true;
                    insertFinalToken(token);
                }
            }
            currentNonFinalTokens = (data.non_final_tokens || [])
                .filter((token) => token && token.text !== '<end>');
            currentNonFinalTokens.forEach(assignSequenceIndex);
            if (hasNewFinalContent) mergeFinalTokens();
            return { changed: true, hasNewFinalContent };
        }

        function applyRefineResult(data, { translateMode = false } = {}) {
            const changed = refineState.applyRefineResult(data, { translateMode });
            return { changed, invalidateRenderCache: false };
        }

        function markSpecPending(source, targetLanguage) {
            return {
                changed: refineState.markSpecPending(source, targetLanguage),
                invalidateRenderCache: false,
            };
        }

        function applySpecTranslation(data) {
            return {
                changed: refineState.applySpecTranslation(data),
                invalidateRenderCache: false,
            };
        }

        function retract(sentenceId) {
            const id = sentenceId === null || sentenceId === undefined
                ? ''
                : String(sentenceId).trim();
            if (!id) return { changed: false, removed: false, invalidateRenderCache: false };
            refineState.cleanupSentenceCaches(id);
            const removal = refineState.removeSentenceTokens(allFinalTokens, id);
            if (!removal.removed) {
                return { changed: false, removed: false, invalidateRenderCache: false };
            }
            allFinalTokens = removal.tokens;
            lastMergedIndex = Math.max(0, allFinalTokens.length - 1);
            mergeFinalTokens();
            return { changed: true, removed: true, invalidateRenderCache: true };
        }

        function finalizeCurrentNonFinalTokens() {
            const pendingTokens = currentNonFinalTokens
                .filter((token) => token && token.text && token.text !== '<end>');
            if (pendingTokens.length === 0) {
                return { changed: false, invalidateRenderCache: false };
            }
            for (const token of pendingTokens) {
                insertFinalToken({ ...token, is_final: true });
            }
            insertFinalToken({
                is_separator: true,
                is_final: true,
                separator_type: 'reconnect',
            });
            currentNonFinalTokens = [];
            mergeFinalTokens();
            return { changed: true, invalidateRenderCache: true };
        }

        function clear({ translateMode = false, translationUiMode = 'fast' } = {}) {
            allFinalTokens = [];
            currentNonFinalTokens = [];
            lastMergedIndex = 0;
            tokenSequenceCounter = 0;
            refineState.clear();
            llmTranslateHideAfterSequence = translateMode ? tokenSequenceCounter : null;
            hybridInterimAfterSequence = translationUiMode === 'hybrid'
                ? tokenSequenceCounter
                : null;
            return { changed: true, invalidateRenderCache: true };
        }

        function applyLlmMode(mode, previousMode) {
            if (mode === 'translate') {
                if (previousMode !== 'translate' || llmTranslateHideAfterSequence === null) {
                    llmTranslateHideAfterSequence = tokenSequenceCounter + 1;
                }
            } else {
                llmTranslateHideAfterSequence = null;
            }
            return llmTranslateHideAfterSequence;
        }

        function disableLlmBoundary() {
            llmTranslateHideAfterSequence = null;
        }

        function noteHybridBoundary(mode, previousMode) {
            if (mode === 'hybrid') {
                if (previousMode !== 'hybrid') {
                    hybridInterimAfterSequence = tokenSequenceCounter + 1;
                }
            } else {
                hybridInterimAfterSequence = null;
            }
            return hybridInterimAfterSequence;
        }

        function revealPendingForRestart() {
            const result = { translateChanged: false, hybridChanged: false };
            if (llmTranslateHideAfterSequence !== null) {
                llmTranslateHideAfterSequence = tokenSequenceCounter;
                result.translateChanged = true;
            }
            if (hybridInterimAfterSequence !== null) {
                hybridInterimAfterSequence = tokenSequenceCounter;
                result.hybridChanged = true;
            }
            return result;
        }

        function buildRenderTokens() {
            return renderModel.buildRenderTokens({ allFinalTokens, currentNonFinalTokens });
        }

        function getDebugState() {
            return {
                allFinalTokens,
                currentNonFinalTokens,
                lastMergedIndex,
                tokenSequenceCounter,
                llmTranslateHideAfterSequence,
                hybridInterimAfterSequence,
            };
        }

        return {
            applyLlmMode,
            applyRefineResult,
            applySpecTranslation,
            applyUpdateFrame,
            assignRenderTokenSequences,
            buildRenderTokens,
            clear,
            disableLlmBoundary,
            finalizeCurrentNonFinalTokens,
            getConfirmed: refineState.isConfirmed,
            getDebugState,
            getHybridInterimAfterSequence: () => hybridInterimAfterSequence,
            getLlmTranslateHideAfterSequence: () => llmTranslateHideAfterSequence,
            getRefinedTranslation: refineState.getRefinedTranslation,
            getSpecPendingLanguage: refineState.getSpecPendingLanguage,
            getSpecTranslation: refineState.getSpecTranslation,
            getTranslationLanguage: refineState.getTranslationLanguage,
            getTranslationOverride: refineState.getTranslationOverride,
            isSpecPending: refineState.isSpecPending,
            markSpecPending,
            noteHybridBoundary,
            retract,
            revealPendingForRestart,
        };
    }

    const api = { create };
    root.SubtitleSession = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

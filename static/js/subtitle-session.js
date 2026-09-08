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
        // Local ASR rows are authoritative snapshots keyed by their stable id.
        // Keep their insertion order separately so an unrelated upsert cannot
        // reorder visible rows, and update token objects in place so the
        // renderer keeps the same sentence DOM node across revisions.
        const localSegments = new Map();
        let localSegmentOrder = [];
        const localTombstones = new Set();
        let localOrderCounter = 0;
        // Render keys describe token identity; these positions describe when a
        // complete ordinary/local display unit first arrived. Keep them apart
        // so revisions retain their DOM key while mixed providers stay in time
        // order.
        const ordinaryDisplayPositions = new Map();
        let displayPositionCounter = 0;
        let lastArrivalKind = null;
        const refineState = refineStateApi.createRefineState(options.refineStateOptions);

        function assignSequenceIndex(token) {
            tokenSequenceCounter = tokenStream.assignSequenceIndex(token, tokenSequenceCounter);
            return token;
        }

        function assignRenderTokenSequences(tokens) {
            for (const token of tokens || []) {
                if (token && token.separator_type === 'speculative') continue;
                assignSequenceIndex(token);
            }
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

        function nextDisplayPosition() {
            displayPositionCounter += 1;
            return displayPositionCounter;
        }

        function rememberOrdinaryDisplayPosition(token, position) {
            if (!token || typeof token._sequenceIndex !== 'number') return;
            ordinaryDisplayPositions.set(token._sequenceIndex, position);
        }

        function addOrdinaryBridge() {
            const previous = allFinalTokens[allFinalTokens.length - 1];
            if (previous && !previous.is_separator) {
                allFinalTokens.push({
                    is_separator: true,
                    is_final: !!previous.is_final,
                    separator_type: 'local-bridge',
                });
            }
        }

        function applyUpdateFrame(data = {}) {
            let hasNewFinalContent = false;
            if (Array.isArray(data.final_tokens)) {
                const finalTokens = data.final_tokens
                    .filter((token) => token && token.text !== '<end>');
                if (finalTokens.length && lastArrivalKind === 'local') {
                    addOrdinaryBridge();
                }
                const position = finalTokens.length ? nextDisplayPosition() : null;
                for (const token of finalTokens) {
                    hasNewFinalContent = true;
                    insertFinalToken(token);
                    rememberOrdinaryDisplayPosition(token, position);
                }
                if (finalTokens.length) lastArrivalKind = 'ordinary';
            }
            if (Object.prototype.hasOwnProperty.call(data, 'non_final_tokens')) {
                currentNonFinalTokens = (data.non_final_tokens || [])
                    .filter((token) => token && token.text !== '<end>');
                currentNonFinalTokens.forEach(assignSequenceIndex);
                if (currentNonFinalTokens.length) {
                    const position = nextDisplayPosition();
                    currentNonFinalTokens.forEach((token) => {
                        if (!token.is_separator) rememberOrdinaryDisplayPosition(token, position);
                    });
                    lastArrivalKind = 'ordinary';
                }
            }
            if (hasNewFinalContent) mergeFinalTokens();
            const localUpdate = applyLocalSegments(data);
            if (localUpdate.arrived) {
                lastArrivalKind = localSegments.size ? 'local' : (
                    allFinalTokens.length || currentNonFinalTokens.length ? 'ordinary' : null
                );
            }
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

        function normalizeLocalSegment(segment) {
            if (!segment || segment.id === null || segment.id === undefined) return null;
            const id = String(segment.id).trim();
            const revision = Number(segment.revision);
            if (!id || !Number.isFinite(revision)) return null;
            return {
                id,
                source: segment.source === null || segment.source === undefined
                    ? ''
                    : String(segment.source),
                translation: segment.translation === null || segment.translation === undefined
                    ? ''
                    : String(segment.translation),
                source_language: segment.source_language === null
                    || segment.source_language === undefined
                    ? ''
                    : String(segment.source_language),
                target_language: segment.target_language === null
                    || segment.target_language === undefined
                    ? ''
                    : String(segment.target_language),
                order: Number.isFinite(Number(segment.order)) ? Number(segment.order) : null,
                revision,
                is_final: !!segment.is_final,
                requires_translation: segment.requires_translation !== false,
            };
        }

        function applyLocalSegments(data = {}) {
            let changed = false;
            let arrived = false;
            if (data.local_reset) {
                if (localSegments.size > 0 || localSegmentOrder.length > 0) changed = true;
                localSegments.clear();
                localSegmentOrder = [];
                localTombstones.clear();
                localOrderCounter = 0;
            }

            const removedIds = Array.isArray(data.local_removed_segment_ids)
                ? data.local_removed_segment_ids
                : [];
            for (const value of removedIds) {
                if (value === null || value === undefined) continue;
                const id = String(value).trim();
                if (!id) continue;
                localTombstones.add(id);
                if (localSegments.delete(id)) {
                    localSegmentOrder = localSegmentOrder.filter((entryId) => entryId !== id);
                    changed = true;
                }
            }

            const incoming = (Array.isArray(data.local_segments) ? data.local_segments : [])
                .map((rawSegment, index) => ({ segment: normalizeLocalSegment(rawSegment), index }))
                .filter((item) => item.segment)
                .sort((left, right) => {
                    const leftOrder = left.segment.order === null
                        ? Number.MAX_SAFE_INTEGER : left.segment.order;
                    const rightOrder = right.segment.order === null
                        ? Number.MAX_SAFE_INTEGER : right.segment.order;
                    return leftOrder - rightOrder || left.index - right.index;
                });
            for (const item of incoming) {
                const { segment } = item;
                if (!segment) continue;
                if (localTombstones.has(segment.id)) continue;
                const existing = localSegments.get(segment.id);
                if (existing && segment.revision <= existing.revision) continue;

                if (!existing) {
                    const originalToken = {
                        text: '',
                        is_final: false,
                        // The historical local provider and local IPC both
                        // render as speaker 0. Keep these row snapshots in
                        // that same block when the two protocols coexist.
                        speaker: '0',
                        language: '',
                        source_language: '',
                        translation_status: 'original',
                        llm_sentence_id: segment.id,
                    };
                    tokenSequenceCounter = tokenStream.assignSequenceIndex(
                        originalToken,
                        tokenSequenceCounter,
                    );
                    const entry = {
                        ...segment,
                        orderIndex: localOrderCounter++,
                        displayPosition: null,
                        originalToken,
                        translationToken: null,
                    };
                    localSegments.set(segment.id, entry);
                    localSegmentOrder.push(segment.id);
                    sortLocalSegmentOrder();
                    entry.displayPosition = allocateLocalDisplayPosition(entry.id);
                    arrived = arrived || entry.displayPosition >= displayPositionCounter;
                    updateLocalSegmentTokens(entry);
                    changed = true;
                    continue;
                }

                Object.assign(existing, segment);
                updateLocalSegmentTokens(existing);
                sortLocalSegmentOrder();
                changed = true;
            }
            return { changed, arrived };
        }

        function sortLocalSegmentOrder() {
            localSegmentOrder.sort((leftId, rightId) => {
                const left = localSegments.get(leftId);
                const right = localSegments.get(rightId);
                if (!left || !right) return left ? -1 : (right ? 1 : 0);
                const leftOrder = left.order === null ? left.orderIndex : left.order;
                const rightOrder = right.order === null ? right.orderIndex : right.order;
                if (leftOrder !== rightOrder) return leftOrder - rightOrder;
                return left.orderIndex - right.orderIndex;
            });
        }

        function allocateLocalDisplayPosition(id) {
            const index = localSegmentOrder.indexOf(id);
            const previous = index > 0 ? localSegments.get(localSegmentOrder[index - 1]) : null;
            const next = index >= 0 && index + 1 < localSegmentOrder.length
                ? localSegments.get(localSegmentOrder[index + 1])
                : null;
            if (previous && next) return (previous.displayPosition + next.displayPosition) / 2;
            if (previous) return nextDisplayPosition();
            if (next) return next.displayPosition - 0.001;
            return nextDisplayPosition();
        }

        function updateLocalSegmentTokens(entry) {
            const sourceToken = entry.originalToken;
            sourceToken.text = entry.source;
            sourceToken.is_final = entry.is_final;
            sourceToken.language = entry.source_language;
            sourceToken.source_language = entry.source_language;
            sourceToken.translation_status = entry.requires_translation ? 'original' : 'none';
            sourceToken.llm_sentence_id = entry.id;

            if (entry.translation) {
                if (!entry.translationToken) {
                    entry.translationToken = {
                        text: '',
                        is_final: entry.is_final,
                        speaker: sourceToken.speaker,
                        language: entry.target_language,
                        source_language: entry.source_language,
                        translation_status: 'translation',
                        llm_sentence_id: entry.id,
                    };
                    tokenSequenceCounter = tokenStream.assignSequenceIndex(
                        entry.translationToken,
                        tokenSequenceCounter,
                    );
                }
                entry.translationToken.text = entry.translation;
                entry.translationToken.is_final = entry.is_final;
                entry.translationToken.language = entry.target_language;
                entry.translationToken.source_language = entry.source_language;
                entry.translationToken.llm_sentence_id = entry.id;
            } else {
                entry.translationToken = null;
            }
        }

        function buildLocalRenderGroups() {
            const groups = [];
            for (const id of localSegmentOrder) {
                const entry = localSegments.get(id);
                if (!entry) continue;
                const tokens = [entry.originalToken];
                if (entry.translationToken) tokens.push(entry.translationToken);
                tokens.push({
                    is_separator: true,
                    is_final: entry.is_final,
                    separator_type: 'local',
                    llm_sentence_id: entry.id,
                });
                groups.push({
                    kind: 'local',
                    position: entry.displayPosition,
                    insertionOrder: entry.orderIndex,
                    tokens,
                });
            }
            return groups;
        }

        function buildOrdinaryRenderGroups(tokens) {
            const groups = [];
            let current = [];
            let position = null;
            let insertionOrder = 0;
            const flush = () => {
                if (!current.length) return;
                groups.push({
                    kind: 'ordinary',
                    position: position === null ? Number.MAX_SAFE_INTEGER : position,
                    insertionOrder: insertionOrder++,
                    tokens: current,
                });
                current = [];
                position = null;
            };
            for (const token of tokens) {
                const tokenPosition = token
                    && typeof token._sequenceIndex === 'number'
                    && ordinaryDisplayPositions.has(token._sequenceIndex)
                    ? ordinaryDisplayPositions.get(token._sequenceIndex)
                    : null;
                if (
                    current.length
                    && position !== null
                    && tokenPosition !== null
                    && tokenPosition !== position
                ) {
                    flush();
                }
                current.push(token);
                if (position === null && tokenPosition !== null) position = tokenPosition;
                if (token && token.is_separator) flush();
            }
            flush();
            return groups;
        }

        function mergeRenderGroups(ordinaryGroups, localGroups) {
            const result = [];
            let previousKind = null;
            const orderedGroups = [...ordinaryGroups, ...localGroups]
                .sort((left, right) => (
                    left.position - right.position || left.insertionOrder - right.insertionOrder
                ));
            for (const group of orderedGroups) {
                const previous = result[result.length - 1];
                const first = group.tokens[0];
                // Groups are created only at existing separators. A local row
                // therefore never lands inside an ordinary source/translation
                // unit; this bridge closes that complete unit before the next
                // chronological group starts.
                if (previous && first && !previous.is_separator && !first.is_separator
                    && (previousKind === 'local' || group.kind === 'local')) {
                    result.push({
                        is_separator: true,
                        is_final: !!previous.is_final,
                        separator_type: 'local-bridge',
                    });
                }
                result.push(...group.tokens);
                previousKind = group.kind;
            }
            return result;
        }

        function clear({ translateMode = false, translationUiMode = 'fast' } = {}) {
            allFinalTokens = [];
            currentNonFinalTokens = [];
            localSegments.clear();
            localSegmentOrder = [];
            localTombstones.clear();
            localOrderCounter = 0;
            ordinaryDisplayPositions.clear();
            displayPositionCounter = 0;
            lastArrivalKind = null;
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
            const ordinaryTokens = renderModel.buildRenderTokens({
                allFinalTokens,
                currentNonFinalTokens,
            });
            if (localSegments.size === 0) return ordinaryTokens;
            return mergeRenderGroups(
                buildOrdinaryRenderGroups(ordinaryTokens),
                buildLocalRenderGroups(),
            );
        }

        function getDebugState() {
            return {
                allFinalTokens,
                currentNonFinalTokens,
                localSegments: localSegmentOrder.map((id) => localSegments.get(id)),
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

(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const container = options.container;
        const session = options.session;
        const scroll = options.scroll;
        const furiganaService = options.furiganaService;
        const renderModel = options.RenderModel || root.RenderModel;
        const renderHtmlApi = options.RenderHtml || root.RenderHtml;
        const segmentation = options.Segmentation || root.Segmentation;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const escapeHtml = options.escapeHtml;
        const getViewState = typeof options.getViewState === 'function'
            ? options.getViewState
            : () => ({});
        if (!container || !session || !scroll || !furiganaService) {
            throw new TypeError('SubtitleRenderer.create requires container, session, scroll, and furiganaService');
        }
        if (!renderModel || !renderHtmlApi || !segmentation || typeof escapeHtml !== 'function') {
            throw new TypeError('SubtitleRenderer.create requires render model/html, segmentation, and escapeHtml');
        }
        if (
            typeof furiganaService.getCached !== 'function'
            || typeof furiganaService.request !== 'function'
            || typeof furiganaService.clearPending !== 'function'
        ) {
            throw new TypeError('SubtitleRenderer.create requires a complete furigana service');
        }

        const renderedSentences = new Map();
        const renderedBlocks = new Map();
        const htmlRenderer = renderHtmlApi.createRenderHtml({
            document: documentRef,
            escapeHtml,
            t,
        });
        const domPatcher = htmlRenderer.createDomPatcher({
            container,
            renderedSentences,
            renderedBlocks,
        });
        const {
            getLangDir,
            getLanguageTag,
            renderTokenSpan,
            renderTokenSpansTrimmed,
            wrapSubtitleLineBody,
        } = htmlRenderer;
        const { endsWithSentenceEnding, splitIntoSentenceSegments } = segmentation;

        function state() {
            const value = getViewState();
            return value && typeof value === 'object' ? value : {};
        }

        function getLlmSentenceId(sentence) {
            return renderModel.resolveLlmSentenceId(sentence);
        }

        function sentenceHasTranslationTokenAtOrAfter(sentence, threshold) {
            if (threshold === null || threshold === undefined) return false;
            if (!sentence || !Array.isArray(sentence.translationTokens)) return false;
            return sentence.translationTokens.some((token) => {
                const sequence = token && typeof token._sequenceIndex === 'number'
                    ? token._sequenceIndex
                    : null;
                return sequence !== null && sequence >= threshold;
            });
        }

        function shouldHideBuiltinTranslation(sentence, sourceText, hasRefined, view) {
            if (!view.translateMode || !sourceText || hasRefined) return false;
            return sentenceHasTranslationTokenAtOrAfter(
                sentence,
                session.getLlmTranslateHideAfterSequence(),
            );
        }

        function getDisplayTranslation(sentence, originalTranslation) {
            const sentenceId = getLlmSentenceId(sentence);
            return (sentenceId && session.getRefinedTranslation(sentenceId)) || originalTranslation;
        }

        function getSentenceId(sentence, fallbackIndex) {
            return sentence.renderKey || renderModel.getSentenceRenderKey(sentence, fallbackIndex);
        }

        function renderOriginalLine(sentence, sentenceId, sentenceDir, view) {
            if (!view.showOriginal || sentence.originalTokens.length === 0) {
                return { blocked: false, pendingFurigana: false, html: '' };
            }
            const languageTag = getLanguageTag(sentence.originalLang);
            const isJapanese = sentence.originalLang === 'ja';
            if (isJapanese && view.furiganaEnabled) {
                const plainText = sentence.originalTokens.map((token) => token.text).join('');
                const hasNonFinal = sentence.originalTokens.some((token) => !token.is_final);
                if (plainText.trim().length === 0) {
                    const lineContent = sentence.originalTokens
                        .map((token) => renderTokenSpan(token))
                        .join('');
                    return {
                        blocked: false,
                        pendingFurigana: false,
                        html: `<div class="subtitle-line original-line" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${languageTag}${wrapSubtitleLineBody(lineContent, sentenceDir, sentence.originalLang)}</div>`,
                    };
                }
                const rubyHtml = furiganaService.getCached(plainText);
                if (rubyHtml) {
                    const classes = ['subtitle-text'];
                    if (hasNonFinal) classes.push('non-final');
                    const rubySpan = `<span class="${classes.join(' ')}">${rubyHtml}</span>`;
                    return {
                        blocked: false,
                        pendingFurigana: false,
                        html: `<div class="subtitle-line original-line subtitle-line--furigana" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${wrapSubtitleLineBody(`${languageTag}${rubySpan}`, sentenceDir, sentence.originalLang)}</div>`,
                    };
                }
                furiganaService.request(plainText);
                const previousHtml = renderedSentences.get(sentenceId);
                return {
                    blocked: !previousHtml,
                    pendingFurigana: true,
                    html: previousHtml || '',
                };
            }
            const lineContent = renderTokenSpansTrimmed(sentence.originalTokens);
            return {
                blocked: false,
                pendingFurigana: false,
                html: `<div class="subtitle-line original-line" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${languageTag}${wrapSubtitleLineBody(lineContent, sentenceDir, sentence.originalLang)}</div>`,
            };
        }

        function renderExistingTranslation(sentence, view) {
            const translationDirection = getLangDir(sentence.translationLang);
            const languageTag = getLanguageTag(sentence.translationLang);
            const baseTranslation = sentence.translationTokens
                .map((token) => (token && token.text) ? String(token.text) : '')
                .join('');
            let normalizedTranslation = baseTranslation.trim();
            const sourceText = sentence.originalTokens
                .map((token) => (token && token.text) ? String(token.text) : '')
                .join('')
                .trim();
            const sentenceId = getLlmSentenceId(sentence);
            const override = sentenceId ? session.getTranslationOverride(sentenceId) : null;
            if (override) normalizedTranslation = override;
            const hasRefined = !!(sentenceId && session.getRefinedTranslation(sentenceId));
            const confirmed = !!(sentenceId && session.getConfirmed(sentenceId));
            const hidden = shouldHideBuiltinTranslation(
                sentence,
                sourceText,
                hasRefined || confirmed,
                view,
            );
            let body;
            let lineClass = 'subtitle-line';
            if (hidden) {
                body = `<span class="subtitle-text placeholder" lang="${sentence.translationLang || ''}">&nbsp;</span>`;
            } else {
                const displayTranslation = override
                    ? override
                    : ((sourceText && normalizedTranslation)
                        ? getDisplayTranslation(sentence, normalizedTranslation)
                        : normalizedTranslation);
                if (displayTranslation && displayTranslation !== normalizedTranslation) {
                    body = `<span class="subtitle-text" lang="${sentence.translationLang || ''}">${escapeHtml(displayTranslation)}</span>`;
                } else if (override) {
                    body = `<span class="subtitle-text" lang="${sentence.translationLang || ''}">${escapeHtml(displayTranslation || '')}</span>`;
                } else {
                    const provisional = view.translationUiMode === 'hybrid'
                        && !confirmed
                        && sentenceHasTranslationTokenAtOrAfter(
                            sentence,
                            session.getHybridInterimAfterSequence(),
                        );
                    body = renderTokenSpansTrimmed(sentence.translationTokens, null, {
                        normalizeTranslationSpacing: true,
                    });
                    if (provisional) lineClass += ' subtitle-line--stt-interim';
                }
            }
            return `<div class="${lineClass}" lang="${sentence.translationLang || ''}" dir="${translationDirection}">${languageTag}${wrapSubtitleLineBody(body, translationDirection, sentence.translationLang)}</div>`;
        }

        function renderAccurateTranslation(sentence, view) {
            const sentenceId = getLlmSentenceId(sentence);
            const override = sentenceId
                ? (session.getTranslationOverride(sentenceId) || '').trim()
                : '';
            const sourceText = sentence.originalTokens
                .map((token) => (token && token.text) ? String(token.text) : '')
                .join('')
                .trim();
            if (override && override !== sourceText) {
                const language = session.getTranslationLanguage(sentenceId)
                    || view.currentTranslationTargetLang
                    || '';
                const direction = getLangDir(language);
                const languageTag = getLanguageTag(language);
                const body = `<span class="subtitle-text" lang="${language}">${escapeHtml(override)}</span>`;
                return `<div class="subtitle-line" lang="${language}" dir="${direction}">${languageTag}${wrapSubtitleLineBody(body, direction, language)}</div>`;
            }
            if (override || !sourceText) return '';

            const parts = [];
            let language = '';
            const whole = session.getSpecTranslation(sourceText);
            if (whole) {
                parts.push({ text: whole.text, pending: false });
                language = whole.lang;
            } else {
                const wholePending = session.isSpecPending(sourceText);
                if (wholePending) {
                    language = session.getSpecPendingLanguage(sourceText) || '';
                }
                for (const segment of splitIntoSentenceSegments(sourceText)) {
                    if (!endsWithSentenceEnding(segment)) continue;
                    const hit = session.getSpecTranslation(segment);
                    if (hit) {
                        parts.push({ text: hit.text, pending: false });
                        if (!language) language = hit.lang;
                    } else if (wholePending || session.isSpecPending(segment)) {
                        parts.push({ text: '', pending: true });
                        if (!language) language = session.getSpecPendingLanguage(segment) || '';
                    }
                }
                if (wholePending && !parts.length) {
                    parts.push({ text: '', pending: true });
                }
            }
            if (!parts.length) return '';
            language = language || view.currentTranslationTargetLang || '';
            const direction = getLangDir(language);
            const languageTag = getLanguageTag(language);
            const body = parts.map((part) => (
                part.pending
                    ? `<span class="subtitle-text placeholder" lang="${language}">&nbsp;</span>`
                    : `<span class="subtitle-text non-final" lang="${language}">${escapeHtml(part.text)}</span>`
            )).join('');
            return `<div class="subtitle-line" lang="${language}" dir="${direction}">${languageTag}${wrapSubtitleLineBody(body, direction, language)}</div>`;
        }

        function render() {
            const scrollState = scroll.capture();
            const tokens = session.buildRenderTokens();
            session.assignRenderTokenSequences(tokens);
            if (tokens.length === 0) {
                container.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
                scroll.reset();
                return { rendered: false, empty: true, blocked: false };
            }

            const viewState = state();
            const model = renderModel.buildRenderModel({
                tokens,
                displayMode: viewState.displayMode,
                suppressTranslationDisplay: viewState.suppressTranslationDisplay,
            });
            const { speakerBlocks, showOriginal, showTranslation } = model;
            if (speakerBlocks.length === 0) {
                container.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
                scroll.restoreAfterEmpty(scrollState);
                return { rendered: false, empty: true, blocked: false };
            }

            let html = '';
            let previousSpeaker = null;
            let fallbackCounter = 0;
            let blockingUpdate = false;
            for (const block of speakerBlocks) {
                if (blockingUpdate) break;
                const firstDirection = block.sentences.length
                    ? getLangDir(block.sentences[0].originalLang)
                    : 'ltr';
                const sentenceHtml = [];
                for (const sentence of block.sentences) {
                    const sentenceId = getSentenceId(sentence, fallbackCounter++);
                    const parts = [];
                    const sentenceDirection = getLangDir(sentence.originalLang);
                    const original = renderOriginalLine(
                        sentence,
                        sentenceId,
                        sentenceDirection,
                        { ...viewState, showOriginal },
                    );
                    if (original.pendingFurigana) {
                        if (original.html) sentenceHtml.push(original.html);
                        if (original.blocked) blockingUpdate = true;
                        continue;
                    }
                    if (original.html) parts.push(original.html);
                    if (original.blocked) {
                        blockingUpdate = true;
                        break;
                    }
                    if (showTranslation && sentence.translationTokens.length > 0) {
                        parts.push(renderExistingTranslation(sentence, viewState));
                    } else if (
                        showTranslation
                        && viewState.translationUiMode === 'accurate'
                        && viewState.translateMode
                    ) {
                        const accurate = renderAccurateTranslation(sentence, viewState);
                        if (accurate) parts.push(accurate);
                    }
                    if (!parts.length) continue;
                    sentenceHtml.push(htmlRenderer.renderSentenceHtml(sentenceId, parts));
                }
                if (blockingUpdate) break;
                const blockHtml = htmlRenderer.renderSpeakerBlockHtml({
                    speaker: block.speaker,
                    sentenceHtml,
                    previousSpeaker,
                    direction: firstDirection,
                    showSpeakerLabel: viewState.speakerDiarizationEnabled
                        && !viewState.hideSpeakerLabels,
                });
                if (blockHtml) {
                    html += blockHtml;
                    previousSpeaker = block.speaker;
                }
            }

            if (blockingUpdate) {
                return { rendered: false, empty: false, blocked: true };
            }
            if (!html) {
                container.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
                scroll.restoreAfterEmpty(scrollState);
                return { rendered: false, empty: true, blocked: false };
            }
            domPatcher.patch(html);
            scroll.completeRender(scrollState);
            return { rendered: true, empty: false, blocked: false };
        }

        function invalidateSentences() {
            renderedSentences.clear();
        }

        function invalidateAll() {
            renderedSentences.clear();
            renderedBlocks.clear();
        }

        function clearSession() {
            invalidateAll();
            furiganaService.clearPending();
        }

        function getDebugState() {
            return { renderedSentences, renderedBlocks };
        }

        return {
            clearSession,
            getDebugState,
            invalidateAll,
            invalidateSentences,
            render,
        };
    }

    const api = { create };
    root.SubtitleRenderer = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

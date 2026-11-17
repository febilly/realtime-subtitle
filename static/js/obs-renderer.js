(function (root) {
    'use strict';

    const FONT_SIZE_DEFAULT = 42;
    const FONT_SIZE_MIN = 16;
    const FONT_SIZE_MAX = 120;
    const DISPLAY_MODES = Object.freeze(['both', 'original', 'translation']);
    const DEFAULT_SETTINGS = Object.freeze({
        fontSize: FONT_SIZE_DEFAULT,
        displayContent: 'both',
    });
    const MAX_RENDERED_SENTENCES = 20;

    function normalizeFontSize(value) {
        if (typeof value === 'boolean') return FONT_SIZE_DEFAULT;
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return FONT_SIZE_DEFAULT;
        return Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, Math.round(parsed)));
    }

    function normalizeDisplayContent(value) {
        if (typeof value !== 'string') return 'both';
        const normalized = value.trim().toLowerCase();
        return DISPLAY_MODES.includes(normalized) ? normalized : 'both';
    }

    function normalizeSettings(raw) {
        const source = raw && typeof raw === 'object' ? raw : {};
        return {
            fontSize: normalizeFontSize(
                source.fontSize !== undefined ? source.fontSize : source.font_size,
            ),
            displayContent: normalizeDisplayContent(
                source.displayContent !== undefined ? source.displayContent : source.display_content,
            ),
        };
    }

    function computeStyleVars(fontSize) {
        const fs = normalizeFontSize(fontSize);
        const tagFs = Math.max(9, Math.floor(fs * 0.55));
        const vars = {
            '--fs': `${fs}px`,
            '--a': '0.592',
            '--pad': `${Math.max(5, Math.round(fs * 0.26))}px`,
            '--tag-fs': `${tagFs}px`,
            '--tag-px': `${Math.max(5, Math.floor(tagFs * 0.6))}px`,
            '--tag-py': `${Math.max(2, Math.floor(tagFs * 0.3))}px`,
            '--tag-gap': `${Math.max(4, Math.floor(tagFs * 0.45))}px`,
        };
        const sentMb = Math.max(5, Math.floor(fs * 0.45));
        return { fs, tagFs, sentMb, vars };
    }

    function applyStyleVars(element, fontSize) {
        const computed = computeStyleVars(fontSize);
        if (element && element.style && typeof element.style.setProperty === 'function') {
            for (const [prop, val] of Object.entries(computed.vars)) {
                element.style.setProperty(prop, val);
            }
        }
        return computed;
    }

    function escapeHtmlFallback(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const container = options.container;
        const rootElement = options.rootElement || null;
        const session = options.session;
        const renderModel = options.RenderModel || root.RenderModel;
        const renderHtmlApi = options.RenderHtml || root.RenderHtml;
        const segmentation = options.Segmentation || root.Segmentation;

        if (!container || !session) {
            throw new TypeError('ObsRenderer.create requires container and session');
        }
        if (!renderModel || !renderHtmlApi || !segmentation) {
            throw new TypeError('ObsRenderer.create requires RenderModel, RenderHtml, and Segmentation');
        }

        const escapeHtml = typeof options.escapeHtml === 'function'
            ? options.escapeHtml
            : (documentRef && typeof documentRef.createElement === 'function'
                ? renderHtmlApi.createEscapeHtml(documentRef)
                : escapeHtmlFallback);
        const normalizeTranslationTokenTexts = typeof renderHtmlApi.normalizeTranslationTokenTexts === 'function'
            ? renderHtmlApi.normalizeTranslationTokenTexts
            : (tokens) => (Array.isArray(tokens) ? tokens.map((t) => (t && t.text ? String(t.text) : '')) : []);
        const getLangDir = typeof renderHtmlApi.getLangDir === 'function'
            ? renderHtmlApi.getLangDir
            : () => 'ltr';
        const { endsWithSentenceEnding, splitIntoSentenceSegments } = segmentation;
        const maxSentences = Number.isFinite(Number(options.maxSentences)) && Number(options.maxSentences) > 0
            ? Math.floor(Number(options.maxSentences))
            : MAX_RENDERED_SENTENCES;

        let settings = normalizeSettings(options.settings);
        let packEnabled = options.packEnabled !== undefined ? !!options.packEnabled : true;
        let uiState = {
            translateMode: false,
            translationUiMode: 'fast',
            suppressTranslationDisplay: false,
            currentTranslationTargetLang: '',
            ...(options.uiState && typeof options.uiState === 'object' ? options.uiState : {}),
        };

        applyStyleVars(rootElement || container, settings.fontSize);

        function renderTrimmedPlainRuns(tokens, { normalizeTranslationSpacing = false } = {}) {
            if (!Array.isArray(tokens) || tokens.length === 0) return '';
            const texts = normalizeTranslationSpacing
                ? normalizeTranslationTokenTexts(tokens)
                : tokens.map((token) => (token && token.text ? String(token.text) : ''));
            const getText = (index) => (texts[index] !== undefined ? texts[index] : '');

            let start = 0;
            let startText = '';
            while (start < tokens.length) {
                const raw = getText(start);
                const trimmed = raw.replace(/^\s+/, '');
                if (trimmed.length === 0 && /^\s*$/.test(raw)) {
                    start += 1;
                    continue;
                }
                startText = trimmed;
                break;
            }

            let end = tokens.length - 1;
            let endText = '';
            while (end >= start) {
                const raw = getText(end);
                const trimmed = raw.replace(/\s+$/, '');
                if (trimmed.length === 0 && /^\s*$/.test(raw)) {
                    end -= 1;
                    continue;
                }
                endText = trimmed;
                break;
            }
            if (start > end) return '';

            const runs = [];
            for (let index = start; index <= end; index += 1) {
                let text = getText(index);
                if (index === start) text = startText;
                if (index === end) text = endText;
                if (start === end) text = getText(start).trim();
                if (!text) continue;
                const isNonFinal = tokens[index] && tokens[index].is_final === false;
                const prev = runs[runs.length - 1];
                if (prev && prev.isNonFinal === isNonFinal) {
                    prev.text += text;
                } else {
                    runs.push({ isNonFinal, text });
                }
            }
            return runs.map((run) => (
                run.isNonFinal
                    ? `<span class="ov-nf">${escapeHtml(run.text)}</span>`
                    : escapeHtml(run.text)
            )).join('');
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

        function shouldHideBuiltinTranslation(sentence, sourceText, hasRefined) {
            if (!uiState.translateMode || !sourceText || hasRefined) return false;
            return sentenceHasTranslationTokenAtOrAfter(
                sentence,
                session.getLlmTranslateHideAfterSequence(),
            );
        }

        function formatLangTag(lang) {
            if (!lang) return '';
            return `<span class="ov-tag">${escapeHtml(String(lang).toUpperCase())}</span>`;
        }

        function formatOverlayLine(lang, bodyHtml, marginBottomPx) {
            if (!bodyHtml) return '';
            const safeLang = lang ? String(lang) : '';
            const dir = getLangDir(safeLang);
            const tagHtml = formatLangTag(safeLang);
            const langAttr = safeLang ? ` lang="${escapeHtml(safeLang)}"` : '';
            return `<div class="ov-line"${langAttr} dir="${dir}" style="margin-bottom:${marginBottomPx}px"><span class="ov-bg">${tagHtml}${bodyHtml}</span></div>`;
        }

        function buildOriginalBody(sentence) {
            if (!sentence || !Array.isArray(sentence.originalTokens) || !sentence.originalTokens.length) {
                return '';
            }
            return renderTrimmedPlainRuns(sentence.originalTokens);
        }

        function buildTranslationLine(sentence, showOriginal, marginBottomPx) {
            const sentenceId = renderModel.resolveLlmSentenceId(sentence);
            const sourceText = (sentence.originalTokens || [])
                .map((token) => (token && token.text ? String(token.text) : ''))
                .join('')
                .trim();

            if (Array.isArray(sentence.translationTokens) && sentence.translationTokens.length > 0) {
                const baseTranslation = sentence.translationTokens
                    .map((token) => (token && token.text ? String(token.text) : ''))
                    .join('');
                let normalizedTranslation = baseTranslation.trim();
                const override = sentenceId ? session.getTranslationOverride(sentenceId) : null;
                if (override) normalizedTranslation = override;
                const refined = sentenceId ? session.getRefinedTranslation(sentenceId) : null;
                const confirmed = !!(sentenceId && session.getConfirmed(sentenceId));
                if (shouldHideBuiltinTranslation(sentence, sourceText, !!(refined || confirmed))) {
                    return '';
                }
                const plainOverrideOrRefined = override || (sourceText && normalizedTranslation ? refined : null);
                const body = plainOverrideOrRefined
                    ? escapeHtml(plainOverrideOrRefined)
                    : renderTrimmedPlainRuns(sentence.translationTokens, {
                        normalizeTranslationSpacing: true,
                    });
                return formatOverlayLine(sentence.translationLang, body, marginBottomPx);
            }

            if (uiState.translationUiMode === 'accurate' && (!showOriginal || sentence.originalTokens.length > 0)) {
                const override = sentenceId ? (session.getTranslationOverride(sentenceId) || '').trim() : '';
                if (override && override !== sourceText) {
                    const lang = session.getTranslationLanguage(sentenceId)
                        || uiState.currentTranslationTargetLang
                        || '';
                    return formatOverlayLine(lang, escapeHtml(override), marginBottomPx);
                }
                if (override || !sourceText) return '';

                const parts = [];
                let lang = '';
                const whole = session.getSpecTranslation(sourceText);
                if (whole) {
                    parts.push({ text: whole.text, speculative: true });
                    lang = whole.lang;
                } else {
                    for (const segment of splitIntoSentenceSegments(sourceText)) {
                        if (!endsWithSentenceEnding(segment)) continue;
                        const hit = session.getSpecTranslation(segment);
                        if (hit) {
                            parts.push({ text: hit.text, speculative: true });
                            if (!lang) lang = hit.lang;
                        }
                    }
                }
                if (!parts.length) return '';
                lang = lang || uiState.currentTranslationTargetLang || '';
                const body = parts
                    .map((part) => `<span class="ov-nf">${escapeHtml(part.text)}</span>`)
                    .join('');
                return formatOverlayLine(lang, body, marginBottomPx);
            }

            return '';
        }

        function setSettings(next) {
            settings = normalizeSettings(next);
            applyStyleVars(rootElement || container, settings.fontSize);
            return { ...settings };
        }

        function getSettings() {
            return { ...settings };
        }

        function setPackEnabled(enabled) {
            packEnabled = !!enabled;
            return packEnabled;
        }

        function isPackEnabled() {
            return packEnabled;
        }

        function setUiState(next = {}) {
            if (!next || typeof next !== 'object') return { ...uiState };
            uiState = { ...uiState, ...next };
            return { ...uiState };
        }

        function invalidateAll() {
            // OBS renderer renders directly from session state on each tick.
        }

        function render() {
            const { sentMb } = applyStyleVars(rootElement || container, settings.fontSize);
            if (!packEnabled) {
                container.innerHTML = '';
                if (rootElement) rootElement.hidden = true;
                return { rendered: false, disabled: true, empty: true };
            }
            if (rootElement) rootElement.hidden = false;

            const tokens = session.buildRenderTokens();
            session.assignRenderTokenSequences(tokens);
            if (!tokens.length) {
                container.innerHTML = '';
                return { rendered: false, disabled: false, empty: true };
            }

            const model = renderModel.buildRenderModel({
                tokens,
                displayMode: settings.displayContent,
                suppressTranslationDisplay: !!uiState.suppressTranslationDisplay,
            });
            const allSentences = [];
            for (const block of model.speakerBlocks || []) {
                for (const sentence of block.sentences || []) {
                    allSentences.push(sentence);
                }
            }
            const visibleSentences = allSentences.slice(-maxSentences);
            const parts = [];
            for (const sentence of visibleSentences) {
                const originalBody = model.showOriginal ? buildOriginalBody(sentence) : '';
                const translationLine = model.showTranslation
                    ? buildTranslationLine(sentence, model.showOriginal, sentMb)
                    : '';
                if (originalBody && translationLine) {
                    parts.push(formatOverlayLine(sentence.originalLang, originalBody, 0));
                    parts.push(translationLine);
                } else if (originalBody) {
                    parts.push(formatOverlayLine(sentence.originalLang, originalBody, sentMb));
                } else if (translationLine) {
                    parts.push(translationLine);
                }
            }

            const html = parts.join('');
            container.innerHTML = html;
            if (typeof container.scrollHeight === 'number') {
                container.scrollTop = container.scrollHeight;
            }
            return {
                rendered: html.length > 0,
                disabled: false,
                empty: html.length === 0,
            };
        }

        return {
            render,
            setSettings,
            getSettings,
            setPackEnabled,
            isPackEnabled,
            setUiState,
            invalidateAll,
        };
    }

    const api = {
        FONT_SIZE_DEFAULT,
        FONT_SIZE_MIN,
        FONT_SIZE_MAX,
        DISPLAY_MODES,
        DEFAULT_SETTINGS,
        MAX_RENDERED_SENTENCES,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeSettings,
        computeStyleVars,
        applyStyleVars,
        create,
    };
    root.ObsRenderer = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

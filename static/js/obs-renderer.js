(function (root) {
    'use strict';

    const obsSettings = root.ObsSettings
        || (typeof require === 'function' ? require('./obs-settings') : null);
    if (!obsSettings) {
        throw new Error('ObsRenderer requires ObsSettings');
    }

    const {
        FONT_SIZE_DEFAULT,
        FONT_SIZE_MIN,
        FONT_SIZE_MAX,
        VALID_DISPLAY_CONTENTS: DISPLAY_MODES,
        DEFAULT_SETTINGS,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeSettings,
        computeStyleVars,
    } = obsSettings;
    const MAX_RENDERED_SENTENCES = 20;

    function applyStyleVars(element, input) {
        const computed = computeStyleVars(input);
        if (element && element.style && typeof element.style.setProperty === 'function') {
            for (const [prop, val] of Object.entries(computed.vars)) {
                element.style.setProperty(prop, val);
            }
        }
        if (element && typeof element.setAttribute === 'function') {
            element.setAttribute('data-bg-mode', computed.settings.bgMode);
            element.setAttribute('data-font-family', computed.settings.fontFamily);
            element.setAttribute('data-position', computed.settings.position);
            element.setAttribute('data-alignment', computed.settings.alignment);
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
        const setTimeoutFn = typeof options.setTimeout === 'function'
            ? options.setTimeout
            : root.setTimeout.bind(root);
        const clearTimeoutFn = typeof options.clearTimeout === 'function'
            ? options.clearTimeout
            : root.clearTimeout.bind(root);

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
        const explicitMaxSentences = Number.isFinite(Number(options.maxSentences)) && Number(options.maxSentences) > 0
            ? Math.floor(Number(options.maxSentences))
            : null;

        let settings = normalizeSettings(options.settings);
        let packEnabled = options.packEnabled !== undefined ? !!options.packEnabled : true;
        let uiState = {
            translateMode: false,
            translationUiMode: 'fast',
            suppressTranslationDisplay: false,
            currentTranslationTargetLang: '',
            ...(options.uiState && typeof options.uiState === 'object' ? options.uiState : {}),
        };
        let autoHideTimer = null;
        let isAutoHidden = false;
        let lastContentSignature = '';

        const targetEl = rootElement || container;
        applyStyleVars(targetEl, settings);

        function setAutoHidden(hidden) {
            isAutoHidden = !!hidden;
            if (targetEl && targetEl.classList && typeof targetEl.classList.toggle === 'function') {
                targetEl.classList.toggle('is-auto-hidden', isAutoHidden);
            }
        }

        function clearAutoHideTimer() {
            if (autoHideTimer !== null) {
                clearTimeoutFn(autoHideTimer);
                autoHideTimer = null;
            }
        }

        function scheduleAutoHideTimer() {
            clearAutoHideTimer();
            if (settings.autoHideSeconds <= 0 || !lastContentSignature) {
                setAutoHidden(false);
                return;
            }
            setAutoHidden(false);
            autoHideTimer = setTimeoutFn(() => {
                autoHideTimer = null;
                setAutoHidden(true);
            }, settings.autoHideSeconds * 1000);
        }

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
            if (!settings.showLangTag || !lang) return '';
            return `<span class="ov-tag">${escapeHtml(String(lang).toUpperCase())}</span>`;
        }

        function formatOverlayLine(kind, lang, bodyHtml, marginBottomPx) {
            if (!bodyHtml) return '';
            const safeLang = lang ? String(lang) : '';
            const dir = getLangDir(safeLang);
            const tagHtml = formatLangTag(safeLang);
            const langAttr = safeLang ? ` lang="${escapeHtml(safeLang)}"` : '';
            const kindClass = kind === 'translation' ? 'ov-line--translation' : 'ov-line--original';
            return `<div class="ov-line ${kindClass}"${langAttr} dir="${dir}" style="margin-bottom:${marginBottomPx}px"><span class="ov-bg">${tagHtml}${bodyHtml}</span></div>`;
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
                return formatOverlayLine('translation', sentence.translationLang, body, marginBottomPx);
            }

            if (uiState.translationUiMode === 'accurate' && (!showOriginal || sentence.originalTokens.length > 0)) {
                const override = sentenceId ? (session.getTranslationOverride(sentenceId) || '').trim() : '';
                if (override && override !== sourceText) {
                    const lang = session.getTranslationLanguage(sentenceId)
                        || uiState.currentTranslationTargetLang
                        || '';
                    return formatOverlayLine('translation', lang, escapeHtml(override), marginBottomPx);
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
                return formatOverlayLine('translation', lang, body, marginBottomPx);
            }

            return '';
        }

        function setSettings(next) {
            settings = normalizeSettings(next);
            applyStyleVars(targetEl, settings);
            scheduleAutoHideTimer();
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

        function dispose() {
            clearAutoHideTimer();
            setAutoHidden(false);
        }

        function render() {
            const { sentMb } = applyStyleVars(targetEl, settings);
            if (!packEnabled) {
                container.innerHTML = '';
                if (rootElement) rootElement.hidden = true;
                lastContentSignature = '';
                clearAutoHideTimer();
                setAutoHidden(false);
                return { rendered: false, disabled: true, empty: true };
            }
            if (rootElement) rootElement.hidden = false;

            const tokens = session.buildRenderTokens();
            session.assignRenderTokenSequences(tokens);
            if (!tokens.length) {
                container.innerHTML = '';
                lastContentSignature = '';
                clearAutoHideTimer();
                setAutoHidden(false);
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
            const limit = explicitMaxSentences !== null
                ? Math.min(settings.maxSentences, explicitMaxSentences)
                : settings.maxSentences;
            const visibleSentences = allSentences.slice(-limit);
            const parts = [];
            for (const sentence of visibleSentences) {
                const originalBody = model.showOriginal ? buildOriginalBody(sentence) : '';
                const translationLine = model.showTranslation
                    ? buildTranslationLine(sentence, model.showOriginal, sentMb)
                    : '';
                if (originalBody && translationLine) {
                    parts.push(formatOverlayLine('original', sentence.originalLang, originalBody, 0));
                    parts.push(translationLine);
                } else if (originalBody) {
                    parts.push(formatOverlayLine('original', sentence.originalLang, originalBody, sentMb));
                } else if (translationLine) {
                    parts.push(translationLine);
                }
            }

            const html = parts.join('');
            container.innerHTML = html;
            if (typeof container.scrollHeight === 'number') {
                container.scrollTop = container.scrollHeight;
            }
            const nextSignature = html ? (container.textContent || html) : '';
            if (!nextSignature) {
                lastContentSignature = '';
                clearAutoHideTimer();
                setAutoHidden(false);
            } else if (nextSignature !== lastContentSignature) {
                lastContentSignature = nextSignature;
                scheduleAutoHideTimer();
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
            dispose,
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

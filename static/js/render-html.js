(function (root) {
    'use strict';

    const RTL_LANGUAGES = new Set(['ar', 'he', 'fa', 'ur', 'yi', 'ps', 'sd', 'ckb', 'dv']);
    const EAST_ASIAN_TIGHT_SPACING_CHAR_RE = /[\u3000-\u303F\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF01-\uFF60\uFF66-\uFF9D\uFFE0-\uFFEE]/u;

    function isRtlLanguage(langCode) {
        if (!langCode) return false;
        return RTL_LANGUAGES.has(String(langCode).toLowerCase());
    }

    function getLangDir(langCode) {
        return isRtlLanguage(langCode) ? 'rtl' : 'ltr';
    }

    function getSpeakerClass(speaker) {
        if (speaker === null || speaker === undefined || speaker === 'undefined') {
            return 'speaker-undefined';
        }
        const parsed = Number.parseInt(String(speaker), 10);
        if (Number.isFinite(parsed) && parsed > 0) {
            return `speaker-${((parsed - 1) % 15) + 1}`;
        }
        return `speaker-${speaker}`;
    }

    function getFirstNonWhitespaceChar(text) {
        for (const char of String(text || '')) {
            if (!/\s/u.test(char)) return char;
        }
        return '';
    }

    function getLastNonWhitespaceChar(text) {
        const chars = Array.from(String(text || ''));
        for (let index = chars.length - 1; index >= 0; index -= 1) {
            if (!/\s/u.test(chars[index])) return chars[index];
        }
        return '';
    }

    function isEastAsianTightSpacingChar(char) {
        return !!char && EAST_ASIAN_TIGHT_SPACING_CHAR_RE.test(char);
    }

    function normalizeTranslationTokenTexts(tokens) {
        const texts = (Array.isArray(tokens) ? tokens : [])
            .map((token) => (token && token.text ? String(token.text) : ''));
        if (texts.length === 0) return texts;

        const nextVisibleChars = new Array(texts.length).fill('');
        let nextVisibleChar = '';
        for (let index = texts.length - 1; index >= 0; index -= 1) {
            nextVisibleChars[index] = nextVisibleChar;
            const firstChar = getFirstNonWhitespaceChar(texts[index]);
            if (firstChar) nextVisibleChar = firstChar;
        }

        let previousVisibleChar = '';
        for (let index = 0; index < texts.length; index += 1) {
            let text = texts[index];
            const firstChar = getFirstNonWhitespaceChar(text);
            const nextChar = firstChar || nextVisibleChars[index];
            if (
                previousVisibleChar
                && nextChar
                && isEastAsianTightSpacingChar(previousVisibleChar)
                && isEastAsianTightSpacingChar(nextChar)
            ) {
                if (firstChar) text = text.replace(/^\s+/u, '');
                else if (/^\s+$/u.test(text)) text = '';
                texts[index] = text;
            }
            const lastChar = getLastNonWhitespaceChar(texts[index]);
            if (lastChar) previousVisibleChar = lastChar;
        }
        return texts;
    }

    function createEscapeHtml(documentRef) {
        if (!documentRef || typeof documentRef.createElement !== 'function') {
            throw new Error('RenderHtml escape requires a document');
        }
        return function escapeHtml(text) {
            const element = documentRef.createElement('div');
            element.textContent = text;
            return element.innerHTML;
        };
    }

    function getDirectSpeakerLabel(block) {
        const first = block ? block.firstElementChild : null;
        return first && first.classList && first.classList.contains('speaker-label')
            ? first
            : null;
    }

    function syncSpeakerLabel(existingBlock, newBlock) {
        const newLabel = getDirectSpeakerLabel(newBlock);
        const existingLabel = getDirectSpeakerLabel(existingBlock);
        if (!newLabel && existingLabel) {
            existingLabel.remove();
            return;
        }
        if (newLabel && !existingLabel) {
            existingBlock.insertBefore(newLabel.cloneNode(true), existingBlock.firstChild);
            return;
        }
        if (newLabel && existingLabel) {
            if (existingLabel.className !== newLabel.className) {
                existingLabel.className = newLabel.className;
            }
            if (existingLabel.textContent !== newLabel.textContent) {
                existingLabel.textContent = newLabel.textContent;
            }
        }
    }

    function ensureBlockId(block, fallbackIndex) {
        let id = block.dataset.blockId;
        if (!id) {
            const firstSentence = block.querySelector('.sentence-block');
            id = firstSentence && firstSentence.dataset.sentenceId
                ? `block-${firstSentence.dataset.sentenceId}`
                : `block-fallback-${fallbackIndex}`;
            block.dataset.blockId = id;
        }
        return id;
    }

    function createDomPatcher({
        document: documentRef,
        container,
        renderedSentences = new Map(),
        renderedBlocks = new Map(),
        logger = typeof console !== 'undefined' ? console : null,
    } = {}) {
        if (!documentRef || typeof documentRef.createElement !== 'function') {
            throw new Error('RenderHtml DOM patcher requires a document');
        }
        if (!container || typeof container.querySelectorAll !== 'function') {
            throw new Error('RenderHtml DOM patcher requires a container');
        }

        function updateSentenceBlocksInPlace(
            existingBlock,
            newBlock,
            previousSentenceHtml,
            nextSentenceHtml,
        ) {
            const newSentences = Array.from(newBlock.querySelectorAll('.sentence-block'));
            const newSentenceIds = new Set();
            const existingById = new Map();
            Array.from(existingBlock.querySelectorAll('.sentence-block')).forEach((node) => {
                if (node && node.dataset && node.dataset.sentenceId) {
                    existingById.set(node.dataset.sentenceId, node);
                }
            });

            for (let index = 0; index < newSentences.length; index += 1) {
                const newSentence = newSentences[index];
                const sentenceId = newSentence.dataset.sentenceId;
                if (!sentenceId) continue;
                newSentenceIds.add(sentenceId);

                const existingSentence = existingById.get(sentenceId);
                const hasChanged = previousSentenceHtml.get(sentenceId)
                    !== nextSentenceHtml.get(sentenceId);
                if (existingSentence) {
                    if (hasChanged) {
                        if (existingSentence.className !== newSentence.className) {
                            existingSentence.className = newSentence.className;
                        }
                        if (existingSentence.innerHTML !== newSentence.innerHTML) {
                            existingSentence.innerHTML = newSentence.innerHTML;
                        }
                    }
                    continue;
                }

                const clone = newSentence.cloneNode(true);
                let inserted = false;
                for (let nextIndex = index + 1; nextIndex < newSentences.length; nextIndex += 1) {
                    const nextId = newSentences[nextIndex].dataset.sentenceId;
                    const nextExisting = nextId ? existingById.get(nextId) : null;
                    if (nextExisting && nextExisting.parentNode) {
                        nextExisting.parentNode.insertBefore(clone, nextExisting);
                        inserted = true;
                        break;
                    }
                }
                if (!inserted) existingBlock.appendChild(clone);
                existingById.set(sentenceId, clone);
            }

            existingById.forEach((node, id) => {
                if (!newSentenceIds.has(id)) node.remove();
            });
        }

        function patch(html) {
            const desiredHtml = String(html || '');
            const fragment = documentRef.createElement('div');
            fragment.innerHTML = desiredHtml;

            const newBlocks = Array.from(fragment.querySelectorAll('.subtitle-block'));
            const nextSentenceHtml = new Map();
            newBlocks.forEach((block) => {
                block.querySelectorAll('.sentence-block').forEach((sentence) => {
                    if (sentence.dataset.sentenceId) {
                        nextSentenceHtml.set(sentence.dataset.sentenceId, sentence.outerHTML);
                    }
                });
            });
            const previousSentenceHtml = new Map(renderedSentences);
            const previousBlockHtml = new Map(renderedBlocks);

            container.querySelectorAll('.empty-state').forEach((node) => node.remove());
            Array.from(container.children).forEach((child) => {
                if (child.classList && child.classList.contains('subtitle-block')) return;
                if (child.querySelector && child.querySelector('.sentence-block')) return;
                child.remove();
            });

            try {
                const existingBlocks = Array.from(container.querySelectorAll('.subtitle-block'));
                const existingById = new Map();
                existingBlocks.forEach((block, index) => {
                    existingById.set(ensureBlockId(block, index), block);
                });
                const keepIds = new Set();
                const nextBlockHtml = new Map();

                for (let index = 0; index < newBlocks.length; index += 1) {
                    const newBlock = newBlocks[index];
                    const id = ensureBlockId(newBlock, index);
                    const newInnerHtml = newBlock.innerHTML;
                    nextBlockHtml.set(id, newInnerHtml);
                    const existingBlock = existingById.get(id);

                    if (existingBlock) {
                        if (existingBlock.className !== newBlock.className) {
                            existingBlock.className = newBlock.className;
                        }
                        if (previousBlockHtml.get(id) !== newInnerHtml) {
                            syncSpeakerLabel(existingBlock, newBlock);
                            updateSentenceBlocksInPlace(
                                existingBlock,
                                newBlock,
                                previousSentenceHtml,
                                nextSentenceHtml,
                            );
                        }
                        keepIds.add(id);
                        continue;
                    }

                    const wrapper = newBlock.cloneNode(true);
                    wrapper.dataset.blockId = id;
                    let inserted = false;
                    for (let nextIndex = index + 1; nextIndex < newBlocks.length; nextIndex += 1) {
                        const nextId = ensureBlockId(newBlocks[nextIndex], nextIndex);
                        const nextExisting = existingById.get(nextId);
                        if (nextExisting && nextExisting.parentNode) {
                            nextExisting.parentNode.insertBefore(wrapper, nextExisting);
                            inserted = true;
                            break;
                        }
                    }
                    if (!inserted) container.appendChild(wrapper);
                    keepIds.add(id);
                }

                existingBlocks.forEach((block, index) => {
                    const id = ensureBlockId(block, index);
                    if (!keepIds.has(id)) block.remove();
                });

                renderedSentences.clear();
                nextSentenceHtml.forEach((value, key) => renderedSentences.set(key, value));
                renderedBlocks.clear();
                nextBlockHtml.forEach((value, key) => renderedBlocks.set(key, value));
            } catch (error) {
                if (logger && typeof logger.warn === 'function') {
                    logger.warn('Incremental render (block-level) failed, falling back to full render:', error);
                }
                container.innerHTML = desiredHtml;
                renderedSentences.clear();
                renderedBlocks.clear();
                container.querySelectorAll('.subtitle-block').forEach((block, index) => {
                    const id = ensureBlockId(block, index);
                    renderedBlocks.set(id, block.innerHTML);
                    block.querySelectorAll('.sentence-block').forEach((sentence) => {
                        if (sentence.dataset.sentenceId) {
                            renderedSentences.set(sentence.dataset.sentenceId, sentence.outerHTML);
                        }
                    });
                });
            }

            return { renderedSentences, renderedBlocks };
        }

        function clear({ dom = false } = {}) {
            renderedSentences.clear();
            renderedBlocks.clear();
            if (dom) container.innerHTML = '';
        }

        return { patch, clear, renderedSentences, renderedBlocks };
    }

    function createRenderHtml({ document: documentRef, escapeHtml, t } = {}) {
        const escape = typeof escapeHtml === 'function'
            ? escapeHtml
            : createEscapeHtml(documentRef);
        const translate = typeof t === 'function' ? t : (key) => key;

        function getLanguageTag(language) {
            if (!language) return '';
            return `<span class="language-tag">${String(language).toUpperCase()}</span>`;
        }

        function wrapSubtitleLineBody(innerHtml, dir, lang) {
            const langAttr = lang ? ` lang="${lang}"` : '';
            return `<span class="subtitle-line-body"${langAttr} dir="${dir || 'auto'}">${innerHtml}</span>`;
        }

        function renderTokenSpan(token, useRubyHtml = null) {
            const classes = ['subtitle-text'];
            if (!token.is_final) classes.push('non-final');
            const content = useRubyHtml || escape(token.text);
            return `<span class="${classes.join(' ')}">${content}</span>`;
        }

        function renderTokenSpanWithText(token, text, useRubyHtml = null) {
            const classes = ['subtitle-text'];
            if (token && token.is_final === false) classes.push('non-final');
            const content = useRubyHtml || escape(text);
            return `<span class="${classes.join(' ')}">${content}</span>`;
        }

        function renderTokenSpansTrimmed(tokens, useRubyHtml = null, options = {}) {
            if (!Array.isArray(tokens) || tokens.length === 0) return '';
            const texts = options.normalizeTranslationSpacing
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
            if (start === end) {
                const text = getText(start).trim();
                return text ? renderTokenSpanWithText(tokens[start], text, useRubyHtml) : '';
            }

            const parts = [];
            for (let index = start; index <= end; index += 1) {
                let text = getText(index);
                if (index === start) text = startText;
                if (index === end) text = endText;
                if (text) parts.push(renderTokenSpanWithText(tokens[index], text, useRubyHtml));
            }
            return parts.join('');
        }

        function renderSubtitleLine({
            bodyHtml = '',
            language = '',
            direction = getLangDir(language),
            className = 'subtitle-line',
            showLanguageTag = true,
            languageTagInsideBody = false,
        } = {}) {
            const tag = showLanguageTag ? getLanguageTag(language) : '';
            const body = languageTagInsideBody ? `${tag}${bodyHtml}` : bodyHtml;
            const beforeBody = languageTagInsideBody ? '' : tag;
            return `<div class="${className}" lang="${language || ''}" dir="${direction}">${beforeBody}${wrapSubtitleLineBody(body, direction, language)}</div>`;
        }

        function renderSentenceHtml(sentenceId, sentenceParts = []) {
            const content = Array.isArray(sentenceParts)
                ? sentenceParts.join('')
                : String(sentenceParts || '');
            if (!content) return '';
            return `<div class="sentence-block" data-sentence-id="${sentenceId}">${content}</div>`;
        }

        function renderSpeakerLabelHtml(speaker) {
            return `<div class="speaker-label ${getSpeakerClass(speaker)}">${escape(translate('speaker_label', { speaker }))}</div>`;
        }

        function renderSpeakerBlockHtml({
            speaker,
            sentenceHtml = [],
            previousSpeaker = null,
            direction = 'ltr',
            showSpeakerLabel = true,
        } = {}) {
            const sentences = Array.isArray(sentenceHtml)
                ? sentenceHtml.join('')
                : String(sentenceHtml || '');
            const label = showSpeakerLabel && speaker !== previousSpeaker
                ? renderSpeakerLabelHtml(speaker)
                : '';
            const content = `${label}${sentences}`;
            if (!content.trim()) return '';
            const className = speaker === previousSpeaker
                ? 'subtitle-block same-speaker'
                : 'subtitle-block';
            return `<div class="${className}" dir="${direction}">${content}</div>`;
        }

        return {
            escapeHtml: escape,
            isRtlLanguage,
            getLangDir,
            getLanguageTag,
            wrapSubtitleLineBody,
            getSpeakerClass,
            renderTokenSpan,
            renderTokenSpanWithText,
            renderTokenSpansTrimmed,
            normalizeTranslationTokenTexts,
            renderSubtitleLine,
            renderSentenceHtml,
            renderSpeakerLabelHtml,
            renderSpeakerBlockHtml,
            createDomPatcher: (options = {}) => createDomPatcher({
                document: documentRef,
                ...options,
            }),
        };
    }

    const api = {
        RTL_LANGUAGES,
        EAST_ASIAN_TIGHT_SPACING_CHAR_RE,
        isRtlLanguage,
        getLangDir,
        getSpeakerClass,
        getFirstNonWhitespaceChar,
        getLastNonWhitespaceChar,
        isEastAsianTightSpacingChar,
        normalizeTranslationTokenTexts,
        createEscapeHtml,
        createDomPatcher,
        createRenderHtml,
    };
    root.RenderHtml = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

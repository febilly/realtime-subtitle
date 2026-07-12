(function (root) {
    'use strict';

    const DEFAULT_DICT_PATH = '/kuromoji/dict/';

    function toHiragana(katakana) {
        const value = (katakana || '').toString();
        let output = '';
        for (let index = 0; index < value.length; index += 1) {
            const code = value.charCodeAt(index);
            if (code >= 0x30a1 && code <= 0x30f6) {
                output += String.fromCharCode(code - 0x60);
            } else {
                output += value[index];
            }
        }
        return output;
    }

    function hasKanji(text) {
        return /[\u4e00-\u9fff]/.test(text || '');
    }

    function hasKatakana(text) {
        return /[\u30a0-\u30ff]/.test(text || '');
    }

    function buildFuriganaHtml(tokens, escapeHtml) {
        if (!Array.isArray(tokens) || tokens.length === 0) return null;
        if (typeof escapeHtml !== 'function') {
            throw new TypeError('buildFuriganaHtml requires an escapeHtml function');
        }

        const htmlParts = [];
        tokens.forEach((token) => {
            const surface = (token.surface_form || token.surface || token.basic_form || '').toString();
            if (!surface) return;
            const readingRaw = (token.reading || token.pronunciation || '').toString();
            const reading = readingRaw ? toHiragana(readingRaw) : '';
            const needsRuby = (hasKanji(surface) || hasKatakana(surface))
                && reading
                && reading !== surface;
            if (needsRuby) {
                htmlParts.push(
                    `<ruby>${escapeHtml(surface)}<rp>(</rp><rt>${escapeHtml(reading)}</rt><rp>)</rp></ruby>`,
                );
            } else {
                htmlParts.push(escapeHtml(surface));
            }
        });
        return htmlParts.join('');
    }

    function defaultDelay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function createService({
        kuromoji = null,
        escapeHtml,
        onReady = () => {},
        dictPath = DEFAULT_DICT_PATH,
        delay = defaultDelay,
    } = {}) {
        if (typeof escapeHtml !== 'function') {
            throw new TypeError('createService requires an escapeHtml function');
        }
        if (typeof onReady !== 'function') {
            throw new TypeError('createService onReady must be a function');
        }
        if (typeof delay !== 'function') {
            throw new TypeError('createService delay must be a function');
        }

        const cache = new Map();
        const pending = new Set();
        let enabled = false;
        let tokenizerPromise = null;

        function getKuromojiTokenizer() {
            if (tokenizerPromise) return tokenizerPromise;

            if (!kuromoji || typeof kuromoji.builder !== 'function') {
                tokenizerPromise = Promise.resolve(null);
                return tokenizerPromise;
            }

            tokenizerPromise = new Promise((resolve) => {
                try {
                    kuromoji.builder({ dicPath: dictPath })
                        .build((error, tokenizer) => {
                            if (error) {
                                console.error('Failed to init kuromoji:', error);
                                resolve(null);
                                return;
                            }
                            resolve(tokenizer);
                        });
                } catch (error) {
                    console.error('Failed to init kuromoji:', error);
                    resolve(null);
                }
            });
            return tokenizerPromise;
        }

        async function getFuriganaHtml(text) {
            if (!text || !enabled) return null;
            if (cache.has(text)) return cache.get(text);

            const tokenizer = await getKuromojiTokenizer();
            if (!tokenizer) return null;

            try {
                const tokens = tokenizer.tokenize(text) || [];
                const html = buildFuriganaHtml(tokens, escapeHtml);
                if (html) {
                    cache.set(text, html);
                    return html;
                }
            } catch (error) {
                console.error('Failed to tokenize furigana:', error);
            }
            return null;
        }

        function requestFurigana(text) {
            if (!text || !enabled) return null;
            if (cache.has(text) || pending.has(text)) return null;

            pending.add(text);
            return getFuriganaHtml(text)
                .then((html) => {
                    // Deliberately do not re-check `enabled`: app.js currently
                    // allows an already-started request to populate the cache
                    // and schedule a render after the feature is switched off.
                    if (html) {
                        cache.set(text, html);
                        onReady();
                    }
                    return html;
                })
                .finally(() => {
                    pending.delete(text);
                });
        }

        function clearCache() {
            cache.clear();
        }

        function clearPending() {
            pending.clear();
        }

        function clear() {
            clearCache();
            clearPending();
        }

        function setEnabled(value, { clearState = true } = {}) {
            enabled = !!value;
            if (clearState) clear();
            return enabled;
        }

        function isEnabled() {
            return enabled;
        }

        function getCached(text) {
            return cache.get(text);
        }

        function hasPending(text) {
            return pending.has(text);
        }

        async function waitForIdle() {
            while (pending.size > 0) await delay(0);
        }

        return {
            setEnabled,
            isEnabled,
            clear,
            clearCache,
            clearPending,
            getCached,
            hasPending,
            getKuromojiTokenizer,
            getTokenizer: getKuromojiTokenizer,
            getFuriganaHtml,
            get: getFuriganaHtml,
            requestFurigana,
            request: requestFurigana,
            waitForIdle,
            cache,
            pending,
            get tokenizerPromise() {
                return tokenizerPromise;
            },
        };
    }

    const api = {
        DEFAULT_DICT_PATH,
        toHiragana,
        hasKanji,
        hasKatakana,
        buildFuriganaHtml,
        createService,
    };
    root.Furigana = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

(function (root) {
    'use strict';

    const DEFAULT_DICT_PATH = '/kuromoji/dict/';
    const SUPPORTED_LEARNING_LANGUAGES = Object.freeze(['ja', 'zh', 'ko']);
    const HANGUL_SEGMENT_PATTERN = /([\uAC00-\uD7A3]+)/g;

    function normalizeLanguage(lang) {
        const primary = String(lang || '')
            .trim()
            .toLowerCase()
            .replace(/_/g, '-')
            .split('-')[0];
        if (primary === 'ja') return 'ja';
        if (primary === 'zh' || primary === 'cmn') return 'zh';
        if (primary === 'ko') return 'ko';
        return null;
    }

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

    // 把分词结果归一成 [[surface, reading|null], ...]。reading 仅在需要注音时非空
    // （含汉字或片假名、且读音转平假名后与表面形不同）。这是注音规则的唯一来源，
    // buildFuriganaHtml 与原生悬浮窗（经 window.__overlayFurigana）都复用它。
    function buildFuriganaPairs(tokens) {
        if (!Array.isArray(tokens)) return [];
        const pairs = [];
        tokens.forEach((token) => {
            const surface = (token.surface_form || token.surface || token.basic_form || '').toString();
            if (!surface) return;
            const readingRaw = (token.reading || token.pronunciation || '').toString();
            const reading = readingRaw ? toHiragana(readingRaw) : '';
            const needsRuby = (hasKanji(surface) || hasKatakana(surface))
                && reading
                && reading !== surface;
            pairs.push([surface, needsRuby ? reading : null]);
        });
        return pairs;
    }

    function buildRubyHtml(pairs, escapeHtml) {
        if (!Array.isArray(pairs)) return null;
        if (typeof escapeHtml !== 'function') {
            throw new TypeError('buildRubyHtml requires an escapeHtml function');
        }
        return pairs.map(([surface, reading]) => {
            const safeSurface = escapeHtml(surface);
            if (reading) {
                return `<ruby>${safeSurface}<rp>(</rp><rt>${escapeHtml(reading)}</rt><rp>)</rp></ruby>`;
            }
            return safeSurface;
        }).join('');
    }

    function buildFuriganaHtml(tokens, escapeHtml) {
        if (!Array.isArray(tokens) || tokens.length === 0) return null;
        if (typeof escapeHtml !== 'function') {
            throw new TypeError('buildFuriganaHtml requires an escapeHtml function');
        }
        return buildRubyHtml(buildFuriganaPairs(tokens), escapeHtml);
    }

    function buildPinyinPairs(text, pinyinProImpl = root.pinyinPro) {
        const value = String(text == null ? '' : text);
        if (!value || !pinyinProImpl || typeof pinyinProImpl.pinyin !== 'function') {
            return [];
        }
        const items = pinyinProImpl.pinyin(value, { type: 'all' });
        if (!Array.isArray(items)) return [];
        const pairs = [];
        let plainBuffer = '';
        let plainIsWhitespace = false;

        function flushPlain() {
            if (!plainBuffer) return;
            pairs.push([plainBuffer, null]);
            plainBuffer = '';
        }

        for (let index = 0; index < items.length; index += 1) {
            const item = items[index];
            if (!item) continue;
            const origin = String(item.origin == null ? '' : item.origin);
            if (!origin) continue;
            const reading = item.isZh && item.pinyin ? String(item.pinyin) : '';
            if (reading && reading !== origin) {
                flushPlain();
                pairs.push([origin, reading]);
                continue;
            }
            const isWs = /\s/.test(origin);
            if (plainBuffer && plainIsWhitespace !== isWs) {
                flushPlain();
            }
            plainIsWhitespace = isWs;
            plainBuffer += origin;
        }
        flushPlain();
        return pairs;
    }

    function buildKoreanRomanizationPairs(text, koromanImpl = root.koroman) {
        const value = String(text == null ? '' : text);
        if (!value || !koromanImpl || typeof koromanImpl.romanize !== 'function') {
            return [];
        }
        const parts = value.split(HANGUL_SEGMENT_PATTERN);
        const pairs = [];
        for (let index = 0; index < parts.length; index += 1) {
            const part = parts[index];
            if (!part) continue;
            if (/^[\uAC00-\uD7A3]+$/.test(part)) {
                const romanized = String(koromanImpl.romanize(part) || '');
                const needsRuby = !!(romanized && romanized !== part);
                pairs.push([part, needsRuby ? romanized : null]);
            } else {
                const subparts = part.split(/(\s+)/);
                for (let subIndex = 0; subIndex < subparts.length; subIndex += 1) {
                    if (subparts[subIndex]) {
                        pairs.push([subparts[subIndex], null]);
                    }
                }
            }
        }
        return pairs;
    }

    function defaultDelay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function toCacheKey(text, lang = 'ja') {
        const norm = normalizeLanguage(lang) || 'ja';
        return norm === 'ja' ? text : `${norm}:${text}`;
    }

    function createService({
        kuromoji = null,
        pinyinPro = root.pinyinPro || null,
        koroman = root.koroman || null,
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
        let readyTokenizer = null;

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
                            readyTokenizer = tokenizer;
                            resolve(tokenizer);
                        });
                } catch (error) {
                    console.error('Failed to init kuromoji:', error);
                    resolve(null);
                }
            });
            return tokenizerPromise;
        }

        function computeSyncHtml(text, normLang) {
            try {
                const pairs = normLang === 'zh'
                    ? buildPinyinPairs(text, pinyinPro || root.pinyinPro)
                    : buildKoreanRomanizationPairs(text, koroman || root.koroman);
                if (!Array.isArray(pairs) || pairs.length === 0) return null;
                return buildRubyHtml(pairs, escapeHtml);
            } catch (error) {
                console.error(`Failed to annotate ${normLang} reading:`, error);
                return null;
            }
        }

        function getSync(text, lang = 'ja') {
            if (!text || !enabled) return null;
            const normLang = normalizeLanguage(lang);
            if (!normLang || normLang === 'ja') return undefined;
            const key = toCacheKey(text, normLang);
            if (cache.has(key)) return cache.get(key);
            const html = computeSyncHtml(text, normLang);
            if (typeof html === 'string' && html.length > 0) {
                cache.set(key, html);
                return html;
            }
            return null;
        }

        async function getFuriganaHtml(text, lang = 'ja') {
            if (!text || !enabled) return null;
            const normLang = normalizeLanguage(lang);
            if (!normLang) return null;
            const key = toCacheKey(text, normLang);
            if (cache.has(key)) return cache.get(key);

            if (normLang === 'zh' || normLang === 'ko') {
                return getSync(text, normLang);
            }

            const tokenizer = await getKuromojiTokenizer();
            if (!tokenizer) return null;

            try {
                const tokens = tokenizer.tokenize(text) || [];
                const html = buildFuriganaHtml(tokens, escapeHtml);
                if (html) {
                    cache.set(key, html);
                    return html;
                }
            } catch (error) {
                console.error('Failed to tokenize furigana:', error);
            }
            return null;
        }

        function requestFurigana(text, lang = 'ja') {
            if (!text || !enabled) return null;
            const normLang = normalizeLanguage(lang);
            if (!normLang) return null;
            const key = toCacheKey(text, normLang);
            if (cache.has(key) || pending.has(key)) return null;

            pending.add(key);
            return getFuriganaHtml(text, normLang)
                .then((html) => {
                    // Deliberately do not re-check `enabled`: app.js currently
                    // allows an already-started request to populate the cache
                    // and schedule a render after the feature is switched off.
                    if (html) {
                        cache.set(key, html);
                        onReady();
                    }
                    return html;
                })
                .finally(() => {
                    pending.delete(key);
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

        function getCached(text, lang = 'ja') {
            return cache.get(toCacheKey(text, lang));
        }

        function hasPending(text, lang = 'ja') {
            return pending.has(toCacheKey(text, lang));
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
            getSync,
            hasPending,
            getKuromojiTokenizer,
            getTokenizer: getKuromojiTokenizer,
            getReadyTokenizer: () => readyTokenizer,
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
        SUPPORTED_LEARNING_LANGUAGES,
        normalizeLanguage,
        toHiragana,
        hasKanji,
        hasKatakana,
        buildFuriganaPairs,
        buildRubyHtml,
        buildFuriganaHtml,
        buildPinyinPairs,
        buildKoreanRomanizationPairs,
        createService,
    };
    root.Furigana = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

const Furigana = require('../../static/js/furigana');

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function tokenizerFor(tokens) {
    return { tokenize: vi.fn(() => tokens) };
}

describe('Furigana pure helpers', () => {
    it('exports through CommonJS and the browser-compatible namespace', () => {
        expect(globalThis.Furigana).toBe(Furigana);
    });

    it('converts the standard katakana range to hiragana only', () => {
        expect(Furigana.toHiragana('アイウエオ・ヴヷーABC')).toBe('あいうえお・ゔヷーABC');
        expect(Furigana.toHiragana(null)).toBe('');
    });

    it('detects the same narrow kanji and katakana ranges as app.js', () => {
        expect(Furigana.hasKanji('食べる')).toBe(true);
        expect(Furigana.hasKanji('かなカナ')).toBe(false);
        expect(Furigana.hasKatakana('カナ')).toBe(true);
        expect(Furigana.hasKatakana('ー')).toBe(true);
        expect(Furigana.hasKatakana('かな')).toBe(false);
    });

    it('returns null for a missing token list and an empty string for skipped tokens', () => {
        expect(Furigana.buildFuriganaHtml(null, escapeHtml)).toBe(null);
        expect(Furigana.buildFuriganaHtml([], escapeHtml)).toBe(null);
        expect(Furigana.buildFuriganaHtml([{ surface_form: '' }], escapeHtml)).toBe('');
    });

    it('builds escaped ruby markup and converts readings to hiragana', () => {
        const html = Furigana.buildFuriganaHtml([
            { surface_form: '猫<&', reading: 'ネコ<&' },
        ], escapeHtml);

        expect(html).toBe(
            '<ruby>猫&lt;&amp;<rp>(</rp><rt>ねこ&lt;&amp;</rt><rp>)</rp></ruby>',
        );
    });

    it('uses surface and pronunciation fallbacks and leaves non-ruby text plain', () => {
        const html = Furigana.buildFuriganaHtml([
            { surface_form: '', surface: '語', basic_form: 'ignored', reading: '', pronunciation: 'ゴ' },
            { surface_form: 'かな<&', reading: 'カナ' },
            { surface_form: 'plain', reading: 'プレーン' },
        ], escapeHtml);

        expect(html).toBe(
            '<ruby>語<rp>(</rp><rt>ご</rt><rp>)</rp></ruby>かな&lt;&amp;plain',
        );
    });

    it('requires an explicit HTML escaper', () => {
        expect(() => Furigana.buildFuriganaHtml([{ surface_form: '猫' }])).toThrow(TypeError);
        expect(() => Furigana.createService()).toThrow(TypeError);
    });

    it('builds [surface, reading|null] pairs shared with the native overlay', () => {
        // buildFuriganaPairs 是注音规则的唯一来源：原生悬浮窗经 window.__overlayFurigana
        // 拿到的正是这里的输出，故须与 buildFuriganaHtml 的判定完全一致。
        const pairs = Furigana.buildFuriganaPairs([
            { surface_form: '', surface: '語', basic_form: 'ignored', reading: '', pronunciation: 'ゴ' },
            { surface_form: 'かな', reading: 'カナ' },
            { surface_form: 'カナ', reading: 'カナ' },
            { surface_form: 'plain', reading: 'プレーン' },
            { surface_form: '' },
        ]);

        expect(pairs).toEqual([
            ['語', 'ご'],       // 汉字 + pronunciation 兜底
            ['かな', null],     // 纯平假名不注音
            ['カナ', 'かな'],   // 片假名注音为平假名
            ['plain', null],    // 无汉字/片假名不注音
        ]);                     // 空 surface 被跳过
    });

    it('returns an empty array for a non-array token list', () => {
        expect(Furigana.buildFuriganaPairs(null)).toEqual([]);
        expect(Furigana.buildFuriganaPairs(undefined)).toEqual([]);
    });
});

describe('Furigana tokenizer service', () => {
    it('builds the tokenizer once with the absolute dictionary path', async () => {
        const tokenizer = tokenizerFor([]);
        const build = vi.fn((callback) => callback(null, tokenizer));
        const builder = vi.fn(() => ({ build }));
        const service = Furigana.createService({
            kuromoji: { builder },
            escapeHtml,
        });

        const first = service.getKuromojiTokenizer();
        const second = service.getKuromojiTokenizer();

        expect(first).toBe(second);
        await expect(first).resolves.toBe(tokenizer);
        expect(builder).toHaveBeenCalledTimes(1);
        expect(builder).toHaveBeenCalledWith({ dicPath: '/kuromoji/dict/' });
        expect(build).toHaveBeenCalledTimes(1);
    });

    it('exposes the resolved tokenizer synchronously for the native overlay bridge', async () => {
        const tokenizer = tokenizerFor([]);
        let finishBuild;
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => { finishBuild = callback; } }),
            },
            escapeHtml,
        });

        // window.__overlayFurigana 用它同步取已就绪的 tokenizer：构建完成前应为 null
        service.getTokenizer();
        expect(service.getReadyTokenizer()).toBe(null);

        finishBuild(null, tokenizer);
        await service.getTokenizer();
        expect(service.getReadyTokenizer()).toBe(tokenizer);
    });

    it('honors an explicit dictionary path', async () => {
        const builder = vi.fn(() => ({
            build: (callback) => callback(null, tokenizerFor([])),
        }));
        const service = Furigana.createService({
            kuromoji: { builder },
            escapeHtml,
            dictPath: '/custom/dict/',
        });

        await service.getTokenizer();
        expect(builder).toHaveBeenCalledWith({ dicPath: '/custom/dict/' });
    });

    it('caches a null tokenizer when kuromoji is unavailable', async () => {
        const service = Furigana.createService({ escapeHtml });
        const first = service.getTokenizer();
        const second = service.getTokenizer();

        expect(first).toBe(second);
        await expect(first).resolves.toBe(null);
        expect(service.tokenizerPromise).toBe(first);
    });

    it('caches a null tokenizer after an async build error', async () => {
        const error = new Error('bad dictionary');
        const builder = vi.fn(() => ({
            build: (callback) => callback(error),
        }));
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const service = Furigana.createService({
            kuromoji: { builder },
            escapeHtml,
        });

        await expect(service.getTokenizer()).resolves.toBe(null);
        await expect(service.getTokenizer()).resolves.toBe(null);
        expect(builder).toHaveBeenCalledTimes(1);
        expect(consoleError).toHaveBeenCalledWith('Failed to init kuromoji:', error);
        consoleError.mockRestore();
    });

    it('caches a null tokenizer after builder throws synchronously', async () => {
        const error = new Error('builder exploded');
        const builder = vi.fn(() => {
            throw error;
        });
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const service = Furigana.createService({
            kuromoji: { builder },
            escapeHtml,
        });

        await expect(service.getTokenizer()).resolves.toBe(null);
        await expect(service.getTokenizer()).resolves.toBe(null);
        expect(builder).toHaveBeenCalledTimes(1);
        expect(consoleError).toHaveBeenCalledWith('Failed to init kuromoji:', error);
        consoleError.mockRestore();
    });

    it('does no work while disabled and caches successful enabled lookups', async () => {
        const tokenizer = tokenizerFor([{ surface_form: '猫', reading: 'ネコ' }]);
        const builder = vi.fn(() => ({
            build: (callback) => callback(null, tokenizer),
        }));
        const service = Furigana.createService({
            kuromoji: { builder },
            escapeHtml,
        });

        await expect(service.get('猫')).resolves.toBe(null);
        expect(builder).not.toHaveBeenCalled();

        service.setEnabled(true);
        const expected = '<ruby>猫<rp>(</rp><rt>ねこ</rt><rp>)</rp></ruby>';
        await expect(service.get('猫')).resolves.toBe(expected);
        await expect(service.get('猫')).resolves.toBe(expected);
        expect(tokenizer.tokenize).toHaveBeenCalledTimes(1);
        expect(service.getCached('猫')).toBe(expected);
    });

    it('returns null and does not cache empty generated HTML', async () => {
        const tokenizer = tokenizerFor([]);
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => callback(null, tokenizer) }),
            },
            escapeHtml,
        });
        service.setEnabled(true);

        await expect(service.get('空')).resolves.toBe(null);
        expect(service.cache.has('空')).toBe(false);
    });

    it('reports tokenization failures without poisoning the tokenizer cache', async () => {
        const error = new Error('tokenize failed');
        const tokenizer = { tokenize: vi.fn(() => { throw error; }) };
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => callback(null, tokenizer) }),
            },
            escapeHtml,
        });
        service.setEnabled(true);

        await expect(service.get('猫')).resolves.toBe(null);
        await expect(service.get('猫')).resolves.toBe(null);
        expect(tokenizer.tokenize).toHaveBeenCalledTimes(2);
        expect(consoleError).toHaveBeenCalledWith('Failed to tokenize furigana:', error);
        consoleError.mockRestore();
    });
});

describe('Furigana request cache and pending state', () => {
    it('deduplicates an in-flight request and calls onReady once', async () => {
        let finishBuild;
        const tokenizer = tokenizerFor([{ surface_form: '猫', reading: 'ネコ' }]);
        const onReady = vi.fn();
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => { finishBuild = callback; } }),
            },
            escapeHtml,
            onReady,
        });
        service.setEnabled(true);

        const request = service.request('猫');
        expect(service.hasPending('猫')).toBe(true);
        expect(service.request('猫')).toBe(null);

        finishBuild(null, tokenizer);
        const html = await request;
        expect(html).toContain('<ruby>猫');
        expect(tokenizer.tokenize).toHaveBeenCalledTimes(1);
        expect(onReady).toHaveBeenCalledTimes(1);
        expect(service.hasPending('猫')).toBe(false);
        expect(service.request('猫')).toBe(null);
    });

    it('preserves the current in-flight callback after the feature is disabled', async () => {
        let finishBuild;
        const tokenizer = tokenizerFor([{ surface_form: '日本', reading: 'ニホン' }]);
        const onReady = vi.fn();
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => { finishBuild = callback; } }),
            },
            escapeHtml,
            onReady,
        });
        service.setEnabled(true);

        const request = service.request('日本');
        service.setEnabled(false);
        expect(service.hasPending('日本')).toBe(false);
        expect(service.getCached('日本')).toBeUndefined();

        finishBuild(null, tokenizer);
        const html = await request;
        expect(html).toContain('<ruby>日本');
        expect(onReady).toHaveBeenCalledTimes(1);
        expect(service.getCached('日本')).toBe(html);
        await expect(service.get('日本')).resolves.toBe(null);
    });

    it('clears cache and pending state on an enabled-state change without cancelling work', () => {
        const service = Furigana.createService({ escapeHtml });
        service.cache.set('cached', 'html');
        service.pending.add('pending');

        service.setEnabled(true);

        expect(service.isEnabled()).toBe(true);
        expect(service.cache.size).toBe(0);
        expect(service.pending.size).toBe(0);
    });

    it('uses the injected delay while waiting for pending requests to settle', async () => {
        let finishBuild;
        const tokenizer = tokenizerFor([{ surface_form: '猫', reading: 'ネコ' }]);
        const delay = vi.fn(async () => {
            if (finishBuild) {
                const callback = finishBuild;
                finishBuild = null;
                callback(null, tokenizer);
            }
            await Promise.resolve();
        });
        const service = Furigana.createService({
            kuromoji: {
                builder: () => ({ build: (callback) => { finishBuild = callback; } }),
            },
            escapeHtml,
            delay,
        });
        service.setEnabled(true);
        service.request('猫');

        await service.waitForIdle();

        expect(delay).toHaveBeenCalled();
        expect(service.hasPending('猫')).toBe(false);
    });
});

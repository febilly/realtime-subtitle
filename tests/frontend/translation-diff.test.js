const TranslationDiff = require('../../static/js/translation-diff');

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function create() {
    return TranslationDiff.create({ escapeHtml });
}

function plainText(html) {
    return html
        .replace(/<span class="llm-diff-ins">/g, '')
        .replace(/<span class="llm-diff-del">/g, '')
        .replace(/<\/span>/g, '');
}

describe('TranslationDiff mode normalization', () => {
    it('keeps the four known modes', () => {
        for (const mode of TranslationDiff.DIFF_MODES) {
            expect(TranslationDiff.normalizeMode(mode)).toBe(mode);
        }
    });

    it('falls back to "off" for unknown or missing modes', () => {
        expect(TranslationDiff.normalizeMode('nonsense')).toBe('off');
        expect(TranslationDiff.normalizeMode('')).toBe('off');
        expect(TranslationDiff.normalizeMode(null)).toBe('off');
        expect(TranslationDiff.normalizeMode(undefined)).toBe('off');
    });

    it('trims and lowercases before matching', () => {
        expect(TranslationDiff.normalizeMode('  TWO_LINES ')).toBe('two_lines');
    });
});

describe('TranslationDiff render "off"', () => {
    it('returns the escaped refined text without any diff markup', () => {
        const diff = create();
        const html = diff.render('hello world', 'hello brave world', 'off');
        expect(html).toBe('hello brave world');
        expect(html).not.toContain('llm-diff');
    });
});

describe('TranslationDiff render "additions"', () => {
    it('highlights added words in green and hides deletions (English, word-level)', () => {
        const diff = create();
        const html = diff.render('hello world', 'hello brave world', 'additions');
        expect(html).toContain('<span class="llm-diff-ins">brave</span>');
        expect(html).not.toContain('llm-diff-del');
        expect(plainText(html)).toBe('hello brave world');
    });

    it('highlights added characters for CJK text (char-level diff)', () => {
        const diff = create();
        const html = diff.render('你好', '你好美', 'additions');
        expect(html).toContain('<span class="llm-diff-ins">美</span>');
        expect(plainText(html)).toBe('你好美');
    });

    it('keeps the refined text readable when the diff is too expensive', () => {
        const diff = create();
        const big = 'a'.repeat(13000);
        const html = diff.render(big, `${big}b`, 'additions');
        expect(html).toBe(escapeHtml(`${big}b`));
        expect(html).not.toContain('llm-diff');
    });
});

describe('TranslationDiff render "additions_deletions"', () => {
    it('shows both inserted (green) and deleted (red strikethrough) text', () => {
        const diff = create();
        const html = diff.render('hello brave world', 'hello world', 'additions_deletions');
        expect(html).toContain('<span class="llm-diff-del">brave</span>');
        expect(html).toContain('hello');
        expect(html).toContain('world');
        const added = diff.render('hello world', 'hello brave world', 'additions_deletions');
        expect(added).toContain('<span class="llm-diff-ins">brave</span>');
    });

    it('separates consecutive deleted words with spaces at word level', () => {
        const diff = create();
        const html = diff.render('one two three four', 'one four', 'additions_deletions');
        expect(html).toContain('<span class="llm-diff-del">two three</span>');
        expect(html).toContain('one');
        expect(html).toContain('four');
    });
});

describe('TranslationDiff render "two_lines"', () => {
    it('renders the original on a red line and the refined one on a green line', () => {
        const diff = create();
        const html = diff.render('旧译文', '新译文', 'two_lines');
        expect(html).toContain('<span class="llm-diff-line-old">旧译文</span>');
        expect(html).toContain('<span class="llm-diff-line-new">新译文</span>');
        expect(html).not.toContain('llm-diff-ins');
        expect(html).not.toContain('llm-diff-del');
    });

    it('escapes HTML inside both lines', () => {
        const diff = create();
        const html = diff.render('<b>old</b>', '<i>new</i>', 'two_lines');
        expect(html).toContain('&lt;b&gt;old&lt;/b&gt;');
        expect(html).toContain('&lt;i&gt;new&lt;/i&gt;');
        expect(html).not.toContain('<b>');
    });
});

const LanguageCatalog = require('../../static/js/language-catalog');

describe('LanguageCatalog', () => {
    it('builds localized labels while preserving backend code spelling', () => {
        expect(LanguageCatalog.buildLanguageList(['EN', 'zh-hans', 'custom'])).toEqual([
            { code: 'EN', en: 'English', native: 'English' },
            { code: 'zh-hans', en: 'Chinese (Simplified)', native: '简体中文' },
            { code: 'custom', en: 'custom', native: 'custom' },
        ]);
    });

    it('coerces case-insensitively and falls back through the active provider list', () => {
        const catalog = LanguageCatalog.create(['ja', 'zh-hant']);
        expect(catalog.coerce('JA', 'zh-hant')).toBe('ja');
        expect(catalog.coerce('missing', 'ZH-HANT')).toBe('zh-hant');
        expect(catalog.coerce('missing', 'also-missing')).toBe('ja');
        expect(catalog.displayName('ZH-HANT')).toBe('Chinese (Traditional) - 繁體中文');
    });

    it('keeps the previous provider list when an empty update arrives', () => {
        const catalog = LanguageCatalog.create(['en', 'ja']);
        expect(catalog.setCodes([])).toBe(false);
        expect(catalog.setCodes(null)).toBe(false);
        expect(catalog.getLanguages().map(({ code }) => code)).toEqual(['en', 'ja']);
    });

    it('replaces the provider list in backend order', () => {
        const catalog = LanguageCatalog.create(['en']);
        expect(catalog.setCodes(['ko', 'fr'])).toBe(true);
        expect(catalog.getLanguages()).toEqual([
            { code: 'ko', en: 'Korean', native: '한국어' },
            { code: 'fr', en: 'French', native: 'Français' },
        ]);
    });
});

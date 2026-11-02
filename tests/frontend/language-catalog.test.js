const LanguageCatalog = require('../../static/js/language-catalog');

describe('LanguageCatalog', () => {
    it('builds localized labels while preserving backend code spelling', () => {
        expect(LanguageCatalog.buildLanguageList(['EN', 'zh-hans', 'custom'])).toEqual([
            { code: 'EN', en: 'English', native: 'English' },
            { code: 'zh-hans', en: 'Chinese (Simplified)', native: '简体中文' },
            { code: 'custom', en: 'custom', native: 'custom' },
        ]);
    });

    it('labels every Hy-MT2 base target including minority languages and script variants', () => {
        const codes = 'en zh ja ko ar bn my yue zh-hant cs nl fil fr de gu he hi id it kk km ms mr mn fa pl pt ru es ta te th bo tr uk ur ug vi'.split(' ');
        const languages = LanguageCatalog.buildLanguageList(codes);
        expect(languages).toHaveLength(38);
        for (const language of languages) {
            expect(language.en).not.toBe(language.code);
            expect(language.native).not.toBe(language.code);
        }
        expect(LanguageCatalog.buildLanguageList(['yue', 'bo', 'ug'])).toEqual([
            { code: 'yue', en: 'Cantonese', native: '粵語' },
            { code: 'bo', en: 'Tibetan', native: 'བོད་ཡིག' },
            { code: 'ug', en: 'Uyghur', native: 'ئۇيغۇرچە' },
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

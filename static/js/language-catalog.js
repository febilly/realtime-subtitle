(function (root) {
    'use strict';

    const LANGUAGE_NAME_MAP = {
        af: { en: 'Afrikaans', native: 'Afrikaans' },
        ak: { en: 'Akan', native: 'Akan' },
        sq: { en: 'Albanian', native: 'Shqip' },
        am: { en: 'Amharic', native: 'አማርኛ' },
        ar: { en: 'Arabic', native: 'العربية' },
        hy: { en: 'Armenian', native: 'Հայերեն' },
        az: { en: 'Azerbaijani', native: 'Azərbaycan dili' },
        eu: { en: 'Basque', native: 'Euskara' },
        be: { en: 'Belarusian', native: 'Беларуская' },
        bn: { en: 'Bengali', native: 'বাংলা' },
        bs: { en: 'Bosnian', native: 'Bosanski' },
        bg: { en: 'Bulgarian', native: 'Български' },
        my: { en: 'Burmese', native: 'မြန်မာ' },
        ca: { en: 'Catalan', native: 'Català' },
        zh: { en: 'Chinese', native: '中文' },
        'zh-hans': { en: 'Chinese (Simplified)', native: '简体中文' },
        'zh-hant': { en: 'Chinese (Traditional)', native: '繁體中文' },
        hr: { en: 'Croatian', native: 'Hrvatski' },
        cs: { en: 'Czech', native: 'Čeština' },
        da: { en: 'Danish', native: 'Dansk' },
        nl: { en: 'Dutch', native: 'Nederlands' },
        en: { en: 'English', native: 'English' },
        et: { en: 'Estonian', native: 'Eesti' },
        fil: { en: 'Filipino', native: 'Filipino' },
        fi: { en: 'Finnish', native: 'Suomi' },
        fr: { en: 'French', native: 'Français' },
        gl: { en: 'Galician', native: 'Galego' },
        ka: { en: 'Georgian', native: 'ქართული' },
        de: { en: 'German', native: 'Deutsch' },
        el: { en: 'Greek', native: 'Ελληνικά' },
        gu: { en: 'Gujarati', native: 'ગુજરાતી' },
        ha: { en: 'Hausa', native: 'Hausa' },
        he: { en: 'Hebrew', native: 'עברית' },
        hi: { en: 'Hindi', native: 'हिन्दी' },
        hu: { en: 'Hungarian', native: 'Magyar' },
        is: { en: 'Icelandic', native: 'Íslenska' },
        id: { en: 'Indonesian', native: 'Bahasa Indonesia' },
        it: { en: 'Italian', native: 'Italiano' },
        ja: { en: 'Japanese', native: '日本語' },
        jv: { en: 'Javanese', native: 'Basa Jawa' },
        kn: { en: 'Kannada', native: 'ಕನ್ನಡ' },
        kk: { en: 'Kazakh', native: 'Қазақша' },
        km: { en: 'Khmer', native: 'ខ្មែរ' },
        rw: { en: 'Kinyarwanda', native: 'Ikinyarwanda' },
        ko: { en: 'Korean', native: '한국어' },
        lo: { en: 'Lao', native: 'ລາວ' },
        lv: { en: 'Latvian', native: 'Latviešu' },
        lt: { en: 'Lithuanian', native: 'Lietuvių' },
        mk: { en: 'Macedonian', native: 'Македонски' },
        ms: { en: 'Malay', native: 'Bahasa Melayu' },
        ml: { en: 'Malayalam', native: 'മലയാളം' },
        mr: { en: 'Marathi', native: 'मराठी' },
        mn: { en: 'Mongolian', native: 'Монгол' },
        ne: { en: 'Nepali', native: 'नेपाली' },
        no: { en: 'Norwegian', native: 'Norsk' },
        fa: { en: 'Persian', native: 'فارسی' },
        pl: { en: 'Polish', native: 'Polski' },
        pt: { en: 'Portuguese', native: 'Português' },
        pa: { en: 'Punjabi', native: 'ਪੰਜਾਬੀ' },
        ro: { en: 'Romanian', native: 'Română' },
        ru: { en: 'Russian', native: 'Русский' },
        sr: { en: 'Serbian', native: 'Српски' },
        sd: { en: 'Sindhi', native: 'سنڌي' },
        si: { en: 'Sinhala', native: 'සිංහල' },
        sk: { en: 'Slovak', native: 'Slovenčina' },
        sl: { en: 'Slovenian', native: 'Slovenščina' },
        es: { en: 'Spanish', native: 'Español' },
        su: { en: 'Sundanese', native: 'Basa Sunda' },
        sw: { en: 'Swahili', native: 'Kiswahili' },
        sv: { en: 'Swedish', native: 'Svenska' },
        tl: { en: 'Tagalog', native: 'Tagalog' },
        ta: { en: 'Tamil', native: 'தமிழ்' },
        te: { en: 'Telugu', native: 'తెలుగు' },
        th: { en: 'Thai', native: 'ไทย' },
        tr: { en: 'Turkish', native: 'Türkçe' },
        uk: { en: 'Ukrainian', native: 'Українська' },
        ur: { en: 'Urdu', native: 'اردو' },
        uz: { en: 'Uzbek', native: 'Oʻzbekcha' },
        vi: { en: 'Vietnamese', native: 'Tiếng Việt' },
        cy: { en: 'Welsh', native: 'Cymraeg' },
        zu: { en: 'Zulu', native: 'isiZulu' },
    };

    function buildLanguageList(codes) {
        return (codes || []).map((rawCode) => {
            const code = String(rawCode || '');
            const info = LANGUAGE_NAME_MAP[code.toLowerCase()] || LANGUAGE_NAME_MAP[code];
            return {
                code,
                en: info ? info.en : code,
                native: info ? info.native : code,
            };
        });
    }

    function create(initialCodes = Object.keys(LANGUAGE_NAME_MAP)) {
        let languages = buildLanguageList(initialCodes);

        function setCodes(codes) {
            if (!Array.isArray(codes) || codes.length === 0) {
                return false;
            }
            languages = buildLanguageList(codes);
            return true;
        }

        function getLanguages() {
            return languages;
        }

        function displayName(code) {
            const normalized = String(code || '').trim().toLowerCase();
            const info = languages.find((language) => String(language.code).toLowerCase() === normalized);
            return info ? `${info.en} - ${info.native}` : String(code || '');
        }

        function first() {
            const language = languages[0];
            return language && language.code ? language.code : 'en';
        }

        function coerce(code, fallback = 'en') {
            const desired = String(code || '').trim().toLowerCase();
            const fallbackCode = String(fallback || '').trim().toLowerCase();
            const desiredMatch = languages.find(
                (language) => String(language.code).toLowerCase() === desired,
            );
            if (desiredMatch) {
                return desiredMatch.code;
            }
            const fallbackMatch = languages.find(
                (language) => String(language.code).toLowerCase() === fallbackCode,
            );
            return fallbackMatch ? fallbackMatch.code : first();
        }

        return { setCodes, getLanguages, displayName, first, coerce };
    }

    const api = { LANGUAGE_NAME_MAP, buildLanguageList, create };
    root.LanguageCatalog = api;
    if (typeof module !== 'undefined') {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);


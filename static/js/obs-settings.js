(function (root) {
    'use strict';

    const FONT_SIZE_DEFAULT = 32;
    const FONT_SIZE_MIN = 16;
    const FONT_SIZE_MAX = 120;

    const TRANSLATION_FONT_SCALE_DEFAULT = 100;
    const TRANSLATION_FONT_SCALE_MIN = 50;
    const TRANSLATION_FONT_SCALE_MAX = 150;

    const OUTLINE_WIDTH_DEFAULT = 0;
    const OUTLINE_WIDTH_MIN = 0;
    const OUTLINE_WIDTH_MAX = 8;

    const BG_OPACITY_DEFAULT = 59;
    const BG_OPACITY_MIN = 0;
    const BG_OPACITY_MAX = 100;

    const MAX_SENTENCES_DEFAULT = 2;
    const MAX_SENTENCES_MIN = 1;
    const MAX_SENTENCES_MAX = 10;

    const AUTO_HIDE_SECONDS_DEFAULT = 0;
    const AUTO_HIDE_SECONDS_MIN = 0;
    const AUTO_HIDE_SECONDS_MAX = 30;

    const VALID_DISPLAY_CONTENTS = Object.freeze(['both', 'original', 'translation']);
    const VALID_FONT_FAMILIES = Object.freeze([
        'system',
        'microsoft_yahei',
        'simhei',
        'simsun',
        'kaiti',
        'noto_sans_cjk',
    ]);
    const VALID_BG_MODES = Object.freeze(['none', 'strip', 'block']);
    const VALID_FLOW_DIRECTIONS = Object.freeze(['down', 'up']);
    const VALID_ALIGNMENTS = Object.freeze(['left', 'center', 'right']);

    const FONT_FAMILY_STACKS = Object.freeze({
        system: '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic", "Meiryo", "Malgun Gothic", "Noto Sans SC", sans-serif',
        microsoft_yahei: '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
        simhei: '"SimHei", "Heiti SC", "Microsoft YaHei", sans-serif',
        simsun: '"SimSun", "NSimSun", "Songti SC", serif',
        kaiti: '"KaiTi", "STKaiti", "Kaiti SC", serif',
        noto_sans_cjk: '"Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK KR", "Noto Sans SC", "Microsoft YaHei", sans-serif',
    });

    const HEX_COLOR_RE = /^#[0-9a-f]{6}$/i;

    const DEFAULT_SETTINGS = Object.freeze({
        displayContent: 'both',
        fontSize: FONT_SIZE_DEFAULT,
        translationFontScale: TRANSLATION_FONT_SCALE_DEFAULT,
        fontFamily: 'system',
        bold: false,
        showLangTag: true,
        originalColor: '#e5e7eb',
        translationColor: '#e5e7eb',
        outlineWidth: OUTLINE_WIDTH_DEFAULT,
        outlineColor: '#000000',
        dropShadow: true,
        bgMode: 'strip',
        bgColor: '#000000',
        bgOpacity: BG_OPACITY_DEFAULT,
        flowDirection: 'down',
        alignment: 'left',
        maxSentences: MAX_SENTENCES_DEFAULT,
        autoHideSeconds: AUTO_HIDE_SECONDS_DEFAULT,
    });

    const CAMEL_TO_SNAKE = Object.freeze({
        displayContent: 'display_content',
        fontSize: 'font_size',
        translationFontScale: 'translation_font_scale',
        fontFamily: 'font_family',
        bold: 'bold',
        showLangTag: 'show_lang_tag',
        originalColor: 'original_color',
        translationColor: 'translation_color',
        outlineWidth: 'outline_width',
        outlineColor: 'outline_color',
        dropShadow: 'drop_shadow',
        bgMode: 'bg_mode',
        bgColor: 'bg_color',
        bgOpacity: 'bg_opacity',
        flowDirection: 'flow_direction',
        alignment: 'alignment',
        maxSentences: 'max_sentences',
        autoHideSeconds: 'auto_hide_seconds',
    });

    function clampInt(value, min, max, fallback) {
        if (typeof value === 'boolean' || value === null || value === '') return fallback;
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return fallback;
        return Math.max(min, Math.min(max, Math.round(parsed)));
    }

    function normalizeEnum(value, validList, fallback) {
        if (typeof value !== 'string') return fallback;
        const normalized = value.trim().toLowerCase();
        return validList.includes(normalized) ? normalized : fallback;
    }

    function normalizeBool(value, fallback) {
        return typeof value === 'boolean' ? value : fallback;
    }

    function normalizeHexColor(value, fallback) {
        if (typeof value !== 'string') return fallback;
        const trimmed = value.trim();
        return HEX_COLOR_RE.test(trimmed) ? trimmed.toLowerCase() : fallback;
    }

    function normalizeFontSize(value) {
        return clampInt(value, FONT_SIZE_MIN, FONT_SIZE_MAX, FONT_SIZE_DEFAULT);
    }

    function normalizeDisplayContent(value) {
        return normalizeEnum(value, VALID_DISPLAY_CONTENTS, DEFAULT_SETTINGS.displayContent);
    }

    function pickField(source, camelKey, snakeKey) {
        if (source[camelKey] !== undefined) return source[camelKey];
        if (source[snakeKey] !== undefined) return source[snakeKey];
        return undefined;
    }

    function normalizeSettings(raw) {
        const source = raw && typeof raw === 'object' ? raw : {};
        return {
            displayContent: normalizeDisplayContent(pickField(source, 'displayContent', 'display_content')),
            fontSize: normalizeFontSize(pickField(source, 'fontSize', 'font_size')),
            translationFontScale: clampInt(
                pickField(source, 'translationFontScale', 'translation_font_scale'),
                TRANSLATION_FONT_SCALE_MIN,
                TRANSLATION_FONT_SCALE_MAX,
                DEFAULT_SETTINGS.translationFontScale,
            ),
            fontFamily: normalizeEnum(
                pickField(source, 'fontFamily', 'font_family'),
                VALID_FONT_FAMILIES,
                DEFAULT_SETTINGS.fontFamily,
            ),
            bold: normalizeBool(pickField(source, 'bold', 'bold'), DEFAULT_SETTINGS.bold),
            showLangTag: normalizeBool(pickField(source, 'showLangTag', 'show_lang_tag'), DEFAULT_SETTINGS.showLangTag),
            originalColor: normalizeHexColor(
                pickField(source, 'originalColor', 'original_color'),
                DEFAULT_SETTINGS.originalColor,
            ),
            translationColor: normalizeHexColor(
                pickField(source, 'translationColor', 'translation_color'),
                DEFAULT_SETTINGS.translationColor,
            ),
            outlineWidth: clampInt(
                pickField(source, 'outlineWidth', 'outline_width'),
                OUTLINE_WIDTH_MIN,
                OUTLINE_WIDTH_MAX,
                DEFAULT_SETTINGS.outlineWidth,
            ),
            outlineColor: normalizeHexColor(
                pickField(source, 'outlineColor', 'outline_color'),
                DEFAULT_SETTINGS.outlineColor,
            ),
            dropShadow: normalizeBool(pickField(source, 'dropShadow', 'drop_shadow'), DEFAULT_SETTINGS.dropShadow),
            bgMode: normalizeEnum(pickField(source, 'bgMode', 'bg_mode'), VALID_BG_MODES, DEFAULT_SETTINGS.bgMode),
            bgColor: normalizeHexColor(pickField(source, 'bgColor', 'bg_color'), DEFAULT_SETTINGS.bgColor),
            bgOpacity: clampInt(
                pickField(source, 'bgOpacity', 'bg_opacity'),
                BG_OPACITY_MIN,
                BG_OPACITY_MAX,
                DEFAULT_SETTINGS.bgOpacity,
            ),
            flowDirection: normalizeEnum(
                pickField(source, 'flowDirection', 'flow_direction'),
                VALID_FLOW_DIRECTIONS,
                DEFAULT_SETTINGS.flowDirection,
            ),
            alignment: normalizeEnum(
                pickField(source, 'alignment', 'alignment'),
                VALID_ALIGNMENTS,
                DEFAULT_SETTINGS.alignment,
            ),
            maxSentences: clampInt(
                pickField(source, 'maxSentences', 'max_sentences'),
                MAX_SENTENCES_MIN,
                MAX_SENTENCES_MAX,
                DEFAULT_SETTINGS.maxSentences,
            ),
            autoHideSeconds: clampInt(
                pickField(source, 'autoHideSeconds', 'auto_hide_seconds'),
                AUTO_HIDE_SECONDS_MIN,
                AUTO_HIDE_SECONDS_MAX,
                DEFAULT_SETTINGS.autoHideSeconds,
            ),
        };
    }

    function toBackendPayload(raw) {
        const normalized = normalizeSettings(raw);
        const payload = {};
        for (const [camelKey, snakeKey] of Object.entries(CAMEL_TO_SNAKE)) {
            payload[snakeKey] = normalized[camelKey];
        }
        return payload;
    }

    function areSettingsEqual(a, b) {
        const na = normalizeSettings(a);
        const nb = normalizeSettings(b);
        for (const key of Object.keys(DEFAULT_SETTINGS)) {
            if (na[key] !== nb[key]) return false;
        }
        return true;
    }

    function hexToRgb(hex) {
        const clean = normalizeHexColor(hex, '#000000').slice(1);
        return {
            r: parseInt(clean.slice(0, 2), 16),
            g: parseInt(clean.slice(2, 4), 16),
            b: parseInt(clean.slice(4, 6), 16),
        };
    }

    function computeStyleVars(input) {
        const settings = (input && typeof input === 'object')
            ? normalizeSettings(input)
            : normalizeSettings({ fontSize: input });
        const fs = settings.fontSize;
        const tagFs = Math.max(9, Math.floor(fs * 0.55));
        const { r, g, b } = hexToRgb(settings.bgColor);
        const opacityFrac = settings.bgOpacity / 100;
        const bgRgba = `rgba(${r}, ${g}, ${b}, ${opacityFrac})`;
        const trScale = settings.translationFontScale / 100;
        const stroke = settings.outlineWidth > 0
            ? `${settings.outlineWidth * 2}px ${settings.outlineColor}`
            : '0px transparent';
        const textShadow = settings.dropShadow
            ? '0 1px 3px rgba(0, 0, 0, .85), 0 2px 6px rgba(0, 0, 0, .55)'
            : 'none';
        const justify = settings.flowDirection === 'up' ? 'flex-end' : 'flex-start';

        const vars = {
            '--fs': `${fs}px`,
            '--a': String(opacityFrac),
            '--pad': `${Math.max(5, Math.round(fs * 0.26))}px`,
            '--tag-fs': `${tagFs}px`,
            '--tag-px': `${Math.max(5, Math.floor(tagFs * 0.6))}px`,
            '--tag-py': `${Math.max(2, Math.floor(tagFs * 0.3))}px`,
            '--tag-gap': `${Math.max(4, Math.floor(tagFs * 0.45))}px`,
            '--ov-tr-scale': String(trScale),
            '--ov-font-family': FONT_FAMILY_STACKS[settings.fontFamily] || FONT_FAMILY_STACKS.system,
            '--ov-font-weight': settings.bold ? '700' : '400',
            '--ov-orig-color': settings.originalColor,
            '--ov-trans-color': settings.translationColor,
            '--ov-stroke': stroke,
            '--ov-text-shadow': textShadow,
            '--ov-bg-rgba': bgRgba,
            '--ov-justify': justify,
            '--ov-text-align': settings.alignment,
        };
        const sentMb = Math.max(5, Math.floor(fs * 0.45));
        return { fs, tagFs, sentMb, settings, vars };
    }

    const api = {
        FONT_SIZE_DEFAULT,
        FONT_SIZE_MIN,
        FONT_SIZE_MAX,
        TRANSLATION_FONT_SCALE_DEFAULT,
        TRANSLATION_FONT_SCALE_MIN,
        TRANSLATION_FONT_SCALE_MAX,
        OUTLINE_WIDTH_DEFAULT,
        OUTLINE_WIDTH_MIN,
        OUTLINE_WIDTH_MAX,
        BG_OPACITY_DEFAULT,
        BG_OPACITY_MIN,
        BG_OPACITY_MAX,
        MAX_SENTENCES_DEFAULT,
        MAX_SENTENCES_MIN,
        MAX_SENTENCES_MAX,
        AUTO_HIDE_SECONDS_DEFAULT,
        AUTO_HIDE_SECONDS_MIN,
        AUTO_HIDE_SECONDS_MAX,
        VALID_DISPLAY_CONTENTS,
        VALID_FONT_FAMILIES,
        VALID_BG_MODES,
        VALID_FLOW_DIRECTIONS,
        VALID_ALIGNMENTS,
        FONT_FAMILY_STACKS,
        DEFAULT_SETTINGS,
        CAMEL_TO_SNAKE,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeHexColor,
        normalizeSettings,
        toBackendPayload,
        areSettingsEqual,
        computeStyleVars,
    };

    root.ObsSettings = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

let ws;
const subtitleContainer = document.getElementById('subtitleContainer');
const themeToggle = document.getElementById('themeToggle');
const themeIcon = document.getElementById('themeIcon');
const restartButton = document.getElementById('restartButton');
const pauseButton = document.getElementById('pauseButton');
const pauseIcon = document.getElementById('pauseIcon');
const autoRestartButton = document.getElementById('autoRestartButton');
const autoRestartIcon = document.getElementById('autoRestartIcon');
const audioSourceButton = document.getElementById('audioSourceButton');
const audioSourceIcon = document.getElementById('audioSourceIcon');
const overlayButton = document.getElementById('overlayButton');
const overlayIcon = document.getElementById('overlayIcon');
let overlayOpen = false;  // 原生字幕悬浮窗当前是否打开
const segmentModeButton = document.getElementById('segmentModeButton');
const segmentModeText = document.getElementById('segmentModeText');
const displayModeButton = document.getElementById('displayModeButton');
const displayModeText = document.getElementById('displayModeText');
const oscTranslationButton = document.getElementById('oscTranslationButton');
const oscTranslationIcon = document.getElementById('oscTranslationIcon');
const furiganaButton = document.getElementById('furiganaButton');
const furiganaIcon = document.getElementById('furiganaIcon');
const translationLangButton = document.getElementById('translationLangButton');
const translationLangIcon = document.getElementById('translationLangIcon');
const translationRefineButton = document.getElementById('translationRefineButton');
const translationRefineIcon = document.getElementById('translationRefineIcon');
const bottomSafeAreaButton = document.getElementById('bottomSafeAreaButton');
const bottomSafeAreaIcon = document.getElementById('bottomSafeAreaIcon');
const isMobileBrowser = /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
const ICON_SPRITE_URL = 'icons/lucide-sprite.svg';

function setControlIcon(iconEl, iconName) {
    if (!iconEl || !iconName) {
        return;
    }
    const useEl = iconEl.querySelector('use');
    if (!useEl) {
        return;
    }
    const href = `${ICON_SPRITE_URL}#${iconName}`;
    useEl.setAttribute('href', href);
    useEl.setAttributeNS('http://www.w3.org/1999/xlink', 'href', href);
}

const t = (key, vars) => {
    try {
        if (window.I18N && typeof window.I18N.t === 'function') {
            return window.I18N.t(key, vars);
        }
    } catch (error) {
        // ignore
    }
    return key;
};

function localizeBackendMessage(message) {
    if (message === null || message === undefined) {
        return message;
    }

    const raw = String(message).trim();
    if (!raw) {
        return raw;
    }

    const directMap = {
        'Manual restart is disabled by server config': 'backend_manual_restart_disabled',
        'Pause is disabled by server config': 'backend_pause_disabled',
        'Resume is disabled by server config': 'backend_resume_disabled',
        'Audio source switching is disabled by server config': 'backend_audio_source_disabled',
        'Microphone device switching is disabled by server config': 'backend_microphone_device_disabled',
        'OSC translation toggle is disabled by server config': 'backend_osc_disabled',
        'Overlay control is disabled by server config': 'backend_overlay_disabled',
        'Segment mode switching is disabled': 'backend_segment_mode_disabled',
        'LLM refine toggle is disabled by server config': 'backend_llm_refine_disabled',
        'Furigana feature not available (pykakasi not installed)': 'backend_furigana_unavailable',
    };

    const key = directMap[raw];
    if (key) {
        return t(key);
    }

    // Lightweight heuristics for similar messages without changing backend.
    if (/disabled by server config/i.test(raw)) {
        return raw;
    }

    return raw;
}

// 由后端下发：锁定“手动控制”相关 UI
let lockManualControls = false;

// 由后端下发：LLM 译文修复能力是否可用（缺少 API key 时为 false）
let llmRefineAvailable = false;

// 由后端下发：是否展示 refined 译文的修订 diff（无前端开关）
let llmRefineShowDiff = false;

// 由后端下发：diff 高亮时是否显示“被删除”的文本（无前端开关）
let llmRefineShowDeletions = false;

// 由后端下发：是否启用说话人分离
let speakerDiarizationEnabled = true;
// 由后端下发：是否隐藏说话人标签
let hideSpeakerLabels = false;
// 由后端下发：当前 provider 及能力（segment_mode 等）。
let translationProvider = 'soniox';
let segmentModeSupported = true;
let twoWaySupported = true;

// ===== 运行时 provider/key（热切换）+ 设置面板状态 =====
const settingsButton = document.getElementById('settingsButton');
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsPanel = document.getElementById('settingsPanel');
const settingsForm = document.getElementById('settingsForm');
const settingsCloseButton = document.getElementById('settingsCloseButton');
const settingsCancelButton = document.getElementById('settingsCancelButton');
const settingsSaveButton = document.getElementById('settingsSaveButton');
const settingsModeBackButton = document.getElementById('settingsModeBackButton');
const resetAllButton = document.getElementById('resetAllButton');
const settingsErrorEl = document.getElementById('settingsError');
const confirmOverlay = document.getElementById('confirmOverlay');
const confirmDialog = document.getElementById('confirmDialog');
const confirmMessageEl = document.getElementById('confirmMessage');
const confirmOkButton = document.getElementById('confirmOkButton');
const confirmCancelButton = document.getElementById('confirmCancelButton');
const apiKeyInput = document.getElementById('apiKeyInput');
const apiKeySourceHint = document.getElementById('apiKeySourceHint');
const providerDescription = document.getElementById('providerDescription');
const apiKeyGetLink = document.getElementById('apiKeyGetLink');
const sonioxRegionSection = document.getElementById('sonioxRegionSection');
const sonioxRegionPickerHost = document.getElementById('sonioxRegionPicker');
const microphoneDeviceSection = document.getElementById('microphoneDeviceSection');
const microphoneDevicePickerHost = document.getElementById('microphoneDevicePicker');
const microphoneDeviceHint = document.getElementById('microphoneDeviceHint');
const runtimeControlsSection = document.getElementById('runtimeControlsSection');
const autoRestartPickerHost = document.getElementById('autoRestartPicker');
const segmentModeSettingField = document.getElementById('segmentModeSettingField');
const segmentModePickerHost = document.getElementById('segmentModePicker');
const toastEl = document.getElementById('toast');

const SONIOX_REGIONS = ['us', 'eu', 'jp'];
// Custom-select element (built lazily); mirrors the language picker styling.
let sonioxRegionPickerEl = null;
let microphoneDevicePickerEl = null;
let autoRestartPickerEl = null;
let segmentModePickerEl = null;
let microphoneDeviceData = { available: false, default: null, devices: [], selected_id: '' };

// Where users obtain an API key for each provider (shown as a link in Settings).
const PROVIDER_KEY_URLS = {
    soniox: 'https://console.soniox.com/api-keys',
    gemini: 'https://aistudio.google.com/apikey',
};

const PROVIDER_SETTINGS_STORAGE_KEY = 'providerSettings.v1';
const UI_TRANSLATION_MODE_STORAGE_KEY = 'uiTranslationMode';

let backendBootId = '';
let setupRequired = false;
let envKeyPresent = { soniox: false, gemini: false };
let backendKeySource = 'env';
let backendSonioxRegion = 'us';
// True when the backend pins a custom Soniox endpoint; region is not selectable.
let backendSonioxCustomUrl = false;
let backendTranslationMode = 'one_way';
let backendTargetLang1 = 'en';
let backendTargetLang2 = 'zh';
let suppressTranslationDisplay = false;
let pushedOverrideBootId = null;
let settingsForcedOpen = false;
let toastTimer = null;

// ---- Subtitle-server relay (hosted mode) state ----
const SUBTITLE_SERVER_STORAGE_KEY = 'subtitleServer.v1';
// Optional client-update reminders throttle: at least this many ms between popups.
const CLIENT_UPDATE_REMINDER_MIN_INTERVAL_MS = 20 * 60 * 60 * 1000;
const CLIENT_UPDATE_REMINDER_KEY = 'clientUpdateReminderLastShown';
let relayAvailable = false;
let relayServerUrl = '';
let creditsPurchaseUrl = '';
let clientVersion = '0.1.0';
let clientLatestVersion = '';
let clientMinimumVersion = '';
let clientUpdateUrl = '';
let clientUpdateNotes = '';
let backendMode = 'direct';
let backendLoggedIn = false;
let loginForcedOpen = false;
let relayPricing = null; // { soniox: {price_per_second, free_*}, gemini: {...} }
let loginChallengeId = '';
let loginExpiryTimer = null;
let loginRegistrationInfo = null; // { bonuses: [...], registration_threshold }
let loginProfile = null;          // resolved VRChat profile { vrc_user_id, display_name, trust_rank }
let loginMethods = [];            // verification methods the server enabled (bio/link/status)
let loginMethod = 'bio';          // currently selected verification method
// Balance bar / this-session cost meter.
let balancePollTimer = null;
let sessionCostTimer = null;
let sessionAccumMs = 0;
let sessionRunSince = null;
let pricePerSecond = 0;
let lastBalanceData = null; // last /account/balance payload, for the account panel
// Maps backend relay close-code tags to localized message keys.
const RELAY_ERROR_KEYS = {
    billing_exhausted: 'relay_err_billing_exhausted',
    upstream_key_error: 'relay_err_upstream_key_error',
    forbidden: 'relay_err_forbidden',
    model_not_allowed: 'relay_err_model_not_allowed',
    concurrency_limit: 'relay_err_concurrency_limit',
};

function safeHttpUrl(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    try {
        const url = new URL(raw);
        if (url.protocol === 'http:' || url.protocol === 'https:') {
            return url.href;
        }
    } catch (e) {
        return '';
    }
    return '';
}

// Normalize a server URL into a stable storage key (host is case-insensitive,
// trailing slash stripped) so the same server maps to one credential bucket.
function normalizeServerUrl(url) {
    const raw = String(url || '').trim();
    if (!raw) return '';
    try {
        const u = new URL(raw);
        const path = u.pathname.replace(/\/+$/, '');
        return u.protocol + '//' + u.host.toLowerCase() + path;
    } catch (e) {
        return raw.replace(/\/+$/, '');
    }
}

// Raw on-disk shape: global `mode`/`modeChosen` + per-server credentials keyed
// by normalized server URL: { servers: { <url>: { token, displayName, trustRank } } }.
function loadServerSettingsRaw() {
    try {
        const raw = localStorage.getItem(SUBTITLE_SERVER_STORAGE_KEY);
        if (raw) {
            const obj = JSON.parse(raw);
            if (obj && typeof obj === 'object') {
                const out = Object.assign({ mode: null, modeChosen: false }, obj);
                if (!out.servers || typeof out.servers !== 'object') {
                    out.servers = {};
                }
                return out;
            }
        }
    } catch (e) {
        // ignore
    }
    return { mode: null, modeChosen: false, servers: {} };
}

// A per-server *view*: flattens the current server's credentials to the top
// level so callers can read/write `token`/`displayName`/`trustRank` directly.
// `mode`/`modeChosen` stay global. The current server is `relayServerUrl`.
function loadServerSettings() {
    const raw = loadServerSettingsRaw();
    const key = normalizeServerUrl(relayServerUrl);
    let creds = key ? raw.servers[key] : null;
    // Migrate pre-per-server data: a top-level token belongs to the current server.
    if (!creds && (raw.token || raw.displayName || raw.trustRank)) {
        creds = { token: raw.token || '', displayName: raw.displayName || '', trustRank: raw.trustRank || '' };
    }
    creds = creds || { token: '', displayName: '', trustRank: '' };
    return {
        mode: raw.mode,
        modeChosen: raw.modeChosen,
        token: creds.token || '',
        displayName: creds.displayName || '',
        trustRank: creds.trustRank || '',
        servers: raw.servers,
    };
}

function saveServerSettings(settings) {
    try {
        const raw = loadServerSettingsRaw();
        raw.mode = settings.mode;
        raw.modeChosen = settings.modeChosen;
        const key = normalizeServerUrl(relayServerUrl);
        if (key) {
            raw.servers[key] = {
                token: settings.token || '',
                displayName: settings.displayName || '',
                trustRank: settings.trustRank || '',
            };
            // Drop legacy top-level credentials once migrated to a per-server bucket.
            delete raw.token;
            delete raw.displayName;
            delete raw.trustRank;
        }
        localStorage.setItem(SUBTITLE_SERVER_STORAGE_KEY, JSON.stringify(raw));
    } catch (e) {
        // ignore
    }
}

function hasExplicitConnectionMode(settings) {
    if (!settings || typeof settings !== 'object') {
        return false;
    }
    if (settings.modeChosen === true) {
        return true;
    }
    // Backwards-compatible migration for users who already completed hosted
    // login before this flag existed.
    return settings.mode === 'relay' && !!settings.token;
}

// Resolved connection mode: 'relay' | 'direct' | null (undecided / first launch).
function getConnectionMode() {
    if (!relayAvailable) {
        return 'direct';
    }
    const s = loadServerSettings();
    if ((s.mode === 'relay' || s.mode === 'direct') && hasExplicitConnectionMode(s)) {
        return s.mode;
    }
    return null;
}

let uiTranslationMode = localStorage.getItem(UI_TRANSLATION_MODE_STORAGE_KEY);
if (!['none', 'one_way', 'two_way'].includes(uiTranslationMode)) {
    uiTranslationMode = null;
}

function loadProviderSettings() {
    try {
        const raw = localStorage.getItem(PROVIDER_SETTINGS_STORAGE_KEY);
        if (raw) {
            const obj = JSON.parse(raw);
            if (obj && typeof obj === 'object') {
                if (!obj.keys || typeof obj.keys !== 'object') {
                    obj.keys = {};
                }
                return obj;
            }
        }
    } catch (e) {
        // ignore
    }
    return { providerOverride: null, keys: {} };
}

function saveProviderSettings(settings) {
    try {
        localStorage.setItem(PROVIDER_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    } catch (e) {
        // ignore
    }
}

function showToast(message, isError = false, options = {}) {
    if (!toastEl) {
        return;
    }
    toastEl.textContent = '';
    const text = document.createElement('span');
    text.textContent = message;
    toastEl.appendChild(text);
    if (options.actionLabel && typeof options.onAction === 'function') {
        const action = document.createElement('button');
        action.type = 'button';
        action.className = 'toast-action';
        action.textContent = options.actionLabel;
        action.addEventListener('click', () => {
            toastEl.hidden = true;
            options.onAction();
        });
        toastEl.appendChild(action);
    }
    toastEl.classList.toggle('error', !!isError);
    toastEl.hidden = false;
    if (toastTimer) {
        clearTimeout(toastTimer);
    }
    toastTimer = setTimeout(() => {
        toastEl.hidden = true;
    }, Number(options.timeoutMs) || 4000);
}

const LLM_REFINE_MODES = ['off', 'refine', 'translate'];
const LLM_REFINE_ICON = 'wand-sparkles';
const LLM_TRANSLATE_ICON = 'bot';
const LLM_REFINE_MODE_STORAGE_KEY = 'llmRefineMode';
const LLM_TRANSLATION_MODE_STORAGE_KEY = 'llmTranslationMode';
let defaultLlmRefineMode = null;

function normalizeSegmentMode(mode) {
    const value = (mode || '').toString().trim();
    return SEGMENT_MODES.includes(value) ? value : null;
}

// 译文自动修复开关（默认关闭）
let llmRefineMode = getStoredLlmRefineMode();
if (!llmRefineMode) {
    const legacy = localStorage.getItem('llmRefineEnabled');
    llmRefineMode = legacy === 'true' ? 'refine' : 'off';
}
let llmRefineEnabled = llmRefineMode !== 'off';
let llmTranslateHideAfterSequence = llmRefineMode === 'translate' ? 0 : null;

// 存储后端改进结果
const backendRefinedResults = new Map();
const backendRefinedResultsBySentenceId = new Map();
// LLM 直译模式下覆盖 Soniox 译文
const llmTranslationOverrides = new Map();
const llmTranslationOverridesBySentenceId = new Map();

// 由后端下发：默认翻译目标语言（ISO 639-1）
let defaultTranslationTargetLang = 'en';
let currentTranslationTargetLang = 'en';

// Single source of truth for language display names (English + native). Covers
// the union of all providers' codes. The actual ordered list of *available*
// languages is driven by the backend (/ui-config -> languages), since each
// provider supports a different subset (e.g. Gemini splits zh into
// zh-hans / zh-hant). See setLanguageListFromCodes().
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
        const code = (rawCode || '').toString();
        const info = LANGUAGE_NAME_MAP[code.toLowerCase()] || LANGUAGE_NAME_MAP[code];
        return {
            code,
            en: info ? info.en : code,
            native: info ? info.native : code,
        };
    });
}

// Available translation languages (populated from the backend on load; falls
// back to the full known set until /ui-config responds).
let SUPPORTED_TRANSLATION_LANGUAGES = buildLanguageList(Object.keys(LANGUAGE_NAME_MAP));

function setLanguageListFromCodes(codes) {
    if (!Array.isArray(codes) || codes.length === 0) {
        return;
    }
    SUPPORTED_TRANSLATION_LANGUAGES = buildLanguageList(codes);
    // Invalidate the cached popover so it rebuilds with the new language set.
    closeLangSelectMenu();
    if (langPopoverEl && langPopoverEl.parentNode) {
        langPopoverEl.parentNode.removeChild(langPopoverEl);
    }
    langPopoverEl = null;
}

let langPopoverEl = null;
let langPopoverOpen = false;
let langPopoverCleanup = null;
let langPopoverDraft = null;
let langSelectMenuEl = null;
let activeLangPickerEl = null;

// 存储所有已确认的tokens
let allFinalTokens = [];
// 存储当前未确认的tokens
let currentNonFinalTokens = [];
// 记录已合并到的位置（allFinalTokens 中的索引）
let lastMergedIndex = 0;

// 缓存已渲染的句子 HTML（用于增量渲染，键为 sentenceId）
let renderedSentences = new Map();
// 缓存已渲染的 speaker/块 HTML（用于按块增量渲染，键为 blockId）
let renderedBlocks = new Map();

const SCROLL_STICKY_THRESHOLD = 50;
let autoStickToBottom = true;
let tokenSequenceCounter = 0;

// 分段模式: 'translation' | 'endpoint' | 'punctuation'
let segmentMode = localStorage.getItem('segmentMode') || 'punctuation';
const SEGMENT_MODES = ['translation', 'endpoint', 'punctuation'];
if (!SEGMENT_MODES.includes(segmentMode)) {
    segmentMode = 'punctuation';
}

// 显示模式: 'both', 'original', 'translation'
let displayMode = localStorage.getItem('displayMode') || 'both';

// 自动重启识别开关（默认开启；已有保存值优先）
const storedAutoRestartEnabled = localStorage.getItem('autoRestartEnabled');
let autoRestartEnabled = storedAutoRestartEnabled === null ? true : storedAutoRestartEnabled === 'true';

// OSC 翻译发送开关（默认关闭）
let oscTranslationEnabled = false;

// 日语假名注音开关（默认关闭）
// 注意：使用 sessionStorage（按“标签页/客户端实例”隔离），避免同一设备多客户端互相影响。
let furiganaEnabled = false;
try {
    furiganaEnabled = sessionStorage.getItem('furiganaEnabled') === 'true';
} catch (storageError) {
    console.warn('Unable to access sessionStorage for furigana preference:', storageError);
}
// 假名注音缓存（避免重复请求）
let furiganaCache = new Map();
const pendingFuriganaRequests = new Set();
let kuromojiTokenizerPromise = null;

// 移动端底部留白开关（默认关闭）
let bottomSafeAreaEnabled = localStorage.getItem('bottomSafeAreaEnabled') === 'true';

// 控制标志
let shouldReconnect = true;  // 是否应该自动重连
let isRestarting = false;    // 是否正在重启中
let isPaused = false;        // 是否暂停中
let audioSource = 'system';  // 音频输入来源
const AUDIO_SOURCES = ['system', 'microphone', 'mix'];

function normalizeAudioSource(source) {
    const value = (source || '').toString().trim().toLowerCase();
    return AUDIO_SOURCES.includes(value) ? value : 'system';
}

function getNextAudioSource(source) {
    const current = normalizeAudioSource(source);
    const index = AUDIO_SOURCES.indexOf(current);
    return AUDIO_SOURCES[(index + 1) % AUDIO_SOURCES.length];
}

// 初始化按钮文本
updateSegmentModeButton();
updateDisplayModeButton();
updatePauseButtonUi();
updateAudioSourceButton();
updateFuriganaButton();
updateOscTranslationButton();
updateAutoRestartButton();
updateBottomSafeAreaButton();
updateTranslationRefineButton();
enforceTranslateSegmentMode();
applySpeakerLabelVisibility();
applyBottomSafeArea();
applyLockPauseRestartControlsUI();
applyStaticUiText();

function applyStaticUiText() {
    if (document && document.documentElement) {
        try {
            document.documentElement.lang = (window.I18N && window.I18N.lang) ? window.I18N.lang : 'en';
        } catch (error) {
            // ignore
        }
    }

    if (themeToggle) {
        themeToggle.title = t('theme_toggle');
    }

    if (restartButton) {
        restartButton.title = t('restart');
    }

    if (translationLangButton) {
        translationLangButton.title = t('translation_language');
    }

    if (translationRefineButton) {
        translationRefineButton.title = t(getLlmRefineTitleKey());
    }

    if (pauseButton) {
        updatePauseButtonUi();
    }

    if (overlayButton) {
        overlayButton.title = overlayOpen ? t('overlay_close') : t('overlay_open');
    }

    if (subtitleContainer) {
        const emptyNode = subtitleContainer.querySelector('.empty-state');
        if (emptyNode) {
            emptyNode.textContent = t('empty_state');
        }
    }
}

function applySpeakerLabelVisibility() {
    if (!subtitleContainer) {
        return;
    }
    subtitleContainer.classList.toggle('hide-speaker-labels', !!hideSpeakerLabels);
}

function normalizeLlmRefineMode(mode) {
    const value = (mode || '').toString().trim().toLowerCase();
    if (LLM_REFINE_MODES.includes(value)) {
        return value;
    }
    return 'off';
}

function getStoredLlmRefineMode() {
    let rawStoredMode = localStorage.getItem(LLM_TRANSLATION_MODE_STORAGE_KEY);
    if (!LLM_REFINE_MODES.includes((rawStoredMode || '').toString().trim().toLowerCase())) {
        rawStoredMode = localStorage.getItem(LLM_REFINE_MODE_STORAGE_KEY);
    }
    const normalized = normalizeLlmRefineMode(rawStoredMode);
    return LLM_REFINE_MODES.includes((rawStoredMode || '').toString().trim().toLowerCase())
        ? normalized
        : null;
}

function isLlmTranslateMode() {
    return llmRefineMode === 'translate';
}

function getLlmRefineTitleKey() {
    if (llmRefineMode === 'translate') {
        return 'translation_refine_translate';
    }
    if (llmRefineMode === 'refine') {
        return 'translation_refine_on';
    }
    return 'translation_refine_off';
}

function applyLlmRefineMode(mode, options = {}) {
    const normalized = normalizeLlmRefineMode(mode);
    const previous = llmRefineMode;
    const wasTranslate = previous === 'translate';
    llmRefineMode = normalized;
    llmRefineEnabled = llmRefineMode !== 'off';

    const shouldPersist = options.persist !== false;
    if (shouldPersist) {
        try {
            localStorage.setItem(LLM_REFINE_MODE_STORAGE_KEY, llmRefineMode);
            localStorage.setItem(LLM_TRANSLATION_MODE_STORAGE_KEY, llmRefineMode);
            localStorage.setItem('llmRefineEnabled', llmRefineEnabled ? 'true' : 'false');
        } catch (storageError) {
            console.warn('Unable to persist LLM refine mode:', storageError);
        }
    }

    if (llmRefineMode === 'translate') {
        if (previous !== 'translate') {
            llmTranslateHideAfterSequence = tokenSequenceCounter + 1;
        } else if (llmTranslateHideAfterSequence === null) {
            llmTranslateHideAfterSequence = tokenSequenceCounter + 1;
        }
        enforceTranslateSegmentMode();
    } else {
        llmTranslateHideAfterSequence = null;
    }

    updateTranslationRefineButton();
    updateSegmentModeButton();

    if (wasTranslate && llmRefineMode !== 'translate') {
        renderSubtitles();
    }
}

function enforceTranslateSegmentMode() {
    if (!isLlmTranslateMode()) {
        return;
    }
    if (segmentMode === 'translation') {
        segmentMode = 'punctuation';
        localStorage.setItem('segmentMode', segmentMode);
        updateSegmentModeButton();
        void setSegmentMode('punctuation');
    }
}

function getNextLlmRefineMode(currentMode) {
    if (currentMode === 'off') {
        return 'refine';
    }
    if (currentMode === 'refine') {
        return 'translate';
    }
    return 'off';
}

function getSegmentModes() {
    return isLlmTranslateMode() ? ['endpoint', 'punctuation'] : SEGMENT_MODES;
}

function updateTranslationRefineButton() {
    if (!translationRefineButton || !translationRefineIcon) {
        return;
    }

    // 没有配置 LLM key/base_url 时，隐藏开关。
    // 注意：不要覆盖用户保存的开关偏好（localStorage），否则会导致每次都需要手动重新打开。
    if (!llmRefineAvailable || lockManualControls) {
        translationRefineButton.style.display = 'none';
        return;
    }

    translationRefineButton.style.display = '';

    const isTranslate = isLlmTranslateMode();

    setControlIcon(translationRefineIcon, isTranslate ? LLM_TRANSLATE_ICON : LLM_REFINE_ICON);
    translationRefineButton.title = t(getLlmRefineTitleKey());

    if (llmRefineMode !== 'off') {
        translationRefineButton.classList.add('active');
    } else {
        translationRefineButton.classList.remove('active');
    }

    translationRefineButton.classList.toggle('mode-translate', isTranslate);
}


// 主题切换功能（默认深色）
const ALL_THEMES = ['dark', 'light', 'chroma'];
const THEME_ICONS = {
    dark: 'moon',
    light: 'sun',
    chroma: 'sparkles',
};
let currentTheme = 'dark';
let lastWindowOnTopState = null;
let enableChromaTheme = false;

function getAvailableThemes() {
    return enableChromaTheme ? ALL_THEMES : ['dark', 'light'];
}

async function syncWindowOnTopByTheme(theme) {
    const shouldOnTop = theme !== 'chroma';
    if (lastWindowOnTopState === shouldOnTop) {
        return;
    }

    lastWindowOnTopState = shouldOnTop;

    try {
        await fetch('/window-on-top', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ on_top: shouldOnTop }),
        });
    } catch (error) {
        // 浏览器模式或接口不可用时静默忽略。
    }
}

function applyTheme(theme) {
    const available = getAvailableThemes();
    const normalizedTheme = available.includes(theme) ? theme : 'dark';

    currentTheme = normalizedTheme;

    document.body.classList.remove('dark-theme', 'chroma-theme');

    if (normalizedTheme === 'dark') {
        document.body.classList.add('dark-theme');
    } else if (normalizedTheme === 'chroma') {
        document.body.classList.add('chroma-theme');
    }

    setControlIcon(themeIcon, THEME_ICONS[normalizedTheme]);
    localStorage.setItem('theme', normalizedTheme);
    if (enableChromaTheme) {
        void syncWindowOnTopByTheme(normalizedTheme);
    }
}

// 从localStorage加载主题偏好，覆盖默认值
const savedTheme = localStorage.getItem('theme');
applyTheme(savedTheme);

themeToggle.addEventListener('click', () => {
    const available = getAvailableThemes();
    const currentIndex = available.indexOf(currentTheme);
    const actualIndex = currentIndex >= 0 ? currentIndex : 0;
    const nextTheme = available[(actualIndex + 1) % available.length];
    applyTheme(nextTheme);
});

function updatePauseButtonUi() {
    if (!pauseButton || !pauseIcon) {
        return;
    }
    setControlIcon(pauseIcon, isPaused ? 'play' : 'pause');
    pauseButton.title = isPaused ? t('resume') : t('pause');
    pauseButton.classList.toggle('is-paused', isPaused);
}

// 更新分段模式按钮文本
function updateSegmentModeButton() {
    if (!segmentModeButton) {
        return;
    }

    const translateLocked = isLlmTranslateMode();

    if (segmentMode === 'translation') {
        segmentModeButton.title = t('segment_translation');
    } else if (segmentMode === 'endpoint') {
        segmentModeButton.title = translateLocked ? t('segment_endpoint_no_translation') : t('segment_endpoint');
    } else {
        segmentModeButton.title = translateLocked ? t('segment_punctuation_no_translation') : t('segment_punctuation');
    }
}

// 更新显示模式按钮文本
function updateDisplayModeButton() {
    if (displayMode === 'both') {
        displayModeButton.title = t('display_both');
    } else if (displayMode === 'original') {
        displayModeButton.title = t('display_original');
    } else {
        displayModeButton.title = t('display_translation');
    }
}

function updateOscTranslationButton() {
    if (!oscTranslationButton || !oscTranslationIcon) {
        return;
    }

    if (oscTranslationEnabled) {
        oscTranslationButton.classList.add('active');
        oscTranslationButton.title = t('osc_on');
    } else {
        oscTranslationButton.classList.remove('active');
        oscTranslationButton.title = t('osc_off');
    }
}

function updateBottomSafeAreaButton() {
    if (!bottomSafeAreaButton || !bottomSafeAreaIcon) {
        return;
    }

    // 仅在移动端显示按钮
    bottomSafeAreaButton.style.display = isMobileBrowser ? '' : 'none';
    if (!isMobileBrowser) {
        return;
    }

    if (bottomSafeAreaEnabled) {
        bottomSafeAreaButton.classList.add('active');
        bottomSafeAreaButton.title = t('bottom_safe_area_on');
        setControlIcon(bottomSafeAreaIcon, 'arrow-up-from-line');
    } else {
        bottomSafeAreaButton.classList.remove('active');
        bottomSafeAreaButton.title = t('bottom_safe_area_off');
        setControlIcon(bottomSafeAreaIcon, 'arrow-down-to-line');
    }
}

function applyBottomSafeArea() {
    if (!subtitleContainer) {
        return;
    }
    const shouldAdd = isMobileBrowser && bottomSafeAreaEnabled;
    subtitleContainer.classList.toggle('mobile-bottom-safe-area', shouldAdd);
}

function updateAutoRestartButton() {
    if (!autoRestartButton || !autoRestartIcon) {
        return;
    }

    // UI 锁定时：隐藏按钮并强制开启
    if (lockManualControls) {
        autoRestartButton.style.display = 'none';
        autoRestartEnabled = true;
        return;
    }

    autoRestartButton.style.display = '';

    if (autoRestartEnabled) {
        autoRestartButton.classList.add('active');
        autoRestartButton.title = t('auto_restart_on');
    } else {
        autoRestartButton.classList.remove('active');
        autoRestartButton.title = t('auto_restart_off');
    }
}

function applyLockPauseRestartControlsUI() {
    if (restartButton) {
        restartButton.style.display = lockManualControls ? 'none' : '';
    }
    if (pauseButton) {
        pauseButton.style.display = lockManualControls ? 'none' : '';
    }
    if (audioSourceButton) {
        audioSourceButton.style.display = lockManualControls ? 'none' : '';
    }
    if (oscTranslationButton) {
        oscTranslationButton.style.display = lockManualControls ? 'none' : '';
    }
    if (translationLangButton) {
        translationLangButton.style.display = lockManualControls ? 'none' : '';
    }
    if (segmentModeButton) {
        // Hidden when locked or when the active provider has no segmentation control.
        segmentModeButton.style.display = (lockManualControls || !segmentModeSupported) ? 'none' : '';
    }
    if (translationRefineButton) {
        translationRefineButton.style.display = lockManualControls ? 'none' : '';
    }

    if (lockManualControls) {
        autoRestartEnabled = true;
    }
    updateAutoRestartButton();
}

async function fetchUiConfig() {
    try {
        const response = await fetch('/ui-config');
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        lockManualControls = !!data.lock_manual_controls;
        llmRefineAvailable = !!data.llm_refine_available;
        if (!llmRefineAvailable) {
            // LLM 环境变量缺失时，忽略前端记忆的 LLM 模式，直接禁用 LLM。
            // 不读取也不修改保存的 LLM 翻译模式（localStorage）。
            llmRefineMode = 'off';
            llmRefineEnabled = false;
            llmTranslateHideAfterSequence = null;
        }
        llmRefineShowDiff = !!data.llm_refine_show_diff;
        llmRefineShowDeletions = !!data.llm_refine_show_deletions;
        if (data && typeof data.llm_refine_default_mode === 'string') {
            defaultLlmRefineMode = normalizeLlmRefineMode(data.llm_refine_default_mode);
        }
        if (data && typeof data.translation_target_lang === 'string' && data.translation_target_lang.trim()) {
            defaultTranslationTargetLang = data.translation_target_lang.trim().toLowerCase();
            currentTranslationTargetLang = defaultTranslationTargetLang;
        }
        if (data && typeof data.provider === 'string' && data.provider.trim()) {
            translationProvider = data.provider.trim().toLowerCase();
        }
        // Backend-driven language dropdown (provider-specific subset).
        if (data && Array.isArray(data.languages)) {
            setLanguageListFromCodes(data.languages);
        }
        // Provider capability flags gate provider-specific controls.
        if (data && data.capabilities && typeof data.capabilities === 'object') {
            segmentModeSupported = data.capabilities.segment_mode !== false;
            twoWaySupported = data.capabilities.two_way_translation === true;
        }

        // Runtime provider/key state (hot-switch).
        if (typeof data.boot_id === 'string') {
            backendBootId = data.boot_id;
        }
        setupRequired = !!data.setup_required;
        if (data.env_key_present && typeof data.env_key_present === 'object') {
            envKeyPresent = {
                soniox: !!data.env_key_present.soniox,
                gemini: !!data.env_key_present.gemini,
            };
        }
        if (typeof data.key_source === 'string') {
            backendKeySource = data.key_source;
        }
        if (typeof data.soniox_region === 'string' && data.soniox_region.trim()) {
            backendSonioxRegion = normalizeSonioxRegion(data.soniox_region);
        }
        if (typeof data.soniox_custom_url === 'boolean') {
            backendSonioxCustomUrl = data.soniox_custom_url;
        }
        // Subtitle-server relay (hosted mode) state.
        if (typeof data.relay_available === 'boolean') {
            relayAvailable = data.relay_available;
        }
        if (typeof data.server_url === 'string') {
            relayServerUrl = data.server_url;
        }
        creditsPurchaseUrl = safeHttpUrl(data && data.credits_purchase_url);
        if (typeof data.client_version === 'string' && data.client_version.trim()) {
            clientVersion = data.client_version.trim();
        }
        if (typeof data.client_latest_version === 'string') {
            clientLatestVersion = data.client_latest_version.trim();
        }
        if (typeof data.client_minimum_version === 'string') {
            clientMinimumVersion = data.client_minimum_version.trim();
        }
        clientUpdateUrl = safeHttpUrl(data && data.client_update_url);
        if (typeof data.client_update_notes === 'string') {
            clientUpdateNotes = data.client_update_notes.trim();
        }
        if (typeof data.mode === 'string') {
            backendMode = data.mode;
        }
        if (typeof data.logged_in === 'boolean') {
            backendLoggedIn = data.logged_in;
        }
        updateBalanceBarVisibility();
        updateAccountSection();
        if (typeof data.translation_mode === 'string' && data.translation_mode.trim()) {
            backendTranslationMode = data.translation_mode.trim().toLowerCase();
        }
        if (typeof data.target_lang_1 === 'string' && data.target_lang_1.trim()) {
            backendTargetLang1 = data.target_lang_1.trim().toLowerCase();
        }
        if (typeof data.target_lang_2 === 'string' && data.target_lang_2.trim()) {
            backendTargetLang2 = data.target_lang_2.trim().toLowerCase();
        }
        if (!uiTranslationMode) {
            uiTranslationMode = backendTranslationMode || 'one_way';
        }
        // Gemini "no translation" is a pure frontend suppression of the translation text.
        suppressTranslationDisplay = (translationProvider === 'gemini' && uiTranslationMode === 'none');
        updateSettingsButtonVisibility();
        const backendSegmentMode = normalizeSegmentMode(data && data.segment_mode);
        const storedSegmentMode = normalizeSegmentMode(localStorage.getItem('segmentMode'));

        // Segment mode priority:
        // 1) LOCK_MANUAL_CONTROLS=true -> always backend value
        // 2) stored browser value (if valid)
        // 3) backend value
        if (lockManualControls) {
            if (backendSegmentMode) {
                segmentMode = backendSegmentMode;
                localStorage.setItem('segmentMode', segmentMode);
            }
        } else if (storedSegmentMode) {
            segmentMode = storedSegmentMode;
            if (backendSegmentMode && backendSegmentMode !== storedSegmentMode) {
                void setSegmentMode(storedSegmentMode);
            }
        } else if (backendSegmentMode) {
            segmentMode = backendSegmentMode;
            localStorage.setItem('segmentMode', segmentMode);
        }
        updateSegmentModeButton();
        renderRuntimeSettingsPickers();

        if (data && typeof data.speaker_diarization_enabled === 'boolean') {
            speakerDiarizationEnabled = data.speaker_diarization_enabled;
        }
        if (data && typeof data.hide_speaker_labels === 'boolean') {
            hideSpeakerLabels = data.hide_speaker_labels;
        }
        if (data && typeof data.enable_chroma_theme === 'boolean') {
            const wasEnabled = enableChromaTheme;
            enableChromaTheme = data.enable_chroma_theme;

            if (enableChromaTheme && !wasEnabled) {
                // Config just turned on — restore saved theme if it was chroma
                const savedTheme = localStorage.getItem('theme');
                if (savedTheme === 'chroma' && currentTheme !== 'chroma') {
                    applyTheme('chroma');
                }
                void syncWindowOnTopByTheme(currentTheme);
            } else if (!enableChromaTheme && wasEnabled) {
                // Config just turned off — fall back if on chroma
                if (currentTheme === 'chroma') {
                    applyTheme('dark');
                }
            } else if (enableChromaTheme) {
                // Config stayed on, but initial applyTheme() ran before we knew the config
                void syncWindowOnTopByTheme(currentTheme);
            }
        }
        applySpeakerLabelVisibility();
        applyLockPauseRestartControlsUI();
        updateTranslationRefineButton();
        enforceTranslateSegmentMode();
    } catch (error) {
        console.error('Error fetching UI config:', error);
    }
}

async function fetchLlmRefineStatus() {
    // LLM 环境变量缺失时，忽略前端记忆的 LLM 模式，直接禁用 LLM。
    // 不读取也不修改保存的 LLM 翻译模式（localStorage）。
    if (!llmRefineAvailable) {
        llmRefineMode = 'off';
        llmRefineEnabled = false;
        llmTranslateHideAfterSequence = null;
        updateTranslationRefineButton();
        enforceTranslateSegmentMode();
        return;
    }

    try {
        const response = await fetch('/llm-refine');
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        if (!data) {
            return;
        }

        const serverMode = normalizeLlmRefineMode(
            typeof data.mode === 'string'
                ? data.mode
                : (data.enabled ? 'refine' : 'off')
        );

        const preferredDefault = normalizeLlmRefineMode(defaultLlmRefineMode || serverMode);

        const storedMode = getStoredLlmRefineMode();
        const hasStoredMode = !!storedMode;

        if (lockManualControls) {
            applyLlmRefineMode(preferredDefault, { persist: false });
            return;
        }

        if (hasStoredMode && storedMode) {
            if (storedMode !== serverMode) {
                const appliedMode = await setLlmRefineMode(storedMode);
                if (!appliedMode) {
                    applyLlmRefineMode(serverMode, { persist: false });
                }
            } else {
                applyLlmRefineMode(storedMode);
            }
            return;
        }

        if (preferredDefault !== serverMode) {
            const appliedMode = await setLlmRefineMode(preferredDefault);
            if (!appliedMode) {
                applyLlmRefineMode(serverMode, { persist: false });
            }
        } else {
            applyLlmRefineMode(preferredDefault);
        }
    } catch (error) {
        console.error('Error fetching LLM refine status:', error);
    }
}

function containsCjkOrJapanese(text) {
    // Han (CJK ideographs), Hiragana, Katakana.
    // We intentionally do NOT include Hangul here; Korean generally benefits from word-level diff.
    const value = (text || '').toString();
    return /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value);
}

function shouldUseCharDiff(original, refined) {
    return containsCjkOrJapanese(original) || containsCjkOrJapanese(refined);
}

function tokenizeForDiff(text, mode) {
    // Tokenize for alignment while ignoring whitespace differences.
    // mode: 'char' | 'word'
    const value = (text || '').toString();
    const out = [];

    if (mode === 'char') {
        for (let idx = 0; idx < value.length; idx++) {
            const ch = value[idx];
            if (/\s/.test(ch)) {
                continue;
            }
            out.push({ text: ch, start: idx, end: idx + 1 });
        }
        return out;
    }

    // Word-level tokenization for non-CJK languages.
    // We align on words; punctuation becomes its own token.
    // Uses Unicode character properties (modern Chromium / modern browsers).
    const wordRe = /[\p{L}\p{N}]+(?:[’'\-][\p{L}\p{N}]+)*/gu;
    let idx = 0;
    while (idx < value.length) {
        const ch = value[idx];
        if (/\s/.test(ch)) {
            idx++;
            continue;
        }

        wordRe.lastIndex = idx;
        const m = wordRe.exec(value);
        if (m && m.index === idx) {
            const w = m[0] || '';
            out.push({ text: w, start: idx, end: idx + w.length });
            idx += w.length;
            continue;
        }

        // Punctuation / symbol as a single-character token.
        out.push({ text: ch, start: idx, end: idx + 1 });
        idx++;
    }

    return out;
}

function renderTranslationDiffHtml(original, refined) {
    const a = (original || '').toString();
    const b = (refined || '').toString();

    // Guardrails: LCS is O(n*m). Keep it safe.
    if (a.length > 12000 || b.length > 12000) {
        return escapeHtml(b);
    }

    const mode = shouldUseCharDiff(a, b) ? 'char' : 'word';
    const A = tokenizeForDiff(a, mode);
    const B = tokenizeForDiff(b, mode);

    const n = A.length;
    const m = B.length;
    if (n === 0 && m === 0) {
        return escapeHtml(b);
    }

    // If this would be too expensive, skip highlighting.
    if (n * m > 400000) {
        return escapeHtml(b);
    }

    // LCS DP table (typed arrays for lower overhead).
    const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = 1; i <= n; i++) {
        const ai = A[i - 1].text;
        const row = dp[i];
        const prevRow = dp[i - 1];
        for (let j = 1; j <= m; j++) {
            if (ai === B[j - 1].text) {
                row[j] = prevRow[j - 1] + 1;
            } else {
                const up = prevRow[j];
                const left = row[j - 1];
                row[j] = up >= left ? up : left;
            }
        }
    }

    const ops = [];
    let i = n;
    let j = m;
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && A[i - 1].text === B[j - 1].text) {
            ops.push({ type: 'eq', start: B[j - 1].start, end: B[j - 1].end });
            i--;
            j--;
        } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
            ops.push({ type: 'ins', start: B[j - 1].start, end: B[j - 1].end });
            j--;
        } else {
            ops.push({ type: 'del', text: A[i - 1].text });
            i--;
        }
    }
    ops.reverse();

    const parts = [];
    const showDeletions = !!llmRefineShowDeletions;
    const pushDel = (text) => {
        if (!showDeletions) return;
        if (!text) return;
        parts.push(`<span class="llm-diff-del">${escapeHtml(text)}</span>`);
    };
    const pushIns = (text) => {
        if (!text) return;
        parts.push(`<span class="llm-diff-ins">${escapeHtml(text)}</span>`);
    };

    let refinedPos = 0;
    let delBuffer = '';
    let insBuffer = '';

    const isWordChar = (s) => {
        if (!s) return false;
        try {
            return /[\p{L}\p{N}]/u.test(s);
        } catch (e) {
            return /[A-Za-z0-9]/.test(s);
        }
    };

    const appendDeletedToken = (tokenText) => {
        if (!showDeletions) {
            return;
        }
        const t = (tokenText || '').toString();
        if (!t) return;
        if (mode === 'word' && delBuffer) {
            const last = delBuffer[delBuffer.length - 1];
            const first = t[0];
            if (isWordChar(last) && isWordChar(first)) {
                delBuffer += ' ';
            }
        }
        delBuffer += t;
    };

    const flushDel = () => {
        if (!showDeletions) {
            delBuffer = '';
            return;
        }
        if (delBuffer) {
            pushDel(delBuffer);
            delBuffer = '';
        }
    };
    const flushIns = () => {
        if (insBuffer) {
            pushIns(insBuffer);
            insBuffer = '';
        }
    };

    for (const op of ops) {
        if (op.type !== 'ins') {
            flushIns();
        }
        if (op.type !== 'del') {
            flushDel();
        }

        if (op.type === 'del') {
            // Deleted non-whitespace characters are shown in red with strikethrough.
            if (showDeletions) {
                appendDeletedToken(op.text);
            }
            continue;
        }

        const start = op.start;
        const end = op.end;
        if (typeof start !== 'number' || typeof end !== 'number' || start < 0 || end < start || end > b.length) {
            continue;
        }

        // Important: when we ignore whitespace in the alignment, two consecutive non-whitespace insertions
        // may still be separated by whitespace in the refined string (e.g. inserted multi-word phrase).
        // If we buffer insertions across that gap, we'd output the whitespace *before* the buffered letters,
        // which breaks languages that use spaces (English, etc.).
        if (op.type === 'ins' && start > refinedPos) {
            flushIns();
        }

        // Always output refined whitespace (and any other chars between aligned non-ws chars) as plain.
        if (start > refinedPos) {
            parts.push(escapeHtml(b.slice(refinedPos, start)));
        }

        const tokenText = b.slice(start, end);
        if (op.type === 'eq') {
            parts.push(escapeHtml(tokenText));
        } else if (op.type === 'ins') {
            // Inserted non-whitespace characters are shown in green.
            insBuffer += tokenText;
        }

        refinedPos = end;
    }

    flushIns();
    flushDel();

    if (refinedPos < b.length) {
        parts.push(escapeHtml(b.slice(refinedPos)));
    }

    return parts.join('');
}

function getDisplayTranslation(source, originalTranslation) {
    const key = `${source}||${originalTranslation}`;
    const refined = backendRefinedResults.get(key);
    return refined || originalTranslation;
}

function getLlmSentenceId(sentence) {
    const tokens = [
        ...(sentence && Array.isArray(sentence.originalTokens) ? sentence.originalTokens : []),
        ...(sentence && Array.isArray(sentence.translationTokens) ? sentence.translationTokens : [])
    ];
    for (const token of tokens) {
        if (token && token.llm_sentence_id) {
            return String(token.llm_sentence_id);
        }
    }
    return null;
}

function getDisplayTranslationForSentence(sentence, source, originalTranslation) {
    const sentenceId = getLlmSentenceId(sentence);
    if (sentenceId && backendRefinedResultsBySentenceId.has(sentenceId)) {
        return backendRefinedResultsBySentenceId.get(sentenceId) || originalTranslation;
    }
    return getDisplayTranslation(source, originalTranslation);
}

function shouldHideSonioxTranslation(sentence, sourceText, hasRefined) {
    if (!isLlmTranslateMode()) {
        return false;
    }
    if (!sourceText) {
        return false;
    }
    if (hasRefined) {
        return false;
    }
    if (llmTranslateHideAfterSequence === null) {
        return false;
    }
    if (!sentence || !Array.isArray(sentence.translationTokens)) {
        return false;
    }
    return sentence.translationTokens.some((token) => {
        const seq = token && typeof token._sequenceIndex === 'number' ? token._sequenceIndex : null;
        return seq !== null && seq >= llmTranslateHideAfterSequence;
    });
}

function handleBackendRefineResult(data) {
    if (!data) {
        return;
    }
    const source = (data.source || '').toString().trim();
    const originalTranslation = (data.original_translation || '').toString().trim();
    const refinedTranslation = (data.refined_translation || '').toString().trim();
    const sentenceId = data.sentence_id ? String(data.sentence_id).trim() : '';
    const noChange = !!data.no_change;

    if (!source || !originalTranslation) {
        return;
    }

    if (!noChange && refinedTranslation) {
        const key = `${source}||${originalTranslation}`;
        backendRefinedResults.set(key, refinedTranslation);
        if (sentenceId) {
            backendRefinedResultsBySentenceId.set(sentenceId, refinedTranslation);
        }
        if (isLlmTranslateMode()) {
            llmTranslationOverrides.set(key, refinedTranslation);
            if (sentenceId) {
                llmTranslationOverridesBySentenceId.set(sentenceId, refinedTranslation);
            }
        }
    }
    renderSubtitles();
}

function handleSegmentModeChanged(data) {
    if (!data || typeof data.mode !== 'string') {
        return;
    }
    segmentMode = data.mode;
    localStorage.setItem('segmentMode', data.mode);
    updateSegmentModeButton();
    renderSegmentModePicker();
    enforceTranslateSegmentMode();
}

function setUiTranslationMode(mode, { persistOnly = false } = {}) {
    if (!['none', 'one_way', 'two_way'].includes(mode)) {
        return;
    }
    uiTranslationMode = mode;
    try {
        localStorage.setItem(UI_TRANSLATION_MODE_STORAGE_KEY, mode);
    } catch (e) {
        // ignore
    }
    if (persistOnly) {
        return;
    }
    suppressTranslationDisplay = (translationProvider === 'gemini' && mode === 'none');
}

function getLanguageDisplayName(code) {
    const normalized = (code || '').toString().trim().toLowerCase();
    const info = SUPPORTED_TRANSLATION_LANGUAGES.find((lang) => lang.code.toLowerCase() === normalized);
    return info ? `${info.en} - ${info.native}` : (code || '').toString();
}

function ensureLangSelectMenu() {
    if (langSelectMenuEl) {
        return langSelectMenuEl;
    }
    const menu = document.createElement('div');
    menu.className = 'lang-select-menu';
    menu.hidden = true;
    menu.setAttribute('role', 'listbox');
    document.body.appendChild(menu);
    langSelectMenuEl = menu;
    return menu;
}

function setLangPickerValue(picker, code) {
    if (!picker) {
        return;
    }
    const selected = coerceSupportedLanguageCode(code, picker.dataset.fallback || 'en');
    picker.dataset.value = selected;
    const label = picker.querySelector('.lang-picker-label');
    if (label) {
        label.textContent = getLanguageDisplayName(selected);
    }
}

function positionLangSelectMenu(picker, menu) {
    const trigger = picker.querySelector('.lang-picker-button') || picker;
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const viewportPadding = 8;
    const menuWidth = Math.max(220, Math.round(rect.width));

    menu.style.maxHeight = '';
    const naturalHeight = menu.offsetHeight;

    const maxHeight = Math.min(260, Math.max(160, window.innerHeight - 2 * viewportPadding));
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding;
    const spaceAbove = rect.top - viewportPadding;
    const openUp = spaceBelow < 170 && spaceAbove > spaceBelow;
    const menuHeight = Math.min(maxHeight, openUp ? Math.max(120, spaceAbove - gap) : Math.max(120, spaceBelow - gap));
    const actualHeight = Math.min(menuHeight, naturalHeight);

    const left = Math.min(
        Math.max(viewportPadding, rect.left),
        Math.max(viewportPadding, window.innerWidth - viewportPadding - menuWidth)
    );
    const top = openUp
        ? Math.max(viewportPadding, rect.top - gap - actualHeight)
        : Math.min(window.innerHeight - viewportPadding - actualHeight, rect.bottom + gap);

    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
    menu.style.width = `${Math.round(menuWidth)}px`;
    menu.style.maxHeight = `${Math.round(menuHeight)}px`;
}

function openLangSelectMenu(picker) {
    const menu = ensureLangSelectMenu();
    activeLangPickerEl = picker;
    menu.innerHTML = '';

    for (const lang of SUPPORTED_TRANSLATION_LANGUAGES) {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'lang-select-option';
        option.dataset.code = lang.code;
        option.setAttribute('role', 'option');
        option.textContent = `${lang.en} - ${lang.native}`;
        const isSelected = lang.code === picker.value;
        option.classList.toggle('selected', isSelected);
        option.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        option.addEventListener('click', () => {
            setLangPickerValue(picker, lang.code);
            closeLangSelectMenu();
            picker.dispatchEvent(new Event('change'));
        });
        menu.appendChild(option);
    }

    picker.classList.add('open');
    const trigger = picker.querySelector('.lang-picker-button');
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'true');
    }
    menu.hidden = false;
    positionLangSelectMenu(picker, menu);

    const selectedOption = menu.querySelector('.lang-select-option.selected');
    if (selectedOption) {
        selectedOption.scrollIntoView({ block: 'nearest' });
    }
}

function closeLangSelectMenu() {
    if (activeLangPickerEl) {
        activeLangPickerEl.classList.remove('open');
        const trigger = activeLangPickerEl.querySelector('.lang-picker-button');
        if (trigger) {
            trigger.setAttribute('aria-expanded', 'false');
        }
    }
    activeLangPickerEl = null;
    if (langSelectMenuEl) {
        langSelectMenuEl.hidden = true;
        langSelectMenuEl.innerHTML = '';
    }
}

function buildLangPicker(selectedCode, fallbackCode = 'en') {
    const picker = document.createElement('div');
    picker.className = 'lang-picker';
    picker.dataset.fallback = fallbackCode;
    Object.defineProperty(picker, 'value', {
        get() {
            return picker.dataset.value || '';
        },
        set(value) {
            setLangPickerValue(picker, value);
        },
    });

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'lang-picker-button';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const label = document.createElement('span');
    label.className = 'lang-picker-label';
    const chevron = document.createElement('span');
    chevron.className = 'lang-picker-chevron';
    chevron.setAttribute('aria-hidden', 'true');

    trigger.appendChild(label);
    trigger.appendChild(chevron);
    trigger.addEventListener('click', () => {
        if (activeLangPickerEl === picker && langSelectMenuEl && !langSelectMenuEl.hidden) {
            closeLangSelectMenu();
            return;
        }
        closeLangSelectMenu();
        openLangSelectMenu(picker);
    });

    picker.appendChild(trigger);
    setLangPickerValue(picker, selectedCode);
    return picker;
}

// Position a fixed-position dropdown menu relative to its trigger, flipping up
// when there isn't enough room below. Shared by the generic custom select.
function positionDropdownMenu(trigger, menu) {
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const viewportPadding = 8;
    const menuWidth = Math.max(rect.width, 180);

    menu.style.maxHeight = '';
    const naturalHeight = menu.offsetHeight;

    const maxHeight = Math.min(260, Math.max(160, window.innerHeight - 2 * viewportPadding));
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding;
    const spaceAbove = rect.top - viewportPadding;
    const openUp = spaceBelow < 170 && spaceAbove > spaceBelow;
    const menuHeight = Math.min(maxHeight, openUp ? Math.max(120, spaceAbove - gap) : Math.max(120, spaceBelow - gap));
    const actualHeight = Math.min(menuHeight, naturalHeight);

    const left = Math.min(
        Math.max(viewportPadding, rect.left),
        Math.max(viewportPadding, window.innerWidth - viewportPadding - menuWidth)
    );
    const top = openUp
        ? Math.max(viewportPadding, rect.top - gap - actualHeight)
        : Math.min(window.innerHeight - viewportPadding - actualHeight, rect.bottom + gap);

    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
    menu.style.width = `${Math.round(menuWidth)}px`;
    menu.style.maxHeight = `${Math.round(menuHeight)}px`;
}

// Generic dark-themed custom <select> replacement. Reuses the language picker's
// CSS classes for visual consistency. `options` is [{ value, label }].
function buildCustomSelect(options, { value = null, onChange = null, disabled = false } = {}) {
    const picker = document.createElement('div');
    picker.className = 'lang-picker';
    let currentValue = value;
    let menuEl = null;

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'lang-picker-button';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    if (disabled) {
        trigger.disabled = true;
        picker.classList.add('disabled');
    }

    const label = document.createElement('span');
    label.className = 'lang-picker-label';
    const chevron = document.createElement('span');
    chevron.className = 'lang-picker-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    trigger.appendChild(label);
    trigger.appendChild(chevron);
    picker.appendChild(trigger);

    const labelFor = (val) => {
        const opt = options.find((o) => o.value === val);
        return opt ? opt.label : '';
    };
    const renderLabel = () => {
        label.textContent = labelFor(currentValue);
    };

    const onScroll = (event) => {
        if (menuEl && event && event.target && menuEl.contains(event.target)) {
            return;
        }
        close();
    };
    const close = () => {
        if (!menuEl) {
            return;
        }
        menuEl.remove();
        menuEl = null;
        picker.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('mousedown', onDocMouseDown, true);
        document.removeEventListener('keydown', onKeyDown, true);
        window.removeEventListener('resize', reposition, true);
        window.removeEventListener('scroll', onScroll, true);
    };
    const onDocMouseDown = (event) => {
        if (picker.contains(event.target) || (menuEl && menuEl.contains(event.target))) {
            return;
        }
        close();
    };
    const onKeyDown = (event) => {
        if (event.key === 'Escape') {
            close();
        }
    };
    const reposition = () => {
        if (menuEl) {
            positionDropdownMenu(trigger, menuEl);
        }
    };
    const open = () => {
        menuEl = document.createElement('div');
        menuEl.className = 'lang-select-menu';
        menuEl.setAttribute('role', 'listbox');
        for (const opt of options) {
            const optionEl = document.createElement('button');
            optionEl.type = 'button';
            optionEl.className = 'lang-select-option';
            optionEl.setAttribute('role', 'option');
            optionEl.textContent = opt.label;
            const isSelected = opt.value === currentValue;
            optionEl.classList.toggle('selected', isSelected);
            optionEl.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            optionEl.addEventListener('click', () => {
                const changed = currentValue !== opt.value;
                currentValue = opt.value;
                renderLabel();
                close();
                if (changed && typeof onChange === 'function') {
                    onChange(currentValue);
                }
            });
            menuEl.appendChild(optionEl);
        }
        document.body.appendChild(menuEl);
        picker.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
        positionDropdownMenu(trigger, menuEl);
        const selectedOption = menuEl.querySelector('.lang-select-option.selected');
        if (selectedOption) {
            selectedOption.scrollIntoView({ block: 'nearest' });
        }
        document.addEventListener('mousedown', onDocMouseDown, true);
        document.addEventListener('keydown', onKeyDown, true);
        window.addEventListener('resize', reposition, true);
        window.addEventListener('scroll', onScroll, true);
    };

    trigger.addEventListener('click', () => {
        if (menuEl) {
            close();
        } else {
            open();
        }
    });

    Object.defineProperty(picker, 'value', {
        get() {
            return currentValue;
        },
        set(val) {
            currentValue = val;
            renderLabel();
        },
    });

    renderLabel();
    return picker;
}

function normalizeTranslationMode(mode) {
    const value = (mode || '').toString().trim().toLowerCase();
    return ['none', 'one_way', 'two_way'].includes(value) ? value : 'one_way';
}

function getFirstSupportedLanguageCode() {
    const first = SUPPORTED_TRANSLATION_LANGUAGES[0];
    return first && first.code ? first.code : 'en';
}

function coerceSupportedLanguageCode(code, fallback = 'en') {
    const desired = (code || '').toString().trim().toLowerCase();
    const fallbackCode = (fallback || '').toString().trim().toLowerCase();
    const languages = SUPPORTED_TRANSLATION_LANGUAGES || [];
    const desiredMatch = languages.find((lang) => lang.code.toLowerCase() === desired);
    if (desiredMatch) {
        return desiredMatch.code;
    }
    const fallbackMatch = languages.find((lang) => lang.code.toLowerCase() === fallbackCode);
    if (fallbackMatch) {
        return fallbackMatch.code;
    }
    return getFirstSupportedLanguageCode();
}

function createLangPopoverDraft() {
    let mode = normalizeTranslationMode(uiTranslationMode || backendTranslationMode || 'one_way');
    if (mode === 'two_way' && !twoWaySupported) {
        mode = 'one_way';
    }
    return {
        mode,
        targetLang: coerceSupportedLanguageCode(currentTranslationTargetLang, defaultTranslationTargetLang),
        targetLang1: coerceSupportedLanguageCode(backendTargetLang1, 'en'),
        targetLang2: coerceSupportedLanguageCode(backendTargetLang2, 'zh'),
    };
}

function getLangPopoverDraft() {
    if (!langPopoverDraft) {
        langPopoverDraft = createLangPopoverDraft();
    }
    return langPopoverDraft;
}

function onSelectTranslationMode(mode) {
    if (mode === 'two_way' && !twoWaySupported) {
        return;
    }
    const draft = getLangPopoverDraft();
    draft.mode = normalizeTranslationMode(mode);
    updateLangPopoverSelection();
    refreshLangPopoverSections();
}

function refreshLangPopoverSections() {
    if (!langPopoverEl) {
        return;
    }
    const modeControl = langPopoverEl.querySelector('.translation-mode-control');
    const onewayBox = langPopoverEl.querySelector('.lang-popover-oneway');
    const twowayBox = langPopoverEl.querySelector('.lang-popover-twoway');
    const draft = getLangPopoverDraft();
    if (modeControl) {
        const twoWayOption = modeControl.querySelector('[data-mode="two_way"]');
        if (twoWayOption) {
            twoWayOption.hidden = !twoWaySupported;
        }
    }
    const effectiveMode = (draft.mode === 'two_way' && !twoWaySupported) ? 'one_way' : draft.mode;
    if (onewayBox) {
        onewayBox.hidden = (effectiveMode !== 'one_way');
    }
    if (twowayBox) {
        twowayBox.hidden = (effectiveMode !== 'two_way');
    }
    const targetSelect = langPopoverEl.querySelector('.lang-picker[data-role="target"]');
    if (targetSelect) {
        targetSelect.value = coerceSupportedLanguageCode(draft.targetLang, defaultTranslationTargetLang);
    }
    const selA = langPopoverEl.querySelector('.lang-picker[data-role="langA"]');
    if (selA) {
        selA.value = coerceSupportedLanguageCode(draft.targetLang1, 'en');
    }
    const selB = langPopoverEl.querySelector('.lang-picker[data-role="langB"]');
    if (selB) {
        selB.value = coerceSupportedLanguageCode(draft.targetLang2, 'zh');
    }
}

function ensureLangPopover() {
    if (langPopoverEl) {
        return langPopoverEl;
    }

    const el = document.createElement('div');
    el.className = 'lang-popover';
    el.style.display = 'none';

    const title = document.createElement('h2');
    title.className = 'lang-popover-title';
    title.textContent = t('translation_panel_title');
    el.appendChild(title);

    // Translation-mode selector (none / one-way / two-way).
    const modeControl = document.createElement('div');
    modeControl.className = 'segmented-control translation-mode-control';
    const modeDefs = [
        ['none', 'translate_mode_none'],
        ['one_way', 'translate_mode_one_way'],
        ['two_way', 'translate_mode_two_way'],
    ];
    for (const [mode, labelKey] of modeDefs) {
        const opt = document.createElement('button');
        opt.type = 'button';
        opt.className = 'segmented-option';
        opt.dataset.mode = mode;
        const span = document.createElement('span');
        span.textContent = t(labelKey);
        opt.appendChild(span);
        opt.addEventListener('click', () => onSelectTranslationMode(mode));
        modeControl.appendChild(opt);
    }
    el.appendChild(modeControl);

    // One-way: single target-language select.
    const onewayBox = document.createElement('div');
    onewayBox.className = 'lang-popover-oneway';
    const targetField = document.createElement('label');
    targetField.className = 'lang-twoway-field';
    const targetLabel = document.createElement('span');
    targetLabel.textContent = t('target_language');
    const targetSelect = buildLangPicker(getLangPopoverDraft().targetLang, defaultTranslationTargetLang);
    targetSelect.dataset.role = 'target';
    targetSelect.addEventListener('change', () => {
        const draft = getLangPopoverDraft();
        draft.targetLang = targetSelect.value;
    });
    targetField.appendChild(targetLabel);
    targetField.appendChild(targetSelect);
    onewayBox.appendChild(targetField);
    el.appendChild(onewayBox);

    // Two-way (Soniox only): pick language A and B.
    const twowayBox = document.createElement('div');
    twowayBox.className = 'lang-popover-twoway';

    const fieldA = document.createElement('label');
    fieldA.className = 'lang-twoway-field';
    const labelA = document.createElement('span');
    labelA.textContent = t('language_a');
    const selA = buildLangPicker(getLangPopoverDraft().targetLang1, 'en');
    selA.dataset.role = 'langA';
    selA.addEventListener('change', () => {
        const draft = getLangPopoverDraft();
        draft.targetLang1 = selA.value;
    });
    fieldA.appendChild(labelA);
    fieldA.appendChild(selA);

    const fieldB = document.createElement('label');
    fieldB.className = 'lang-twoway-field';
    const labelB = document.createElement('span');
    labelB.textContent = t('language_b');
    const selB = buildLangPicker(getLangPopoverDraft().targetLang2, 'zh');
    selB.dataset.role = 'langB';
    selB.addEventListener('change', () => {
        const draft = getLangPopoverDraft();
        draft.targetLang2 = selB.value;
    });
    fieldB.appendChild(labelB);
    fieldB.appendChild(selB);

    const actions = document.createElement('div');
    actions.className = 'lang-popover-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'secondary-button';
    cancelBtn.textContent = t('cancel');
    cancelBtn.addEventListener('click', hideLangPopover);
    const applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'primary-button';
    applyBtn.textContent = t('apply');
    applyBtn.addEventListener('click', applyLangPopoverDraft);
    actions.appendChild(cancelBtn);
    actions.appendChild(applyBtn);

    twowayBox.appendChild(fieldA);
    twowayBox.appendChild(fieldB);
    el.appendChild(twowayBox);
    el.appendChild(actions);

    document.body.appendChild(el);
    langPopoverEl = el;
    return el;
}

function updateLangPopoverSelection() {
    if (!langPopoverEl) {
        return;
    }
    const draft = getLangPopoverDraft();
    const modeButtons = langPopoverEl.querySelectorAll('.translation-mode-control .segmented-option');
    modeButtons.forEach((btn) => {
        const checked = btn.dataset.mode === draft.mode;
        btn.classList.toggle('selected', checked);
        btn.setAttribute('aria-pressed', checked ? 'true' : 'false');
    });
    refreshLangPopoverSections();
}

function applyLangPopoverDraft() {
    const draft = getLangPopoverDraft();
    const nextMode = (draft.mode === 'two_way' && !twoWaySupported) ? 'one_way' : draft.mode;

    if (nextMode === 'none') {
        const modeChanged = uiTranslationMode !== 'none' || suppressTranslationDisplay;
        setUiTranslationMode('none');
        hideLangPopover();
        if (translationProvider === 'gemini') {
            renderSubtitles();
        } else if (modeChanged) {
            void restartRecognition({ translationMode: 'none' });
        }
        return;
    }

    if (nextMode === 'two_way') {
        const a = coerceSupportedLanguageCode(draft.targetLang1, 'en');
        const b = coerceSupportedLanguageCode(draft.targetLang2, 'zh');
        if (!a || !b || a === b) {
            return;
        }
        const changed = uiTranslationMode !== 'two_way' || suppressTranslationDisplay || a !== backendTargetLang1 || b !== backendTargetLang2;
        backendTargetLang1 = a;
        backendTargetLang2 = b;
        setUiTranslationMode('two_way');
        hideLangPopover();
        if (changed) {
            void restartRecognition({ translationMode: 'two_way', targetLang1: a, targetLang2: b });
        }
        return;
    }

    const selected = coerceSupportedLanguageCode(draft.targetLang, defaultTranslationTargetLang);
    const changed = uiTranslationMode !== 'one_way' || suppressTranslationDisplay || selected !== currentTranslationTargetLang;
    currentTranslationTargetLang = selected;
    setUiTranslationMode('one_way');
    hideLangPopover();
    if (changed) {
        void restartRecognition({ translationMode: 'one_way', targetLang: selected });
    }
}

function showLangPopover() {
    if (!translationLangButton) {
        return;
    }
    const el = ensureLangPopover();
    langPopoverDraft = createLangPopoverDraft();
    updateLangPopoverSelection();

    el.style.display = 'block';
    langPopoverOpen = true;

    const onDocMouseDown = (event) => {
        const target = event.target;
        if (!target) {
            return;
        }
        if (langPopoverEl && langPopoverEl.contains(target)) {
            return;
        }
        if (langSelectMenuEl && langSelectMenuEl.contains(target)) {
            return;
        }
        if (translationLangButton && translationLangButton.contains(target)) {
            return;
        }
        hideLangPopover();
    };

    const onKeyDown = (event) => {
        if (event.key === 'Escape') {
            if (langSelectMenuEl && !langSelectMenuEl.hidden) {
                closeLangSelectMenu();
                return;
            }
            hideLangPopover();
        }
    };

    document.addEventListener('mousedown', onDocMouseDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    langPopoverCleanup = () => {
        document.removeEventListener('mousedown', onDocMouseDown, true);
        document.removeEventListener('keydown', onKeyDown, true);
    };
}

function hideLangPopover() {
    if (!langPopoverOpen) {
        return;
    }
    langPopoverOpen = false;
    if (langPopoverEl) {
        langPopoverEl.style.display = 'none';
    }
    closeLangSelectMenu();
    langPopoverDraft = null;
    if (typeof langPopoverCleanup === 'function') {
        langPopoverCleanup();
    }
    langPopoverCleanup = null;
}

if (translationLangButton) {
    translationLangButton.addEventListener('click', () => {
        if (lockManualControls) {
            return;
        }
        if (langPopoverOpen) {
            hideLangPopover();
        } else {
            showLangPopover();
        }
    });
}

if (translationRefineButton) {
    translationRefineButton.addEventListener('click', () => {
        if (!llmRefineAvailable) {
            return;
        }
        const nextMode = getNextLlmRefineMode(llmRefineMode);
        void setLlmRefineMode(nextMode);
    });
}

async function setLlmRefineMode(mode) {
    try {
        const response = await fetch('/llm-refine', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode })
        });
        if (response.ok) {
            const data = await response.json();
            const nextMode = normalizeLlmRefineMode(data && data.mode ? data.mode : mode);
            applyLlmRefineMode(nextMode);
            return nextMode;
        } else {
            console.error('Failed to set LLM refine');
        }
    } catch (error) {
        console.error('Error setting LLM refine:', error);
    }
    return null;
}

function updateAudioSourceButton() {
    if (!audioSourceButton || !audioSourceIcon) {
        return;
    }

    audioSource = normalizeAudioSource(audioSource);

    if (audioSource === 'microphone') {
        setControlIcon(audioSourceIcon, 'mic');
        audioSourceButton.title = t('audio_to_mix');
        return;
    }

    if (audioSource === 'mix') {
        setControlIcon(audioSourceIcon, 'blend');
        audioSourceButton.title = t('audio_to_system');
        return;
    }

    setControlIcon(audioSourceIcon, 'volume-2');
    audioSourceButton.title = t('audio_to_mic');
}

async function fetchInitialAudioSource() {
    try {
        const stored = localStorage.getItem('audioSource');
        audioSource = normalizeAudioSource(stored);
        updateAudioSourceButton();
    } catch (storageError) {
        console.warn('Unable to access stored audio source preference:', storageError);
    }

    try {
        const response = await fetch('/audio-source');
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        if (data && typeof data.source === 'string') {
            audioSource = normalizeAudioSource(data.source);
            updateAudioSourceButton();
            try {
                localStorage.setItem('audioSource', audioSource);
            } catch (persistError) {
                console.warn('Unable to persist audio source preference:', persistError);
            }
        }
    } catch (error) {
        console.error('Failed to fetch current audio source:', error);
    }
}

function microphoneDefaultLabel() {
    const defaultName = microphoneDeviceData && microphoneDeviceData.default && microphoneDeviceData.default.name;
    if (defaultName) {
        return t('microphone_device_default', { name: defaultName });
    }
    return t('microphone_device_default_unknown');
}

function getSelectedMicrophoneDeviceId() {
    if (microphoneDevicePickerEl && typeof microphoneDevicePickerEl.value === 'string') {
        return microphoneDevicePickerEl.value;
    }
    return String((microphoneDeviceData && microphoneDeviceData.selected_id) || '');
}

function renderMicrophoneDevicePicker() {
    if (!microphoneDevicePickerHost) {
        return;
    }
    microphoneDevicePickerHost.innerHTML = '';
    const devices = Array.isArray(microphoneDeviceData.devices) ? microphoneDeviceData.devices : [];
    const options = [
        { value: '', label: microphoneDefaultLabel() },
        ...devices.filter((device) => !device.is_default).map((device) => ({
            value: String(device.id || ''),
            label: String(device.name || device.id || ''),
        })).filter((option) => option.value && option.label),
    ];
    const selected = options.some((option) => option.value === microphoneDeviceData.selected_id)
        ? microphoneDeviceData.selected_id
        : '';
    microphoneDevicePickerEl = buildCustomSelect(options, {
        value: selected,
        disabled: !microphoneDeviceData.available,
    });
    microphoneDevicePickerHost.appendChild(microphoneDevicePickerEl);
    if (microphoneDeviceHint) {
        microphoneDeviceHint.textContent = microphoneDeviceData.available
            ? t('microphone_device_hint')
            : t('microphone_device_unavailable');
    }
}

async function fetchMicrophoneDevices() {
    if (!microphoneDeviceSection) {
        return;
    }
    try {
        const response = await fetch('/microphones');
        if (!response.ok) {
            microphoneDeviceData = { available: false, default: null, devices: [], selected_id: '' };
            renderMicrophoneDevicePicker();
            return;
        }
        const data = await response.json();
        microphoneDeviceData = {
            available: !!(data && data.available),
            default: (data && data.default) || null,
            devices: Array.isArray(data && data.devices) ? data.devices : [],
            selected_id: String((data && data.selected_id) || ''),
        };
        renderMicrophoneDevicePicker();
    } catch (error) {
        console.error('Failed to fetch microphone devices:', error);
        microphoneDeviceData = { available: false, default: null, devices: [], selected_id: '' };
        renderMicrophoneDevicePicker();
    }
}

async function saveMicrophoneDeviceSelection() {
    if (!microphoneDeviceSection || !microphoneDeviceData.available) {
        return { ok: true };
    }
    try {
        const response = await fetch('/microphone-device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: getSelectedMicrophoneDeviceId() }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            return {
                ok: false,
                message: localizeBackendMessage(data && data.message) || `HTTP ${response.status}`,
            };
        }
        microphoneDeviceData.selected_id = String((data && data.id) || getSelectedMicrophoneDeviceId() || '');
        return { ok: true };
    } catch (error) {
        return { ok: false, message: String(error) };
    }
}

function segmentModeLabel(mode) {
    if (mode === 'translation') {
        return t('segment_mode_translation');
    }
    if (mode === 'endpoint') {
        return t('segment_mode_endpoint');
    }
    return t('segment_mode_punctuation');
}

function renderAutoRestartPicker() {
    if (!autoRestartPickerHost) {
        return;
    }
    autoRestartPickerHost.innerHTML = '';
    autoRestartPickerEl = buildCustomSelect([
        { value: 'true', label: t('auto_restart_enabled') },
        { value: 'false', label: t('auto_restart_disabled') },
    ], {
        value: autoRestartEnabled ? 'true' : 'false',
    });
    autoRestartPickerHost.appendChild(autoRestartPickerEl);
}

function renderSegmentModePicker() {
    if (segmentModeSettingField) {
        segmentModeSettingField.hidden = !segmentModeSupported;
    }
    if (!segmentModePickerHost) {
        return;
    }
    segmentModePickerHost.innerHTML = '';
    if (!segmentModeSupported) {
        segmentModePickerEl = null;
        return;
    }
    const availableModes = getSegmentModes();
    const selected = availableModes.includes(segmentMode) ? segmentMode : availableModes[0];
    segmentModePickerEl = buildCustomSelect(availableModes.map((mode) => ({
        value: mode,
        label: segmentModeLabel(mode),
    })), {
        value: selected,
    });
    segmentModePickerHost.appendChild(segmentModePickerEl);
}

function renderRuntimeSettingsPickers() {
    if (runtimeControlsSection) {
        runtimeControlsSection.hidden = false;
    }
    renderAutoRestartPicker();
    renderSegmentModePicker();
}

async function applyRuntimeControlSettings() {
    if (autoRestartPickerEl && typeof autoRestartPickerEl.value === 'string') {
        autoRestartEnabled = autoRestartPickerEl.value !== 'false';
        localStorage.setItem('autoRestartEnabled', autoRestartEnabled ? 'true' : 'false');
        updateAutoRestartButton();
    }

    const requestedSegmentMode = normalizeSegmentMode(segmentModePickerEl && segmentModePickerEl.value);
    if (requestedSegmentMode && requestedSegmentMode !== segmentMode) {
        const ok = await setSegmentMode(requestedSegmentMode);
        if (!ok) {
            return { ok: false, message: t('backend_segment_mode_disabled') };
        }
    }

    return { ok: true };
}

async function setSegmentMode(mode) {
    if (lockManualControls) {
        return false;
    }
    if (!segmentModeSupported) {
        return false;
    }
    if (isLlmTranslateMode() && mode === 'translation') {
        return false;
    }
    try {
        const response = await fetch('/segment-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode })
        });
        if (!response.ok) {
            console.error('Failed to set segment mode');
            return false;
        }
        segmentMode = mode;
        localStorage.setItem('segmentMode', mode);
        updateSegmentModeButton();
        renderSegmentModePicker();
        return true;
    } catch (error) {
        console.error('Error setting segment mode:', error);
        return false;
    }
}

// 分段模式切换
if (segmentModeButton) {
    segmentModeButton.addEventListener('click', () => {
        const availableModes = getSegmentModes();
        const currentIndex = availableModes.indexOf(segmentMode);
        const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % availableModes.length : 0;
        const nextMode = availableModes[nextIndex];
        void setSegmentMode(nextMode);
    });
}

// 显示模式切换
displayModeButton.addEventListener('click', () => {
    if (displayMode === 'both') {
        displayMode = 'original';
    } else if (displayMode === 'original') {
        displayMode = 'translation';
    } else {
        displayMode = 'both';
    }
    localStorage.setItem('displayMode', displayMode);
    updateDisplayModeButton();
    renderSubtitles();  // 立即重新渲染
    console.log(`Display mode switched to: ${displayMode}`);
});

if (oscTranslationButton) {
    oscTranslationButton.addEventListener('click', async () => {
        const next = !oscTranslationEnabled;
        try {
            const response = await fetch('/osc-translation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: next })
            });

            let data = null;
            try {
                data = await response.json();
            } catch (parseError) {
                console.error('Failed to parse OSC translation toggle response:', parseError);
            }

            if (response.ok && data) {
                oscTranslationEnabled = !!data.enabled;
                updateOscTranslationButton();
                console.log(`OSC translation ${oscTranslationEnabled ? 'enabled' : 'disabled'}`);
            } else {
                console.error('Failed to toggle OSC translation:', response.status, data?.message);
            }
        } catch (error) {
            console.error('Error toggling OSC translation:', error);
        }
    });
}

if (bottomSafeAreaButton) {
    bottomSafeAreaButton.addEventListener('click', () => {
        if (!isMobileBrowser) {
            return;
        }
        bottomSafeAreaEnabled = !bottomSafeAreaEnabled;
        try {
            localStorage.setItem('bottomSafeAreaEnabled', bottomSafeAreaEnabled);
        } catch (persistError) {
            console.warn('Unable to persist bottom safe area preference:', persistError);
        }
        applyBottomSafeArea();
        updateBottomSafeAreaButton();
        console.log(`Mobile bottom safe area ${bottomSafeAreaEnabled ? 'enabled' : 'disabled'}`);
    });
}

if (autoRestartButton) {
    autoRestartButton.addEventListener('click', () => {
        if (lockManualControls) {
            return;
        }
        autoRestartEnabled = !autoRestartEnabled;
        localStorage.setItem('autoRestartEnabled', autoRestartEnabled);
        updateAutoRestartButton();
        console.log(`Auto restart ${autoRestartEnabled ? 'enabled' : 'disabled'}`);
    });
}

// 假名注音开关
function updateFuriganaButton() {
    if (!furiganaButton || !furiganaIcon) {
        return;
    }
    
    if (furiganaEnabled) {
        furiganaButton.classList.add('active');
        furiganaButton.title = t('furigana_on');
    } else {
        furiganaButton.classList.remove('active');
        furiganaButton.title = t('furigana_off');
    }
}

if (furiganaButton) {
    furiganaButton.addEventListener('click', () => {
        furiganaEnabled = !furiganaEnabled;
        try {
            sessionStorage.setItem('furiganaEnabled', furiganaEnabled);
        } catch (persistError) {
            console.warn('Unable to persist furigana preference:', persistError);
        }
        updateFuriganaButton();
        // 清空缓存以便重新渲染
        furiganaCache.clear();
        pendingFuriganaRequests.clear();
        renderedSentences.clear();
        renderSubtitles();
        console.log(`Furigana ${furiganaEnabled ? 'enabled' : 'disabled'}`);
    });
}

async function restartRecognition({ auto = false, targetLang = null, translationMode = null, targetLang1 = null, targetLang2 = null } = {}) {
    if (isRestarting) {
        return false;
    }

    // A manual restart begins a fresh recognition session; reset the
    // this-session cost meter so it counts from zero again.
    if (!auto) {
        sessionCostReset();
    }
    isRestarting = true;
    shouldReconnect = false;

    if (!auto && restartButton) {
        restartButton.classList.add('restarting');
    }

    const manualStatusHtml = `<div style="text-align: center; padding: 40px; color: #6b7280;">${escapeHtml(t('restarting'))}</div>`;
    const manualErrorHtml = `<div style="text-align: center; padding: 40px; color: #ef4444;">${escapeHtml(t('connection_error_try_again'))}</div>`;
    const manualFailureHtml = `<div style="text-align: center; padding: 40px; color: #ef4444;">${escapeHtml(t('restart_failed_try_again'))}</div>`;

    try {
        if (auto) {
            finalizeCurrentNonFinalTokens();
        } else if (ws) {
            console.log('Closing old WebSocket connection...');
            try {
                ws.close();
            } catch (closeError) {
                console.warn('WebSocket close during restart raised an error:', closeError);
            }
            ws = null;
        }

        if (!auto) {
            clearSubtitleState();
            subtitleContainer.innerHTML = manualStatusHtml;
        }

        await delay(500);

        const payload = { auto: !!auto };
        const lang = (targetLang || currentTranslationTargetLang || '').toString().trim().toLowerCase();
        if (lang) {
            payload.target_lang = lang;
        }
        if (translationMode) {
            payload.translation_mode = translationMode;
        }
        if (targetLang1) {
            payload.target_lang_1 = String(targetLang1).trim().toLowerCase();
        }
        if (targetLang2) {
            payload.target_lang_2 = String(targetLang2).trim().toLowerCase();
        }

        const response = await fetch('/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            if (!auto) {
                subtitleContainer.innerHTML = manualFailureHtml;
            }
            throw new Error(`Restart failed with status ${response.status}`);
        }

        const result = await response.json().catch(() => ({}));
        if (!auto) {
            isPaused = false;
            updatePauseButtonUi();
            if (result && result.paused === false) {
                sessionCostResume();
            }
        }
        console.log(auto ? 'Auto restart: new recognition session requested.' : 'Recognition restarted successfully');

        await delay(1500);

        shouldReconnect = true;
        if (!auto || !hasUsableWebSocket()) {
            connect();
        }
        return true;
    } catch (error) {
        console.error(`${auto ? 'Auto restart' : 'Restart'} error:`, error);
        if (!auto) {
            if (subtitleContainer.innerHTML === manualStatusHtml) {
                subtitleContainer.innerHTML = manualErrorHtml;
            }
        }
        shouldReconnect = true;
        return false;
    } finally {
        if (!auto && restartButton) {
            setTimeout(() => restartButton.classList.remove('restarting'), 1500);
        }
        isRestarting = false;
    }
}

function triggerAutoRestart() {
    if (!autoRestartEnabled) {
        return;
    }

    if (isRestarting) {
        console.log('Restart already in progress; skipping auto restart trigger.');
        return;
    }

    restartRecognition({ auto: true })
        .then((success) => {
            if (!success && autoRestartEnabled && shouldReconnect && !isRestarting) {
                console.log('Auto restart failed; retrying in 2 seconds...');
                setTimeout(triggerAutoRestart, 2000);
            }
        })
        .catch((error) => {
            console.error('Auto restart promise rejected:', error);
            if (autoRestartEnabled && shouldReconnect && !isRestarting) {
                console.log('Auto restart failed; retrying in 2 seconds...');
                setTimeout(triggerAutoRestart, 2000);
            }
        });
}

// 重启识别功能
restartButton.addEventListener('click', () => {
    if (lockManualControls) {
        return;
    }
    void restartRecognition();
});

// 暂停/恢复识别功能
pauseButton.addEventListener('click', async () => {
    if (lockManualControls) {
        return;
    }
    try {
        if (isPaused) {
            // 恢复识别
            const response = await fetch('/resume', { method: 'POST' });
            if (response.ok) {
                isPaused = false;
                updatePauseButtonUi();
                console.log('Recognition resumed');
                sessionCostResume();
            }
        } else {
            // 暂停识别
            const response = await fetch('/pause', { method: 'POST' });
            if (response.ok) {
                isPaused = true;
                updatePauseButtonUi();
                console.log('Recognition paused');
                sessionCostPause();
            }
        }
    } catch (error) {
        console.error('Error toggling pause state:', error);
    }
});

if (audioSourceButton) {
    audioSourceButton.addEventListener('click', async () => {
        if (lockManualControls) {
            return;
        }
        const nextSource = getNextAudioSource(audioSource);

        try {
            const response = await fetch('/audio-source', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ source: nextSource })
            });

            let result = null;
            try {
                result = await response.json();
            } catch (parseError) {
                console.error('Failed to parse audio source response:', parseError);
            }

            if (response.ok && result && result.source) {
                audioSource = normalizeAudioSource(result.source);
                updateAudioSourceButton();
                localStorage.setItem('audioSource', audioSource);
                if (result.message) {
                    console.log(result.message);
                } else {
                    console.log(`Audio source switched to ${audioSource}`);
                }
            } else {
                const message = result?.message || `Server responded with status ${response.status}`;
                console.error('Failed to switch audio source:', message);
            }
        } catch (error) {
            console.error('Error switching audio source:', error);
        }
    });
}


// --- 原生字幕悬浮窗（PySide6）开关 ---
function updateOverlayButton() {
    if (!overlayButton) {
        return;
    }
    // 仅更新提示文案，不切换 .active —— 按钮颜色不随悬浮窗开关变化。
    overlayButton.title = overlayOpen ? t('overlay_close') : t('overlay_open');
}

async function refreshOverlayState() {
    if (!overlayButton) {
        return;
    }
    try {
        const response = await fetch('/overlay');
        const result = await response.json();
        if (!result || result.available === false) {
            // 该平台/模式不支持原生悬浮窗，隐藏按钮。
            overlayButton.style.display = 'none';
            return;
        }
        overlayOpen = !!result.open;
        updateOverlayButton();
    } catch (error) {
        console.error('Failed to query overlay state:', error);
    }
}

if (overlayButton) {
    overlayButton.addEventListener('click', async () => {
        if (lockManualControls) {
            return;
        }
        try {
            const response = await fetch('/overlay', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'toggle' })
            });
            const result = await response.json();
            if (result && result.available === false) {
                overlayButton.style.display = 'none';
                return;
            }
            overlayOpen = !!(result && result.open);
            updateOverlayButton();
        } catch (error) {
            console.error('Error toggling subtitle overlay:', error);
        }
    });
    refreshOverlayState();
}




function displayErrorMessage(message) {
    const localizedMessage = localizeBackendMessage(message);
    subtitleContainer.innerHTML = `
        <div class="error-message-overlay">
            <h2 class="error-title">${escapeHtml(t('error_title'))}</h2>
            <p class="error-text">${escapeHtml(localizedMessage)}</p>
            <p class="error-suggestion">${escapeHtml(t('error_suggestion_api'))}</p>
        </div>
    `;
    subtitleContainer.scrollTop = 0; // Ensure error is visible
}

async function fetchApiKeyStatus() {
    try {
        const response = await fetch('/api-key-status');
        if (!response.ok) {
            console.error('Failed to fetch API key status:', response.statusText);
            return;
        }
        const data = await response.json();
        if (data.status === 'error' && data.message) {
            displayErrorMessage(data.message);
        }
    } catch (error) {
        console.error('Error fetching API key status:', error);
        // Do not display a generic network error here, as it might be a temporary server startup issue.
        // The WebSocket connection will eventually show the error if the API key is truly missing.
    }
}

async function fetchOscTranslationStatus() {
    if (!oscTranslationButton) {
        return;
            if (lockManualControls) {
                return;
            }
    }

    try {
        const response = await fetch('/osc-translation');
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        oscTranslationEnabled = !!data.enabled;
        updateOscTranslationButton();
    } catch (error) {
        console.error('Error fetching OSC translation status:', error);
    }
}


function connect() {
    if (hasUsableWebSocket()) {
        return;
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${wsProtocol}://${window.location.host}/ws${window.location.search}`);
    ws = socket;

    socket.onopen = () => {
        console.log('WebSocket connected');
        // The this-session cost meter is NOT started here: opening the local
        // browser↔server socket happens at page load, before login or any
        // billed relay link exists. The meter is driven by recognition-session
        // events from the backend ('session_connected' / 'session_idle' /
        // 'session_disconnected') so it only counts while we're actually billed.
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleMessage(data);
    };

    socket.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    socket.onclose = () => {
        console.log('WebSocket closed');
        // Don't pause the cost meter on a local socket drop: recognition (and
        // billing) may still be running on the backend. The meter is paused by
        // explicit recognition-session events instead.
        if (ws === socket) {
            ws = null;
        }

        if (autoRestartEnabled) {
            triggerAutoRestart();
            return;
        }

        // 只在应该重连且不在重启过程中时才重连
        if (shouldReconnect && !isRestarting) {
            console.log('Attempting to reconnect in 2 seconds...');
            setTimeout(connect, 2000);
        } else {
            console.log('Auto-reconnect disabled');
        }
    };
}

function handleMessage(data) {
    if (data.type === 'overlay_visibility') {
        overlayOpen = !!data.visible;
        updateOverlayButton();
        return;
    }
    if (data.type === 'ipc_status') {
        const btn = document.getElementById('ipcStatusButton');
        if (btn) {
            if (data.connected) {
                btn.style.display = 'flex';
                btn.classList.add('ipc-connected');
            } else {
                btn.style.display = 'none';
                btn.classList.remove('ipc-connected');
            }
        }
        return;
    }
    if (data.type === 'error') {
        displayErrorMessage(data.message);
        if (data.code === 'api_key' && !lockManualControls) {
            openSettings({ forced: true });
        }
        return;
    }
    if (data.type === 'refine_result') {
        handleBackendRefineResult(data);
        return;
    }
    if (data.type === 'segment_mode_changed') {
        handleSegmentModeChanged(data);
        return;
    }
    if (data.type === 'session_connected') {
        // The billed relay link is live (first connect or wake from silence
        // sleep); start counting this-session cost from now.
        if (!isPaused) {
            sessionCostResume();
        }
        return;
    }
    if (data.type === 'session_idle') {
        // Relay link closed for silence sleep (no longer billed); pause the meter.
        sessionCostPause();
        return;
    }
    if (data.type === 'session_disconnected') {
        console.warn('Recognition session disconnected:', data.reason || 'unknown');
        sessionCostPause();
        // Relay (hosted) close codes: show a friendly message and, for terminal
        // ones, stop auto-restart (and re-prompt login when the token is bad).
        const relayKey = RELAY_ERROR_KEYS[data.code];
        if (relayKey) {
            if (data.code === 'billing_exhausted') {
                showToast(t(relayKey), true, {
                    timeoutMs: 8000,
                    actionLabel: t('open_settings'),
                    onAction: () => openSettings({ forced: false }),
                });
            } else {
                showToast(t(relayKey), true);
            }
            if (data.code === 'forbidden' && !lockManualControls) {
                const server = loadServerSettings();
                server.token = '';
                saveServerSettings(server);
                backendLoggedIn = false;
                updateBalanceBarVisibility();
                openLogin({ forced: true });
                return;
            }
            if (data.relay_terminal) {
                return; // do not auto-restart on terminal relay errors
            }
        }
        if (data.code === 'api_key' && !lockManualControls) {
            openSettings({ forced: true });
            return;
        }
        if (autoRestartEnabled && !isRestarting) {
            triggerAutoRestart();
        }
        return;
    }
    if (data.type === 'clear') {
        if (data.preserve_existing) {
            console.log('Finalizing pending subtitles before restart...');
            finalizeCurrentNonFinalTokens();
        } else {
            // 清空所有数据
            console.log('Clearing all subtitles...');
            clearSubtitleState();
        }
        // 不修改UI,因为重启流程会处理
        return;
    }
    
    if (data.type === 'update') {
        let hasNewFinalContent = false;
        if (data.final_tokens && data.final_tokens.length > 0) {
            data.final_tokens.forEach(token => {
                if (token.text === '<end>') {
                    return;
                }
                hasNewFinalContent = true;
                insertFinalToken(token);
            });
        }

        // 更新non-final tokens并过滤 <end>
        // 注意：即使本帧带有 separator（断句）也不要清空 non-final tail。
        // separator 现在由后端在句号处即时下发，而 non_final_tokens 是后端权威的
        // 当前进行中尾巴（属于下一句），与已确定 final token 不重叠。清空它只会让
        // 这段预览消失一帧、等下一帧再出现，造成界面闪烁。
        currentNonFinalTokens = (data.non_final_tokens || []).filter(token => token.text !== '<end>');
        currentNonFinalTokens.forEach(assignSequenceIndex);

        // 合并新增的final tokens
        if (hasNewFinalContent) {
            mergeFinalTokens();
        }

        // 重新渲染
        renderSubtitles();
    }
}

function insertFinalToken(token) {
    assignSequenceIndex(token);
    allFinalTokens.push(token);
}

function joinTokenText(tokens) {
    if (!tokens || tokens.length === 0) {
        return '';
    }
    return tokens.map(t => (t && t.text) ? String(t.text) : '').join('');
}

/**
 * 合并连续的final tokens以减少token数量
 * 只合并从lastMergedIndex开始的新tokens
 * 合并条件：相同speaker、相同language、相同translation_status、is_final=true、非分隔符
 */
function mergeFinalTokens() {
    if (allFinalTokens.length === 0) {
        return;
    }

    const safeStart = Math.max(0, lastMergedIndex - 1);
    const startIndex = Math.min(safeStart, allFinalTokens.length - 1);
    let writeIndex = startIndex;
    let readIndex = startIndex;

    while (readIndex < allFinalTokens.length) {
        const currentToken = allFinalTokens[readIndex];

        // 分隔符或非final token不合并，直接保留
        if (currentToken.is_separator || !currentToken.is_final) {
            allFinalTokens[writeIndex] = currentToken;
            writeIndex++;
            readIndex++;
            continue;
        }

        // 尝试合并连续的相似token
        let mergedText = currentToken.text || '';
        let mergedToken = { ...currentToken };
        let nextIndex = readIndex + 1;

        // 查找可以合并的后续tokens
        while (nextIndex < allFinalTokens.length) {
            const nextToken = allFinalTokens[nextIndex];

            // 检查是否可以合并
            if (
                !nextToken.is_separator &&
                nextToken.is_final &&
                nextToken.speaker === currentToken.speaker &&
                nextToken.language === currentToken.language &&
                (nextToken.translation_status || 'original') === (currentToken.translation_status || 'original') &&
                nextToken.source_language === currentToken.source_language
            ) {
                // 合并文本
                mergedText += (nextToken.text || '');
                nextIndex++;
            } else {
                // 遇到不能合并的token，停止
                break;
            }
        }

        // 更新合并后的token
        mergedToken.text = mergedText;
        mergedToken._merged = true; // 标记为已合并
        const sentenceIds = new Set();
        for (let i = readIndex; i < nextIndex; i++) {
            const id = allFinalTokens[i] && allFinalTokens[i].llm_sentence_id;
            if (id) {
                sentenceIds.add(String(id));
            }
        }
        if (sentenceIds.size === 1) {
            mergedToken.llm_sentence_id = Array.from(sentenceIds)[0];
        } else if (sentenceIds.size > 1) {
            delete mergedToken.llm_sentence_id;
        }

        allFinalTokens[writeIndex] = mergedToken;
        writeIndex++;
        readIndex = nextIndex;
    }

    // 截断数组，移除已合并的重复项
    allFinalTokens.length = writeIndex;

    // 更新lastMergedIndex到新的末尾
    lastMergedIndex = allFinalTokens.length;
}

const RTL_LANGUAGES = new Set(['ar', 'he', 'fa', 'ur', 'yi', 'ps', 'sd', 'ckb', 'dv']);

function isRtlLanguage(langCode) {
    if (!langCode) return false;
    return RTL_LANGUAGES.has(langCode.toLowerCase());
}

function getLangDir(langCode) {
    return isRtlLanguage(langCode) ? 'rtl' : 'ltr';
}

function getLanguageTag(language) {
    if (!language) return '';
    return `<span class="language-tag">${language.toUpperCase()}</span>`;
}

function wrapSubtitleLineBody(innerHtml, dir) {
    return `<span class="subtitle-line-body" dir="${dir || 'auto'}">${innerHtml}</span>`;
}

function assignSequenceIndex(token) {
    if (!token || token._sequenceIndex !== undefined) {
        return;
    }
    token._sequenceIndex = tokenSequenceCounter++;
}

function isCloseToBottom() {
    return (subtitleContainer.scrollTop + subtitleContainer.clientHeight) >= (subtitleContainer.scrollHeight - SCROLL_STICKY_THRESHOLD);
}

function captureScrollState() {
    const wasAtBottom = isCloseToBottom();

    if (wasAtBottom) {
        return { wasAtBottom: true };
    }

    const sentenceBlocks = subtitleContainer.querySelectorAll('.sentence-block');
    const currentScrollTop = subtitleContainer.scrollTop;
    let anchor = null;

    for (const block of sentenceBlocks) {
        const blockTop = block.offsetTop;
        const blockBottom = blockTop + block.offsetHeight;
        if (blockBottom > currentScrollTop) {
            anchor = block;
            break;
        }
    }

    if (anchor) {
        return {
            wasAtBottom: false,
            sentenceId: anchor.dataset.sentenceId,
            offset: currentScrollTop - anchor.offsetTop
        };
    }

    return {
        wasAtBottom: false,
        scrollTop: currentScrollTop
    };
}

function restoreScrollState(state) {
    if (!state) {
        return;
    }

    if (state.wasAtBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
        return;
    }

    if (state.sentenceId) {
        const anchor = subtitleContainer.querySelector(`.sentence-block[data-sentence-id="${state.sentenceId}"]`);
        if (anchor) {
            subtitleContainer.scrollTop = anchor.offsetTop + (state.offset || 0);
            return;
        }
    }

    if (typeof state.scrollTop === 'number') {
        subtitleContainer.scrollTop = state.scrollTop;
    }
}

function getSpeakerClass(speaker) {
    if (speaker === null || speaker === undefined || speaker === 'undefined') {
        return 'speaker-undefined';
    }

    const parsed = Number.parseInt(String(speaker), 10);
    if (Number.isFinite(parsed) && parsed > 0) {
        const normalized = ((parsed - 1) % 15) + 1;
        return `speaker-${normalized}`;
    }

    return `speaker-${speaker}`;
}

function getKuromojiTokenizer() {
    if (kuromojiTokenizerPromise) {
        return kuromojiTokenizerPromise;
    }

    if (!window.kuromoji || typeof window.kuromoji.builder !== 'function') {
        kuromojiTokenizerPromise = Promise.resolve(null);
        return kuromojiTokenizerPromise;
    }

    kuromojiTokenizerPromise = new Promise((resolve) => {
        try {
            window.kuromoji.builder({ dicPath: '/kuromoji/dict/' })
                .build((err, tokenizer) => {
                    if (err) {
                        console.error('Failed to init kuromoji:', err);
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

    return kuromojiTokenizerPromise;
}

function toHiragana(katakana) {
    const value = (katakana || '').toString();
    let out = '';
    for (let i = 0; i < value.length; i++) {
        const code = value.charCodeAt(i);
        if (code >= 0x30a1 && code <= 0x30f6) {
            out += String.fromCharCode(code - 0x60);
        } else {
            out += value[i];
        }
    }
    return out;
}

function hasKanji(text) {
    return /[\u4e00-\u9fff]/.test(text || '');
}

function hasKatakana(text) {
    return /[\u30a0-\u30ff]/.test(text || '');
}

function buildFuriganaHtml(tokens) {
    if (!Array.isArray(tokens) || tokens.length === 0) {
        return null;
    }

    const htmlParts = [];
    tokens.forEach((token) => {
        const surface = (token.surface_form || token.surface || token.basic_form || '').toString();
        if (!surface) {
            return;
        }
        const readingRaw = (token.reading || token.pronunciation || '').toString();
        const reading = readingRaw ? toHiragana(readingRaw) : '';
        const needsRuby = (hasKanji(surface) || hasKatakana(surface)) && reading && reading !== surface;
        if (needsRuby) {
            htmlParts.push(`<ruby>${escapeHtml(surface)}<rp>(</rp><rt>${escapeHtml(reading)}</rt><rp>)</rp></ruby>`);
        } else {
            htmlParts.push(escapeHtml(surface));
        }
    });

    return htmlParts.join('');
}

// 异步获取假名注音
async function getFuriganaHtml(text) {
    if (!text || !furiganaEnabled) {
        return null;
    }
    
    // 检查缓存
    if (furiganaCache.has(text)) {
        return furiganaCache.get(text);
    }

    const tokenizer = await getKuromojiTokenizer();
    if (!tokenizer) {
        return null;
    }

    try {
        const tokens = tokenizer.tokenize(text) || [];
        const html = buildFuriganaHtml(tokens);
        if (html) {
            furiganaCache.set(text, html);
            return html;
        }
    } catch (error) {
        console.error('Failed to tokenize furigana:', error);
    }

    return null;
}

function requestFurigana(text) {
    if (!text || !furiganaEnabled) {
        return;
    }

    if (furiganaCache.has(text) || pendingFuriganaRequests.has(text)) {
        return;
    }

    pendingFuriganaRequests.add(text);
    getFuriganaHtml(text)
        .then((html) => {
            if (html) {
                furiganaCache.set(text, html);
                renderSubtitles();
            }
        })
        .finally(() => {
            pendingFuriganaRequests.delete(text);
        });
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function hasUsableWebSocket() {
    return ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
}

function finalizeCurrentNonFinalTokens({ render = true } = {}) {
    const pendingTokens = (currentNonFinalTokens || [])
        .filter(token => token && token.text && token.text !== '<end>');

    if (pendingTokens.length === 0) {
        return false;
    }

    pendingTokens.forEach(token => {
        insertFinalToken({
            ...token,
            is_final: true
        });
    });

    insertFinalToken({
        is_separator: true,
        is_final: true,
        separator_type: 'reconnect'
    });

    currentNonFinalTokens = [];
    renderedSentences.clear();
    renderedBlocks.clear();
    mergeFinalTokens();

    if (render) {
        renderSubtitles();
    }

    return true;
}

function clearSubtitleState() {
    allFinalTokens = [];
    currentNonFinalTokens = [];
    lastMergedIndex = 0;
    renderedSentences.clear();
    renderedBlocks.clear();
    tokenSequenceCounter = 0;
    pendingFuriganaRequests.clear();

    backendRefinedResults.clear();
    backendRefinedResultsBySentenceId.clear();
    llmTranslationOverrides.clear();
    llmTranslationOverridesBySentenceId.clear();

    if (isLlmTranslateMode()) {
        llmTranslateHideAfterSequence = tokenSequenceCounter;
    } else {
        llmTranslateHideAfterSequence = null;
    }
}

function renderTokenSpan(token, useRubyHtml = null) {
    const classes = ['subtitle-text'];
    if (!token.is_final) {
        classes.push('non-final');
    }
    
    // 如果提供了 ruby HTML（假名注音），使用它
    if (useRubyHtml) {
        return `<span class="${classes.join(' ')}">${useRubyHtml}</span>`;
    }
    
    return `<span class="${classes.join(' ')}">${escapeHtml(token.text)}</span>`;
}

function renderTokenSpanWithText(token, text, useRubyHtml = null) {
    const classes = ['subtitle-text'];
    if (token && token.is_final === false) {
        classes.push('non-final');
    }

    if (useRubyHtml) {
        return `<span class="${classes.join(' ')}">${useRubyHtml}</span>`;
    }

    return `<span class="${classes.join(' ')}">${escapeHtml(text)}</span>`;
}

const EAST_ASIAN_TIGHT_SPACING_CHAR_RE = /[\u3000-\u303F\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF01-\uFF60\uFF66-\uFF9D\uFFE0-\uFFEE]/u;

function getFirstNonWhitespaceChar(text) {
    for (const char of String(text || '')) {
        if (!/\s/u.test(char)) {
            return char;
        }
    }
    return '';
}

function getLastNonWhitespaceChar(text) {
    const chars = Array.from(String(text || ''));
    for (let i = chars.length - 1; i >= 0; i--) {
        if (!/\s/u.test(chars[i])) {
            return chars[i];
        }
    }
    return '';
}

function isEastAsianTightSpacingChar(char) {
    return !!char && EAST_ASIAN_TIGHT_SPACING_CHAR_RE.test(char);
}

function normalizeTranslationTokenTexts(tokens) {
    const texts = (Array.isArray(tokens) ? tokens : []).map((tok) => ((tok && tok.text) ? String(tok.text) : ''));
    if (texts.length === 0) {
        return texts;
    }

    const nextVisibleChars = new Array(texts.length).fill('');
    let nextVisibleChar = '';
    for (let i = texts.length - 1; i >= 0; i--) {
        nextVisibleChars[i] = nextVisibleChar;
        const firstChar = getFirstNonWhitespaceChar(texts[i]);
        if (firstChar) {
            nextVisibleChar = firstChar;
        }
    }

    let prevVisibleChar = '';
    for (let i = 0; i < texts.length; i++) {
        let text = texts[i];
        const firstChar = getFirstNonWhitespaceChar(text);
        const nextChar = firstChar || nextVisibleChars[i];

        if (
            prevVisibleChar &&
            nextChar &&
            isEastAsianTightSpacingChar(prevVisibleChar) &&
            isEastAsianTightSpacingChar(nextChar)
        ) {
            if (firstChar) {
                text = text.replace(/^\s+/u, '');
            } else if (/^\s+$/u.test(text)) {
                text = '';
            }
            texts[i] = text;
        }

        const lastChar = getLastNonWhitespaceChar(texts[i]);
        if (lastChar) {
            prevVisibleChar = lastChar;
        }
    }

    return texts;
}

function renderTokenSpansTrimmed(tokens, useRubyHtml = null, options = {}) {
    // Render token spans but remove leading/trailing whitespace from the concatenated output.
    // IMPORTANT: does NOT mutate token objects; trimming is display-only.
    if (!Array.isArray(tokens) || tokens.length === 0) {
        return '';
    }

    const normalizeTranslationSpacing = !!options.normalizeTranslationSpacing;
    const texts = normalizeTranslationSpacing
        ? normalizeTranslationTokenTexts(tokens)
        : tokens.map((tok) => ((tok && tok.text) ? String(tok.text) : ''));
    const getText = (index) => (texts[index] !== undefined ? texts[index] : '');

    let start = 0;
    let startText = '';
    while (start < tokens.length) {
        const raw = getText(start);
        const trimmed = raw.replace(/^\s+/, '');
        if (trimmed.length === 0 && /^\s*$/.test(raw)) {
            start++;
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
            end--;
            continue;
        }
        endText = trimmed;
        break;
    }

    if (start > end) {
        return '';
    }

    // If a single token remains after trimming whitespace-only edges, trim both ends.
    if (start === end) {
        const raw = getText(start);
        const both = raw.trim();
        if (!both) {
            return '';
        }
        return renderTokenSpanWithText(tokens[start], both, useRubyHtml);
    }

    const parts = [];
    for (let i = start; i <= end; i++) {
        const tok = tokens[i];
        let txt = getText(i);
        if (i === start) {
            txt = startText;
        }
        if (i === end) {
            txt = endText;
        }
        if (!txt) {
            continue;
        }
        parts.push(renderTokenSpanWithText(tok, txt, useRubyHtml));
    }

    return parts.join('');
}

function getSentenceId(sentence, fallbackIndex) {
    const anchorToken = sentence.originalTokens[0] || sentence.translationTokens[0];
    if (anchorToken && anchorToken._sequenceIndex !== undefined) {
        return `sent-${anchorToken._sequenceIndex}`;
    }
    return `sent-fallback-${fallbackIndex}`;
}

const SENTENCE_ENDING_PUNCTUATION_RE = /[。．.！!？?…︒︕︖]$/;

function endsWithSentencePunctuation(text) {
    const value = (text === null || text === undefined) ? '' : String(text).trim();
    return value !== '' && SENTENCE_ENDING_PUNCTUATION_RE.test(value);
}

/**
 * Build the flat token list for rendering. Finalized tokens already carry real
 * separators from the backend. For the non-final (in-progress) tail we
 * speculatively insert separators at sentence-ending punctuation so a completed
 * sentence shows on its own line as soon as the period appears — without waiting
 * for Soniox to finalize. This is render-time only: the non-final tail is rebuilt
 * every update, so the split recomputes and naturally follows revisions; nothing
 * is committed (LLM/OSC finalization still happens on real finalize).
 *
 * Only done when the non-final tail has no translation tokens (the same-language
 * case), to avoid disturbing original/translation pairing.
 */
function buildRenderTokens() {
    const nonFinal = currentNonFinalTokens || [];
    const hasNonFinalTranslation = nonFinal.some(
        token => (token.translation_status || 'original') === 'translation'
    );
    if (hasNonFinalTranslation) {
        return [...allFinalTokens, ...nonFinal];
    }

    const tokens = [...allFinalTokens];
    nonFinal.forEach((token, index) => {
        tokens.push(token);
        const isLast = index === nonFinal.length - 1;
        if (!isLast && !token.is_separator && endsWithSentencePunctuation(token.text)) {
            tokens.push({ is_separator: true, is_final: false, separator_type: 'speculative' });
        }
    });
    return tokens;
}

function renderSubtitles() {
    const scrollState = captureScrollState();
    const tokens = buildRenderTokens();
    tokens.forEach(assignSequenceIndex);

    if (tokens.length === 0) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        subtitleContainer.scrollTop = 0;
        autoStickToBottom = true;
        return;
    }

    const sentences = [];
    let currentSentence = null;

    const ensureSpeakerValue = (speaker) => {
        return (speaker === null || speaker === undefined) ? 'undefined' : speaker;
    };

    const startSentence = (speaker, options = {}) => {
        const normalizedSpeaker = ensureSpeakerValue(speaker);
        const sentence = {
            speaker: normalizedSpeaker,
            originalTokens: [],
            translationTokens: [],
            originalLang: null,
            translationLang: null,
            requiresTranslation: options.requiresTranslation !== undefined ? options.requiresTranslation : null, // null means undecided
            isTranslationOnly: !!options.translationOnly,
            hasFakeTranslation: false
        };
        sentences.push(sentence);
        if (!sentence.isTranslationOnly) {
            currentSentence = sentence;
        }
        return sentence;
    };

    const findLastSentenceForSpeaker = (speaker, predicate = () => true, options = {}) => {
        const normalizedSpeaker = ensureSpeakerValue(speaker);
        const stopOnFakeTranslation = !!options.stopOnFakeTranslation;
        for (let i = sentences.length - 1; i >= 0; i--) {
            const sentence = sentences[i];
            if (sentence.speaker === normalizedSpeaker && predicate(sentence)) {
                return sentence;
            }
            if (stopOnFakeTranslation && sentence.speaker === normalizedSpeaker && sentence.hasFakeTranslation) {
                break;
            }
        }
        return null;
    };

    const findNearestOriginalSentenceForSpeaker = (speaker) => {
        return findLastSentenceForSpeaker(
            speaker,
            (sentence) => !sentence.isTranslationOnly,
            { stopOnFakeTranslation: false }
        );
    };

    tokens.forEach(token => {
        if (token.is_separator) {
            if (
                currentSentence &&
                currentSentence.requiresTranslation !== false &&
                currentSentence.translationTokens.length === 0
            ) {
                currentSentence.hasFakeTranslation = true;
            }

            currentSentence = null;
            return;
        }

        const speaker = ensureSpeakerValue(token.speaker);
        const translationStatus = token.translation_status || 'original';

        if (translationStatus === 'translation') {
            // Strict policy: every translation token attaches to the nearest
            // previous original sentence for the same speaker. If that sentence
            // already has translation text, append to it.
            let targetSentence = findNearestOriginalSentenceForSpeaker(speaker);

            if (!targetSentence) {
                targetSentence = startSentence(speaker, { translationOnly: true });
            }

            if (targetSentence.translationLang === null && token.language) {
                targetSentence.translationLang = token.language;
            }

            if (!targetSentence.originalLang && token.source_language) {
                targetSentence.originalLang = token.source_language;
            }

            targetSentence.translationTokens.push(token);

        } else {
            // 原文 token (original 或 none)
            const tokenRequiresTranslation = (translationStatus !== 'none');

            // 检查是否需要新起一个句子
            let shouldStartNew = false;
            if (!currentSentence) shouldStartNew = true;
            else if (currentSentence.speaker !== speaker) shouldStartNew = true;
            else if (currentSentence.isTranslationOnly) shouldStartNew = true;
            else if (currentSentence.requiresTranslation !== null && currentSentence.requiresTranslation !== tokenRequiresTranslation) {
                // 如果当前句子的翻译需求状态与新token不一致（例如从 original 变 none），则新起一句
                shouldStartNew = true;
            }

            if (shouldStartNew) {
                currentSentence = startSentence(speaker, { requiresTranslation: tokenRequiresTranslation });
            }

            // 确保状态被设置（如果是新句子且 options 没传，或者 null 的情况）
            if (currentSentence.requiresTranslation === null) {
                currentSentence.requiresTranslation = tokenRequiresTranslation;
            }

            if (currentSentence.originalLang === null && token.language) {
                currentSentence.originalLang = token.language;
            } else if (currentSentence.originalLang && token.language && currentSentence.originalLang !== token.language) {
                // 语言变了，新起一句
                currentSentence = startSentence(speaker, { requiresTranslation: tokenRequiresTranslation });
                currentSentence.originalLang = token.language;
            }

            currentSentence.originalTokens.push(token);
        }
    });

    const showTranslation = !suppressTranslationDisplay && (displayMode === 'both' || displayMode === 'translation');
    // When translation is suppressed (e.g. Gemini "no translation"), always keep the
    // original visible so the subtitle area is never empty.
    const showOriginal = suppressTranslationDisplay ? true : (displayMode === 'both' || displayMode === 'original');

    const speakerBlocks = [];
    let currentBlock = null;

    sentences.forEach(sentence => {
        const hasOriginal = showOriginal && sentence.originalTokens.length > 0;
        const hasTranslation = showTranslation && sentence.translationTokens.length > 0;

        if (!hasOriginal && !hasTranslation) {
            return;
        }

        if (!currentBlock || currentBlock.speaker !== sentence.speaker) {
            if (currentBlock) {
                speakerBlocks.push(currentBlock);
            }
            currentBlock = { speaker: sentence.speaker, sentences: [] };
        }

        currentBlock.sentences.push(sentence);
    });

    if (currentBlock) {
        speakerBlocks.push(currentBlock);
    }

    if (speakerBlocks.length === 0) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    let html = '';
    let previousSpeaker = null;
    let fallbackCounter = 0;
    const activeSentenceIds = new Set();
    const pendingSentenceUpdates = [];
    const sentencesToRemove = [];
    let blockingUpdate = false;

    for (const block of speakerBlocks) {
        if (blockingUpdate) {
            break;
        }

        let blockHtml = '';
        const firstSentenceDir = block.sentences.length > 0 ? getLangDir(block.sentences[0].originalLang) : 'ltr';

        if (speakerDiarizationEnabled && !hideSpeakerLabels && block.speaker !== previousSpeaker) {
            blockHtml += `<div class="speaker-label ${getSpeakerClass(block.speaker)}">${escapeHtml(t('speaker_label', { speaker: block.speaker }))}</div>`;
        }

        const sentencesHtml = [];

        for (const sentence of block.sentences) {
            const sentenceId = getSentenceId(sentence, fallbackCounter++);
            activeSentenceIds.add(sentenceId);

            const sentenceParts = [];
            const sentenceDir = getLangDir(sentence.originalLang);

            if (showOriginal && sentence.originalTokens.length > 0) {
                const langTag = getLanguageTag(sentence.originalLang);
                const isJapanese = sentence.originalLang === 'ja';

                if (isJapanese && furiganaEnabled) {
                    const plainText = sentence.originalTokens.map(t => t.text).join('');
                    const hasNonFinal = sentence.originalTokens.some(t => !t.is_final);

                    if (plainText.trim().length === 0) {
                        const lineContent = sentence.originalTokens.map(t => renderTokenSpan(t)).join('');
                        sentenceParts.push(`<div class="subtitle-line original-line" dir="${sentenceDir}">${langTag}${wrapSubtitleLineBody(lineContent, sentenceDir)}</div>`);
                    } else {
                        const rubyHtml = furiganaCache.get(plainText);

                        if (rubyHtml) {
                            const classes = ['subtitle-text'];
                            if (hasNonFinal) {
                                classes.push('non-final');
                            }
                            const rubySpan = `<span class="${classes.join(' ')}">${rubyHtml}</span>`;
                            sentenceParts.push(`<div class="subtitle-line original-line subtitle-line--furigana" dir="${sentenceDir}">${wrapSubtitleLineBody(`${langTag}${rubySpan}`, sentenceDir)}</div>`);
                        } else {
                            requestFurigana(plainText);
                            const previousHtml = renderedSentences.get(sentenceId);
                            if (previousHtml) {
                                sentencesHtml.push(previousHtml);
                            } else {
                                blockingUpdate = true;
                            }
                            continue;
                        }
                    }
                } else {
                    const lineContent = renderTokenSpansTrimmed(sentence.originalTokens);
                    sentenceParts.push(`<div class="subtitle-line original-line" dir="${sentenceDir}">${langTag}${wrapSubtitleLineBody(lineContent, sentenceDir)}</div>`);
                }
            }

            if (blockingUpdate) {
                break;
            }

            if (showTranslation && sentence.translationTokens.length > 0) {
                const translationDir = getLangDir(sentence.translationLang);
                const langTag = getLanguageTag(sentence.translationLang);
                const baseTranslation = sentence.translationTokens.map(t => (t && t.text) ? String(t.text) : '').join('');
                let baseTranslationNormalized = baseTranslation.trim();

                const sourceText = sentence.originalTokens.map(t => (t && t.text) ? String(t.text) : '').join('').trim();
                const key = (sourceText && baseTranslationNormalized)
                    ? `${sourceText}||${baseTranslationNormalized}`
                    : null;
                const sentenceLlmId = getLlmSentenceId(sentence);
                const overrideTranslation = sentenceLlmId && llmTranslationOverridesBySentenceId.has(sentenceLlmId)
                    ? llmTranslationOverridesBySentenceId.get(sentenceLlmId)
                    : (key ? llmTranslationOverrides.get(key) : null);
                if (overrideTranslation) {
                    baseTranslationNormalized = overrideTranslation;
                }
                const hasRefined = (sentenceLlmId && backendRefinedResultsBySentenceId.has(sentenceLlmId))
                    || (key ? backendRefinedResults.has(key) : false);
                const shouldHide = shouldHideSonioxTranslation(sentence, sourceText, hasRefined);

                if (!shouldHide) {
                    const displayTranslation = overrideTranslation
                        ? overrideTranslation
                        : ((sourceText && baseTranslationNormalized)
                            ? getDisplayTranslationForSentence(sentence, sourceText, baseTranslationNormalized)
                            : baseTranslationNormalized);

                    if (displayTranslation && displayTranslation !== baseTranslationNormalized) {
                        const showDiff = llmRefineShowDiff && !isLlmTranslateMode();
                        const html = showDiff
                            ? renderTranslationDiffHtml(baseTranslationNormalized, displayTranslation)
                            : escapeHtml(displayTranslation);
                        sentenceParts.push(`<div class="subtitle-line" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text">${html}</span>`, translationDir)}</div>`);
                    } else if (overrideTranslation) {
                        const html = escapeHtml(displayTranslation || '');
                        sentenceParts.push(`<div class="subtitle-line" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text">${html}</span>`, translationDir)}</div>`);
                    } else {
                        const lineContent = renderTokenSpansTrimmed(sentence.translationTokens, null, {
                            normalizeTranslationSpacing: true
                        });
                        sentenceParts.push(`<div class="subtitle-line" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(lineContent, translationDir)}</div>`);
                    }
                } else {
                    const placeholderText = '&nbsp;';
                    sentenceParts.push(`<div class="subtitle-line" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text placeholder">${placeholderText}</span>`, translationDir)}</div>`);
                }
            }

            if (sentenceParts.length === 0) {
                sentencesToRemove.push(sentenceId);
                continue;
            }

            const sentenceHtml = `<div class="sentence-block" data-sentence-id="${sentenceId}">${sentenceParts.join('')}</div>`;
            sentencesHtml.push(sentenceHtml);
            pendingSentenceUpdates.push({ id: sentenceId, html: sentenceHtml });
        }

        if (blockingUpdate) {
            break;
        }

        if (sentencesHtml.length > 0) {
            blockHtml += sentencesHtml.join('');
        }

        if (blockHtml.trim().length > 0) {
            const blockClass = (block.speaker === previousSpeaker) ? 'subtitle-block same-speaker' : 'subtitle-block';
            html += `<div class="${blockClass}" dir="${firstSentenceDir}">${blockHtml}</div>`;
            previousSpeaker = block.speaker;
        }
    }

    if (blockingUpdate) {
        return;
    }

    const previousRenderedSentences = new Map(renderedSentences);
    const previousRenderedBlocks = new Map(renderedBlocks);

    pendingSentenceUpdates.forEach(({ id, html }) => renderedSentences.set(id, html));
    sentencesToRemove.forEach(id => renderedSentences.delete(id));

    renderedSentences.forEach((_, key) => {
        if (!activeSentenceIds.has(key)) {
            renderedSentences.delete(key);
        }
    });

    if (!html) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    // 增量渲染：解析新生成的 html 到临时容器，然后只更新发生变化的 .sentence-block
    const frag = document.createElement('div');
    frag.innerHTML = html;

    // 如果页面中存在占位 empty-state（"Subtitles will appear here..."），当有真实字幕时应移除
    const emptyNodes = subtitleContainer.querySelectorAll('.empty-state');
    emptyNodes.forEach(node => node.remove());

    // 更通用的清理：移除 subtitleContainer 中所有非字幕占位元素（例如重启提示、Server Closed 等）
    // 保留已有的 `.subtitle-block` 或包含 `.sentence-block` 的节点，删除其它直接子节点
    Array.from(subtitleContainer.children).forEach(child => {
        if (child.classList && child.classList.contains('subtitle-block')) {
            return; // 保留 subtitle-block
        }
        if (child.querySelector && child.querySelector('.sentence-block')) {
            return; // 保留包含句子块的容器
        }
        // 否则认为是占位/状态节点，移除
        child.remove();
    });

    try {
        // 以 subtitle-block 为结构单位，但尽量只更新/追加 sentence-block，避免替换历史 DOM（否则会打断文本选中）
        const newBlocks = Array.from(frag.querySelectorAll('.subtitle-block'));
        const existingBlocks = Array.from(subtitleContainer.querySelectorAll('.subtitle-block'));

        // 索引现有块，键为 data-block-id（若不存在则使用首个 sentence 的 id 作为块 id）
        const existingIndex = new Map();
        existingBlocks.forEach((node, idx) => {
            let id = node.dataset.blockId;
            if (!id) {
                const firstSent = node.querySelector('.sentence-block');
                if (firstSent && firstSent.dataset.sentenceId) {
                    id = `block-${firstSent.dataset.sentenceId}`;
                } else {
                    id = `block-fallback-${idx}`;
                }
                node.dataset.blockId = id;
            }
            existingIndex.set(id, node);
            if (!renderedBlocks.has(id)) {
                renderedBlocks.set(id, node.innerHTML);
            }
        });

        const keepIds = new Set();

        const syncSpeakerLabel = (existingBlock, newBlock) => {
            const getDirectSpeakerLabel = (block) => {
                const first = block ? block.firstElementChild : null;
                if (first && first.classList && first.classList.contains('speaker-label')) {
                    return first;
                }
                return null;
            };

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
        };

        const updateSentenceBlocksInPlace = (existingBlock, newBlock) => {
            const newSentences = Array.from(newBlock.querySelectorAll('.sentence-block'));
            const newSentenceIds = new Set();

            const existingSentenceNodes = Array.from(existingBlock.querySelectorAll('.sentence-block'));
            const existingById = new Map();
            existingSentenceNodes.forEach(node => {
                if (node && node.dataset && node.dataset.sentenceId) {
                    existingById.set(node.dataset.sentenceId, node);
                }
            });

            for (let i = 0; i < newSentences.length; i++) {
                const newSentence = newSentences[i];
                const sentenceId = newSentence.dataset.sentenceId;
                if (!sentenceId) {
                    continue;
                }
                newSentenceIds.add(sentenceId);

                const existingSentence = existingById.get(sentenceId);
                const oldHtml = previousRenderedSentences.get(sentenceId);
                const newHtml = renderedSentences.get(sentenceId) || newSentence.outerHTML;
                const hasChanged = oldHtml !== newHtml;

                if (existingSentence) {
                    if (hasChanged) {
                        if (existingSentence.className !== newSentence.className) {
                            existingSentence.className = newSentence.className;
                        }
                        // 只更新句子内部内容，保留 sentence-block 节点本身，避免影响已渲染历史的 DOM 引用/选择范围
                        if (existingSentence.innerHTML !== newSentence.innerHTML) {
                            existingSentence.innerHTML = newSentence.innerHTML;
                        }
                    }
                    continue;
                }

                // 新句子：尽量插入到正确的位置；若找不到插入点则追加到末尾
                const clone = newSentence.cloneNode(true);
                let inserted = false;
                for (let j = i + 1; j < newSentences.length; j++) {
                    const nextId = newSentences[j].dataset.sentenceId;
                    if (!nextId) {
                        continue;
                    }
                    const nextExisting = existingById.get(nextId);
                    if (nextExisting && nextExisting.parentNode) {
                        nextExisting.parentNode.insertBefore(clone, nextExisting);
                        inserted = true;
                        break;
                    }
                }

                if (!inserted) {
                    existingBlock.appendChild(clone);
                }

                existingById.set(sentenceId, clone);
            }

            // 删除不再存在的句子块（通常发生在切换显示模式/隐藏翻译等手动操作）
            existingById.forEach((node, id) => {
                if (!newSentenceIds.has(id)) {
                    node.remove();
                }
            });
        };

        // 遍历新的 subtitle-block，进行就地更新/插入
        for (let i = 0; i < newBlocks.length; i++) {
            const newBlock = newBlocks[i];
            let id = newBlock.dataset.blockId;
            if (!id) {
                const firstSent = newBlock.querySelector('.sentence-block');
                if (firstSent && firstSent.dataset.sentenceId) {
                    id = `block-${firstSent.dataset.sentenceId}`;
                } else {
                    id = `block-fallback-${i}`;
                }
                newBlock.dataset.blockId = id;
            }

            const existingNode = existingIndex.get(id);
            const newInnerHtml = newBlock.innerHTML;

            if (existingNode) {
                // 结构块存在：尽量不替换节点，只更新 class/speaker label/发生变化的句子
                if (existingNode.className !== newBlock.className) {
                    existingNode.className = newBlock.className;
                }

                const oldBlockHtml = previousRenderedBlocks.get(id);
                const hasBlockChanged = oldBlockHtml !== newInnerHtml;
                if (hasBlockChanged) {
                    syncSpeakerLabel(existingNode, newBlock);
                    updateSentenceBlocksInPlace(existingNode, newBlock);
                }

                renderedBlocks.set(id, newInnerHtml);
                keepIds.add(id);
                continue;
            }

            // 新的 subtitle-block：插入完整节点（不会触碰历史块）
            const wrapper = newBlock.cloneNode(true);
            wrapper.dataset.blockId = id;

            let inserted = false;
            for (let j = i + 1; j < newBlocks.length; j++) {
                const nextFirst = newBlocks[j].querySelector('.sentence-block');
                const nextId = nextFirst && nextFirst.dataset.sentenceId ? `block-${nextFirst.dataset.sentenceId}` : newBlocks[j].dataset.blockId;
                if (!nextId) {
                    continue;
                }
                const nextExisting = subtitleContainer.querySelector(`.subtitle-block[data-block-id="${nextId}"]`);
                if (nextExisting) {
                    subtitleContainer.insertBefore(wrapper, nextExisting);
                    inserted = true;
                    break;
                }
            }
            if (!inserted) {
                subtitleContainer.appendChild(wrapper);
            }

            renderedBlocks.set(id, wrapper.innerHTML);
            keepIds.add(id);
        }

        // 移除旧的、不再需要的块
        existingBlocks.forEach(node => {
            const id = node.dataset.blockId || (node.querySelector('.sentence-block') ? `block-${node.querySelector('.sentence-block').dataset.sentenceId}` : null);
            if (id && !keepIds.has(id)) {
                node.remove();
                renderedBlocks.delete(id);
            }
        });

    } catch (e) {
        // 在任何异常情况下回退到全量替换，保证正确性
        console.warn('Incremental render (block-level) failed, falling back to full render:', e);
        subtitleContainer.innerHTML = html;
        // 同步缓存为当前 DOM
        renderedBlocks.clear();
        const allBlocks = subtitleContainer.querySelectorAll('.subtitle-block');
        allBlocks.forEach((node, idx) => {
            let id = node.dataset.blockId;
            if (!id) {
                const first = node.querySelector('.sentence-block');
                id = first && first.dataset.sentenceId ? `block-${first.dataset.sentenceId}` : `block-fallback-${idx}`;
                node.dataset.blockId = id;
            }
            renderedBlocks.set(id, node.innerHTML);
        });
    }

    // 恢复滚动状态并处理自动贴底
    restoreScrollState(scrollState);
    autoStickToBottom = scrollState ? scrollState.wasAtBottom : isCloseToBottom();
    if (autoStickToBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
    }
}

subtitleContainer.addEventListener('scroll', () => {
    autoStickToBottom = isCloseToBottom();
});

window.addEventListener('resize', () => {
    if (autoStickToBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
    }
});

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===================== Settings panel (provider + API key) =====================

function updateSettingsButtonVisibility() {
    if (settingsButton) {
        settingsButton.style.display = lockManualControls ? 'none' : '';
    }
    if (overlayButton && lockManualControls) {
        overlayButton.style.display = 'none';
    }
}

function applySettingsI18n() {
    const setText = (id, key) => {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = t(key);
        }
    };
    setText('settingsTitle', 'settings');
    setText('modeLabel', 'conn_mode');
    setText('modeRelayLabel', 'conn_mode_relay');
    setText('modeDirectLabel', 'conn_mode_direct');
    setText('accountLabel', 'account');
    setText('redeemLabel', 'account_redeem_label');
    setText('redeemButton', 'account_redeem');
    setText('purchaseCreditsLink', 'account_purchase_credits');
    setText('copyInviteButton', 'account_invite_copy');
    setText('openUserWebButton', 'account_open_web');
    setText('reLoginButton', 'account_relogin');
    setText('logoutButton', 'account_logout');
    setText('providerLabel', 'api_selection');
    setText('providerSonioxLabel', 'provider_soniox');
    setText('providerGeminiLabel', 'provider_gemini');
    setText('sonioxRegionLabel', 'soniox_region');
    setText('microphoneDeviceLabel', 'microphone_device');
    setText('runtimeControlsLabel', 'recognition_controls');
    setText('autoRestartSettingLabel', 'auto_restart_setting');
    setText('segmentModeSettingLabel', 'segment_mode_setting');
    // Rebuild the region picker so its option labels follow the active language.
    renderSonioxRegionPicker(getSelectedSonioxRegion());
    renderMicrophoneDevicePicker();
    renderRuntimeSettingsPickers();
    if (settingsSaveButton) settingsSaveButton.textContent = t('save');
    if (settingsCancelButton) settingsCancelButton.textContent = t('cancel');
    if (settingsModeBackButton) settingsModeBackButton.textContent = t('mode_back_to_chooser');
    if (resetAllButton) resetAllButton.textContent = t('reset_all');
    if (settingsButton) settingsButton.title = t('settings');
    if (settingsCloseButton) settingsCloseButton.title = t('close');
}

function getDesiredProvider() {
    const settings = loadProviderSettings();
    return settings.providerOverride || translationProvider || 'soniox';
}

function setProviderRadio(provider) {
    if (!settingsForm) {
        return;
    }
    settingsForm.querySelectorAll('input[name="provider"]').forEach((radio) => {
        radio.checked = (radio.value === provider);
    });
}

function getSelectedProvider() {
    if (settingsForm) {
        const checked = settingsForm.querySelector('input[name="provider"]:checked');
        if (checked) {
            return checked.value;
        }
    }
    return getDesiredProvider();
}

function getProviderDisplayName(provider) {
    return provider === 'gemini' ? t('provider_gemini') : t('provider_soniox');
}

// Format a per-second rate with enough precision for small fractional values.
function formatRate(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    if (n === 0) return '0';
    if (n < 1) return parseFloat(n.toPrecision(2)).toString();
    return (Math.round(n * 100) / 100).toString();
}

// Provider description line. In relay mode show the server's unit price
// (credits/sec) for that provider's model instead of the direct-mode blurb.
function getProviderDescriptionText(provider) {
    if (getSettingsMode() === 'relay') {
        const info = relayPricing && relayPricing[provider];
        if (!info) {
            return t('provider_relay_desc_loading');
        }
        // Show every configured free pool (total allocation > 0) for this provider,
        // e.g. "托管中转 · 免费 (日 100 / 周 500 / 月 2000)".
        const summary = freePoolsSummary(info.free_pools);
        if (summary) {
            return `${t('provider_relay_desc_free')} (${summary})`;
        }
        return t('provider_relay_desc', { price: formatRate(info.price_per_second) });
    }
    return t(`provider_${provider}_desc`);
}

async function fetchRelayPricing() {
    if (!relayAvailable) return;
    try {
        const resp = await fetch('/account/pricing');
        if (!resp.ok) return;
        const data = await resp.json();
        relayPricing = (data && data.pricing) || {};
        // Refresh the description if the settings panel is open in relay mode.
        if (settingsPanel && !settingsPanel.hidden) {
            updateApiKeyFieldForProvider(getSelectedProvider());
        }
    } catch (e) {
        // ignore
    }
}

function normalizeSonioxRegion(region) {
    const r = String(region || '').trim().toLowerCase();
    return SONIOX_REGIONS.includes(r) ? r : 'us';
}

function getDesiredSonioxRegion() {
    const settings = loadProviderSettings();
    return normalizeSonioxRegion(settings.sonioxRegion || backendSonioxRegion || 'us');
}

function getSelectedSonioxRegion() {
    // No selectable region when the backend pins a custom endpoint; leave it untouched.
    if (backendSonioxCustomUrl) {
        return null;
    }
    if (sonioxRegionPickerEl && sonioxRegionPickerEl.value) {
        return normalizeSonioxRegion(sonioxRegionPickerEl.value);
    }
    return getDesiredSonioxRegion();
}

// (Re)build the custom region select with current-language option labels.
function renderSonioxRegionPicker(selectedRegion) {
    if (!sonioxRegionPickerHost) {
        return;
    }
    sonioxRegionPickerHost.innerHTML = '';
    if (backendSonioxCustomUrl) {
        // Backend set a custom Soniox address: show a disabled "Custom" picker.
        const options = [{ value: 'custom', label: t('soniox_region_custom') }];
        sonioxRegionPickerEl = buildCustomSelect(options, { value: 'custom', disabled: true });
        sonioxRegionPickerHost.appendChild(sonioxRegionPickerEl);
        return;
    }
    const value = normalizeSonioxRegion(selectedRegion);
    const options = SONIOX_REGIONS.map((region) => ({
        value: region,
        label: t(`soniox_region_${region}`),
    }));
    sonioxRegionPickerEl = buildCustomSelect(options, { value });
    sonioxRegionPickerHost.appendChild(sonioxRegionPickerEl);
}

// Region applies to Soniox direct mode only; hidden for other providers and in
// relay mode (the hosted server picks the endpoint).
function updateSonioxRegionForProvider(provider) {
    const relay = getSettingsMode() === 'relay';
    if (sonioxRegionSection) {
        sonioxRegionSection.hidden = relay || (provider !== 'soniox');
    }
    if (!relay && provider === 'soniox') {
        renderSonioxRegionPicker(getDesiredSonioxRegion());
    }
}

function updateApiKeyFieldForProvider(provider) {
    const settings = loadProviderSettings();
    const override = settings.keys && settings.keys[provider];
    const providerName = getProviderDisplayName(provider);
    const label = document.getElementById('apiKeyLabel');
    if (label) {
        label.textContent = `${providerName} ${t('api_key')}`;
    }
    if (apiKeyInput) {
        apiKeyInput.value = override || '';
        if (override) {
            apiKeyInput.placeholder = '';
        } else if (envKeyPresent[provider]) {
            apiKeyInput.placeholder = t('api_key_placeholder_env_configured', { provider: providerName });
        } else {
            apiKeyInput.placeholder = t('api_key_placeholder_env_missing', { provider: providerName });
        }
    }
    if (apiKeySourceHint) {
        apiKeySourceHint.textContent = '';
    }
    if (providerDescription) {
        providerDescription.textContent = getProviderDescriptionText(provider);
    }
    if (apiKeyGetLink) {
        const url = PROVIDER_KEY_URLS[provider];
        if (url) {
            apiKeyGetLink.textContent = t('api_key_get_link', { provider: providerName });
            apiKeyGetLink.href = url;
            apiKeyGetLink.parentElement.hidden = false;
        } else {
            apiKeyGetLink.textContent = '';
            apiKeyGetLink.removeAttribute('href');
            apiKeyGetLink.parentElement.hidden = true;
        }
    }
}

function populateSettingsForm() {
    const provider = getDesiredProvider();
    setProviderRadio(provider);
    // Set the mode radio first so the region/description helpers (which read it)
    // see the correct mode.
    const mode = (getConnectionMode() === 'relay') ? 'relay' : 'direct';
    setModeRadio(mode);
    updateApiKeyFieldForProvider(provider);
    updateSonioxRegionForProvider(provider);
    applyModeSectionsVisibility(mode);
    renderMicrophoneDevicePicker();
    renderRuntimeSettingsPickers();
    updateAccountSection();
    if (settingsErrorEl) {
        settingsErrorEl.textContent = setupRequired ? t('setup_required_hint') : '';
    }
}

function setModeRadio(mode) {
    if (!settingsForm) {
        return;
    }
    settingsForm.querySelectorAll('input[name="connmode"]').forEach((radio) => {
        radio.checked = (radio.value === mode);
    });
}

function getSettingsMode() {
    if (!relayAvailable) {
        return 'direct';
    }
    if (settingsForm) {
        const checked = settingsForm.querySelector('input[name="connmode"]:checked');
        if (checked) {
            return checked.value;
        }
    }
    return (getConnectionMode() === 'relay') ? 'relay' : 'direct';
}

// Show the mode toggle only when a server is configured; in relay mode swap the
// API-key field for the account panel (provider + region stay in both modes).
function applyModeSectionsVisibility(mode) {
    const modeSection = document.getElementById('modeSection');
    const accountSection = document.getElementById('accountSection');
    const apiKeySection = document.getElementById('apiKeySection');
    if (modeSection) modeSection.hidden = !relayAvailable;
    const relay = (mode === 'relay');
    if (accountSection) accountSection.hidden = !relay;
    if (apiKeySection) apiKeySection.hidden = relay;
    const modeDesc = document.getElementById('modeDescription');
    if (modeDesc) modeDesc.textContent = t(relay ? 'conn_mode_relay_desc' : 'conn_mode_direct_desc');
}

function updateAccountSection() {
    const serverHint = document.getElementById('accountServerHint');
    const identityHint = document.getElementById('accountIdentityHint');
    const purchaseHint = document.getElementById('purchaseCreditsHint');
    const purchaseLink = document.getElementById('purchaseCreditsLink');
    if (serverHint) {
        serverHint.textContent = relayServerUrl ? t('account_server', { url: relayServerUrl }) : '';
    }
    if (identityHint) {
        const server = loadServerSettings();
        if (backendLoggedIn || server.token) {
            const name = server.displayName || '—';
            const rank = server.trustRank || '—';
            identityHint.textContent = t('account_identity', { name, rank });
        } else {
            identityHint.textContent = t('account_not_signed_in');
        }
    }
    if (purchaseHint && purchaseLink) {
        if (creditsPurchaseUrl) {
            purchaseLink.href = creditsPurchaseUrl;
            purchaseLink.textContent = t('account_purchase_credits');
            purchaseHint.hidden = false;
        } else {
            purchaseLink.removeAttribute('href');
            purchaseHint.hidden = true;
        }
    }
    updateAccountBalance();
}

// Show the signed-in user's current balance and free pools inside the account
// panel (requirement: account info also shows the current quota balance).
function updateAccountBalance() {
    const balanceHint = document.getElementById('accountBalanceHint');
    const poolsBox = document.getElementById('accountFreePools');
    const server = loadServerSettings();
    const signedIn = backendLoggedIn || !!server.token;
    if (balanceHint) {
        if (signedIn && lastBalanceData && lastBalanceData.prepaid_balance != null) {
            balanceHint.textContent = t('account_balance', {
                balance: formatCredits(lastBalanceData.prepaid_balance),
            });
            balanceHint.hidden = false;
        } else {
            balanceHint.textContent = '';
            balanceHint.hidden = true;
        }
    }
    if (poolsBox) {
        const pools = (signedIn && lastBalanceData && lastBalanceData.free) ? lastBalanceData.free.pools : null;
        renderFreePools(poolsBox, pools);
    }
}

function openSettings({ forced = false } = {}) {
    if (lockManualControls) {
        return;
    }
    settingsForcedOpen = !!forced;
    applySettingsI18n();
    populateSettingsForm();
    if (relayAvailable && getConnectionMode() === 'relay') {
        void fetchRelayPricing();
        void fetchBalance();
    }
    void fetchMicrophoneDevices();
    if (settingsOverlay) settingsOverlay.hidden = false;
    if (settingsPanel) settingsPanel.hidden = false;
    const hideClose = settingsForcedOpen ? 'none' : '';
    if (settingsCancelButton) settingsCancelButton.style.display = hideClose;
    if (settingsCloseButton) settingsCloseButton.style.display = hideClose;
    if (settingsModeBackButton) settingsModeBackButton.hidden = !(settingsForcedOpen && relayAvailable);
}

function hideSettingsPanel() {
    if (settingsOverlay) settingsOverlay.hidden = true;
    if (settingsPanel) settingsPanel.hidden = true;
    settingsForcedOpen = false;
}

function closeSettings() {
    if (settingsForcedOpen) {
        return;
    }
    hideSettingsPanel();
}

async function pushSetup(provider, apiKey, { silent = false, region = null, mode = null, token = null } = {}) {
    try {
        const body = { provider };
        if (mode) {
            body.mode = mode;
        }
        if (mode === 'relay') {
            if (token) {
                body.token = token;
            }
        } else if (apiKey) {
            body.api_key = apiKey;
        }
        if (provider === 'soniox' && region) {
            body.soniox_region = region;
        }
        const resp = await fetch('/setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            return { ok: false, data };
        }
        translationProvider = data.provider || provider;
        backendBootId = data.boot_id || backendBootId;
        setupRequired = !!data.setup_required;
        if (typeof data.mode === 'string') {
            backendMode = data.mode;
        }
        if (typeof data.logged_in === 'boolean') {
            backendLoggedIn = !!data.logged_in;
        }
        pushedOverrideBootId = backendBootId;
        if (data.downgraded_two_way) {
            showToast(t('gemini_no_two_way_warning'), true);
            uiTranslationMode = 'one_way';
            setUiTranslationMode('one_way', { persistOnly: true });
        }
        await fetchUiConfig();
        if (!silent && !data.setup_required) {
            showToast(t('settings_saved'));
        }
        return { ok: true, data };
    } catch (err) {
        return { ok: false, data: { message: String(err) } };
    }
}

let confirmResolve = null;

function closeConfirmDialog(result) {
    if (confirmOverlay) confirmOverlay.hidden = true;
    if (confirmDialog) confirmDialog.hidden = true;
    document.removeEventListener('keydown', handleConfirmKeydown);
    if (confirmResolve) {
        const resolve = confirmResolve;
        confirmResolve = null;
        resolve(result);
    }
}

function handleConfirmKeydown(event) {
    if (event.key === 'Escape') {
        closeConfirmDialog(false);
    } else if (event.key === 'Enter') {
        closeConfirmDialog(true);
    }
}

// 自定义确认对话框，替代浏览器自带的 confirm()。
function showConfirm(message, { okLabel, cancelLabel, danger = false } = {}) {
    if (!confirmDialog || !confirmOverlay) {
        return Promise.resolve(window.confirm(message));
    }
    if (confirmResolve) {
        // 已有对话框在显示，先取消旧的。
        closeConfirmDialog(false);
    }
    if (confirmMessageEl) confirmMessageEl.textContent = message;
    if (confirmOkButton) {
        confirmOkButton.textContent = okLabel || t('confirm');
        confirmOkButton.className = danger ? 'danger-button' : 'primary-button';
    }
    if (confirmCancelButton) confirmCancelButton.textContent = cancelLabel || t('cancel');
    confirmOverlay.hidden = false;
    confirmDialog.hidden = false;
    document.addEventListener('keydown', handleConfirmKeydown);
    if (confirmCancelButton) confirmCancelButton.focus();
    return new Promise((resolve) => {
        confirmResolve = resolve;
    });
}

if (confirmOkButton) {
    confirmOkButton.addEventListener('click', () => closeConfirmDialog(true));
}
if (confirmCancelButton) {
    confirmCancelButton.addEventListener('click', () => closeConfirmDialog(false));
}
if (confirmOverlay) {
    confirmOverlay.addEventListener('click', () => closeConfirmDialog(false));
}

async function handleResetAll() {
    const confirmed = await showConfirm(t('reset_all_confirm'), {
        okLabel: t('reset_all'),
        cancelLabel: t('cancel'),
        danger: true,
    });
    if (!confirmed) {
        return;
    }
    // 清除前端保存的所有数据（包括 API 配置、主题、各类开关偏好）。
    try {
        localStorage.clear();
    } catch (_) {}
    try {
        sessionStorage.clear();
    } catch (_) {}
    // 请求应用退出（在 WebView 桌面模式下生效）。服务器会先返回响应再延迟退出。
    try {
        await fetch('/shutdown', { method: 'POST' });
    } catch (_) {
        // 服务器正在关闭，请求可能无法完成，忽略即可。
    }
    try {
        window.close();
    } catch (_) {}
    // 浏览器模式下脚本通常无法关闭窗口，显示已退出提示作为兜底。
    const doneColor = document.body.classList.contains('dark-theme') ? '#e5e7eb' : '#1f2937';
    document.body.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;'
        + 'height:100vh;font-size:15px;opacity:0.7;text-align:center;padding:24px;'
        + 'color:' + doneColor + ';">'
        + t('reset_all_done') + '</div>';
}

async function handleSettingsSave(event) {
    if (event) {
        event.preventDefault();
    }
    const provider = getSelectedProvider();
    const region = getSelectedSonioxRegion();
    const mode = getSettingsMode();
    const settings = loadProviderSettings();
    settings.providerOverride = provider;
    if (region) {
        settings.sonioxRegion = region;
    }
    settings.keys = settings.keys || {};

    if (mode === 'relay') {
        const allowed = await ensureHostedVersionAllowed({ candidateMode: 'relay' });
        if (!allowed) {
            updateApiKeyFieldForProvider(provider);
            updateSonioxRegionForProvider(provider);
            return;
        }
    }

    const server = loadServerSettings();
    server.mode = mode;
    server.modeChosen = true;
    saveServerSettings(server);

    const runtimeResult = await applyRuntimeControlSettings();
    if (!runtimeResult.ok) {
        if (settingsErrorEl) settingsErrorEl.textContent = runtimeResult.message || t('backend_segment_mode_disabled');
        return;
    }

    if (mode === 'relay') {
        // Hosted mode: no provider key needed; sign-in supplies the token.
        if (!server.token) {
            saveProviderSettings(settings);
            hideSettingsPanel();
            openLogin({ forced: false });
            return;
        }
        if (settingsSaveButton) { settingsSaveButton.disabled = true; settingsSaveButton.textContent = t('saving'); }
        if (settingsErrorEl) settingsErrorEl.textContent = '';
        const micResult = await saveMicrophoneDeviceSelection();
        if (!micResult.ok) {
            if (settingsSaveButton) { settingsSaveButton.disabled = false; settingsSaveButton.textContent = t('save'); }
            if (settingsErrorEl) settingsErrorEl.textContent = micResult.message || t('validation_error');
            return;
        }
        saveProviderSettings(settings);
        const result = await pushSetup(provider, null, {
            silent: false, mode: 'relay', token: server.token, region,
        });
        if (settingsSaveButton) { settingsSaveButton.disabled = false; settingsSaveButton.textContent = t('save'); }
        if (!result.ok) {
            const msg = (result.data && result.data.message) || t('validation_api_key');
            if (settingsErrorEl) settingsErrorEl.textContent = localizeBackendMessage(msg);
            return;
        }
        if (result.data && result.data.setup_required) {
            hideSettingsPanel();
            openLogin({ forced: true });
            return;
        }
        hideSettingsPanel();
        clearSubtitleState();
        return;
    }

    // ----- direct mode (user's own provider key) -----
    const key = (apiKeyInput && apiKeyInput.value || '').trim();
    if (key) {
        settings.keys[provider] = key;
    } else {
        delete settings.keys[provider];
    }
    const hasOverride = !!(settings.keys && settings.keys[provider]);
    if (!hasOverride && !envKeyPresent[provider]) {
        if (settingsErrorEl) settingsErrorEl.textContent = t('api_key_required');
        return;
    }
    saveProviderSettings(settings);

    if (settingsSaveButton) settingsSaveButton.disabled = true;
    if (settingsSaveButton) settingsSaveButton.textContent = t('saving');
    if (settingsErrorEl) settingsErrorEl.textContent = '';

    const micResult = await saveMicrophoneDeviceSelection();
    if (!micResult.ok) {
        if (settingsSaveButton) settingsSaveButton.disabled = false;
        if (settingsSaveButton) settingsSaveButton.textContent = t('save');
        if (settingsErrorEl) settingsErrorEl.textContent = micResult.message || t('validation_error');
        return;
    }

    const apiKeyToPush = (settings.keys && settings.keys[provider]) || null;
    const result = await pushSetup(provider, apiKeyToPush, { silent: false, region, mode: 'direct' });

    if (settingsSaveButton) settingsSaveButton.disabled = false;
    if (settingsSaveButton) settingsSaveButton.textContent = t('save');

    if (!result.ok) {
        const msg = (result.data && result.data.message) || t('validation_api_key');
        if (settingsErrorEl) settingsErrorEl.textContent = localizeBackendMessage(msg);
        return;
    }
    if (result.data && result.data.setup_required) {
        if (settingsErrorEl) settingsErrorEl.textContent = t('setup_required_hint');
        populateSettingsForm();
        return;
    }
    hideSettingsPanel();
    clearSubtitleState();
}

async function syncProviderFromStorage() {
    if (lockManualControls) {
        return;
    }
    const settings = loadProviderSettings();
    const desiredProvider = settings.providerOverride || translationProvider || 'soniox';
    // When the backend pins a custom endpoint, never push a region (it would override the URL).
    const desiredRegion = backendSonioxCustomUrl ? null : getDesiredSonioxRegion();
    const providerMismatch = settings.providerOverride && desiredProvider !== translationProvider;
    const mode = getConnectionMode();

    if (mode === 'relay') {
        const server = loadServerSettings();
        const token = server.token || '';
        const modeMismatch = backendMode !== 'relay';
        const needTokenPush = token && !backendLoggedIn;
        if (!providerMismatch && !modeMismatch && !needTokenPush) {
            return;
        }
        if (pushedOverrideBootId === backendBootId) {
            return;
        }
        await pushSetup(desiredProvider, null, {
            silent: true, mode: 'relay', token, region: desiredRegion,
        });
        return;
    }

    if (mode === 'direct') {
        const overrideKey = settings.keys && settings.keys[desiredProvider];
        const needKeyPush = overrideKey && backendKeySource !== 'localstorage';
        const regionMismatch = !backendSonioxCustomUrl
            && desiredProvider === 'soniox'
            && settings.sonioxRegion
            && desiredRegion !== backendSonioxRegion;
        const modeMismatch = backendMode !== 'direct';
        if (!providerMismatch && !needKeyPush && !regionMismatch && !modeMismatch) {
            return;
        }
        if (pushedOverrideBootId === backendBootId) {
            return;
        }
        await pushSetup(desiredProvider, overrideKey || null, {
            silent: true, mode: 'direct', region: desiredRegion,
        });
    }
    // mode === null (undecided) is handled by the first-launch chooser.
}

function maybeForceOpenSettings() {
    if (lockManualControls) {
        return;
    }
    const mode = getConnectionMode();
    if (mode === 'relay') {
        const hasToken = !!loadServerSettings().token;
        if (!hasToken || setupRequired) {
            openLogin({ forced: true });
        }
        return;
    }
    if (mode === 'direct' && setupRequired) {
        openSettings({ forced: true });
    }
}

if (settingsButton) {
    settingsButton.addEventListener('click', () => openSettings());
}
if (settingsCloseButton) {
    settingsCloseButton.addEventListener('click', () => closeSettings());
}
if (settingsCancelButton) {
    settingsCancelButton.addEventListener('click', () => closeSettings());
}
if (settingsModeBackButton) {
    settingsModeBackButton.addEventListener('click', () => returnToModeChooser());
}
if (resetAllButton) {
    resetAllButton.addEventListener('click', () => handleResetAll());
}
if (settingsOverlay) {
    settingsOverlay.addEventListener('click', () => closeSettings());
}
if (settingsForm) {
    settingsForm.addEventListener('submit', handleSettingsSave);
    settingsForm.querySelectorAll('input[name="provider"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            const provider = getSelectedProvider();
            updateApiKeyFieldForProvider(provider);
            updateSonioxRegionForProvider(provider);
        });
    });
    settingsForm.querySelectorAll('input[name="connmode"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            const mode = getSettingsMode();
            applyModeSectionsVisibility(mode);
            updateAccountSection();
            // Region + price description depend on the mode.
            const provider = getSelectedProvider();
            updateSonioxRegionForProvider(provider);
            updateApiKeyFieldForProvider(provider);
            if (mode === 'relay') {
                void fetchRelayPricing();
            }
        });
    });
}

// Account actions (relay/hosted mode).
const redeemButton = document.getElementById('redeemButton');
const redeemInput = document.getElementById('redeemInput');
const reLoginButton = document.getElementById('reLoginButton');
const logoutButton = document.getElementById('logoutButton');
const copyInviteButton = document.getElementById('copyInviteButton');
const openUserWebButton = document.getElementById('openUserWebButton');
if (redeemButton) {
    redeemButton.addEventListener('click', () => handleRedeem());
}
if (copyInviteButton) {
    copyInviteButton.addEventListener('click', () => handleCopyInvite());
}
if (openUserWebButton) {
    openUserWebButton.addEventListener('click', () => handleOpenUserWeb());
}
if (reLoginButton) {
    reLoginButton.addEventListener('click', () => {
        hideSettingsPanel();
        openLogin({ forced: false });
    });
}
if (logoutButton) {
    logoutButton.addEventListener('click', () => handleLogout());
}

// ===================== Relay (hosted) client: chooser / login / account / balance =====================

const modeChooserOverlay = document.getElementById('modeChooserOverlay');
const modeChooserEl = document.getElementById('modeChooser');
const clientUpdateOverlay = document.getElementById('clientUpdateOverlay');
const clientUpdateDialog = document.getElementById('clientUpdateDialog');
const clientUpdateTitle = document.getElementById('clientUpdateTitle');
const clientUpdateBody = document.getElementById('clientUpdateBody');
const clientUpdateCurrentLabel = document.getElementById('clientUpdateCurrentLabel');
const clientUpdateLatestLabel = document.getElementById('clientUpdateLatestLabel');
const clientUpdateMinimumLabel = document.getElementById('clientUpdateMinimumLabel');
const clientUpdateCurrent = document.getElementById('clientUpdateCurrent');
const clientUpdateLatest = document.getElementById('clientUpdateLatest');
const clientUpdateMinimum = document.getElementById('clientUpdateMinimum');
const clientUpdateNotesEl = document.getElementById('clientUpdateNotes');
const clientUpdateNoUrl = document.getElementById('clientUpdateNoUrl');
const clientUpdateDirectButton = document.getElementById('clientUpdateDirectButton');
const clientUpdateLaterButton = document.getElementById('clientUpdateLaterButton');
const clientUpdateButton = document.getElementById('clientUpdateButton');
const loginOverlay = document.getElementById('loginOverlay');
const loginPanel = document.getElementById('loginPanel');
const loginForm = document.getElementById('loginForm');
const loginCloseButton = document.getElementById('loginCloseButton');
const loginUserInput = document.getElementById('loginUserInput');
const loginPrimaryButton = document.getElementById('loginPrimaryButton');
const loginModeBackButton = document.getElementById('loginModeBackButton');
const loginBackButton = document.getElementById('loginBackButton');
const loginCopyButton = document.getElementById('loginCopyButton');
const loginPasteButton = document.getElementById('loginPasteButton');
const loginCodeLink = document.getElementById('loginCodeLink');
const loginMethodList = document.getElementById('loginMethodList');
const loginErrorEl = document.getElementById('loginError');
const balanceBar = document.getElementById('balanceBar');
const balanceActionItem = document.getElementById('balanceActionItem');
const balanceOpenSettingsButton = document.getElementById('balanceOpenSettingsButton');
if (balanceOpenSettingsButton) {
    balanceOpenSettingsButton.addEventListener('click', () => openSettings({ forced: false }));
}

function setElText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ---- First-launch mode chooser ----
function applyChooserI18n() {
    setElText('modeChooserTitle', t('chooser_title'));
    setElText('modeChooserHint', t('chooser_hint'));
    setElText('modeChooserRelayTitle', t('chooser_relay_title'));
    setElText('modeChooserRelayDesc', t('chooser_relay_desc'));
    setElText('modeChooserDirectTitle', t('chooser_direct_title'));
    setElText('modeChooserDirectDesc', t('chooser_direct_desc'));
}

function openModeChooser() {
    return new Promise((resolve) => {
        applyChooserI18n();
        if (modeChooserOverlay) modeChooserOverlay.hidden = false;
        if (modeChooserEl) modeChooserEl.hidden = false;
        const relayBtn = document.getElementById('modeChooserRelay');
        const directBtn = document.getElementById('modeChooserDirect');
        const choose = (mode) => {
            const s = loadServerSettings();
            s.mode = mode;
            s.modeChosen = true;
            saveServerSettings(s);
            if (modeChooserOverlay) modeChooserOverlay.hidden = true;
            if (modeChooserEl) modeChooserEl.hidden = true;
            resolve(mode);
        };
        if (relayBtn) relayBtn.onclick = () => choose('relay');
        if (directBtn) directBtn.onclick = () => choose('direct');
    });
}

function clearConnectionModeChoice() {
    const s = loadServerSettings();
    s.mode = null;
    s.modeChosen = false;
    saveServerSettings(s);
}

function versionParts(version) {
    const raw = String(version || '').trim().replace(/^v/i, '');
    if (!raw) return null;
    const parts = raw.split(/[.+_-]/).map((part) => {
        const match = part.match(/^\d+/);
        return match ? Number(match[0]) : 0;
    });
    if (!parts.length || parts.every((part) => part === 0) && !/^0(?:[.+_-]0)*$/.test(raw)) {
        return null;
    }
    while (parts.length < 3) parts.push(0);
    return parts;
}

function compareVersions(a, b) {
    const left = versionParts(a);
    const right = versionParts(b);
    if (!left || !right) return 0;
    const len = Math.max(left.length, right.length);
    for (let i = 0; i < len; i++) {
        const diff = (left[i] || 0) - (right[i] || 0);
        if (diff !== 0) return diff < 0 ? -1 : 1;
    }
    return 0;
}

function getClientUpdateState(mode = getConnectionMode()) {
    if (!relayAvailable || mode !== 'relay') {
        return { needed: false, forced: false };
    }
    const current = clientVersion || '0.1.0';
    const latest = clientLatestVersion || '';
    const minimum = clientMinimumVersion || '';
    const belowMinimum = !!minimum && compareVersions(current, minimum) < 0;
    const belowLatest = !!latest && compareVersions(current, latest) < 0;
    return {
        needed: belowMinimum || belowLatest,
        forced: belowMinimum,
        current,
        latest: latest || minimum || '',
        minimum,
        updateUrl: clientUpdateUrl,
        notes: clientUpdateNotes,
    };
}

let clientUpdateResolve = null;

function closeClientUpdateDialog(result) {
    if (clientUpdateOverlay) clientUpdateOverlay.hidden = true;
    if (clientUpdateDialog) clientUpdateDialog.hidden = true;
    if (clientUpdateResolve) {
        const resolve = clientUpdateResolve;
        clientUpdateResolve = null;
        resolve(result);
    }
}

function showClientUpdateDialog(state) {
    if (!clientUpdateDialog || !clientUpdateOverlay) {
        if (state.forced) {
            return Promise.resolve('direct');
        }
        return Promise.resolve('later');
    }
    if (clientUpdateResolve) {
        closeClientUpdateDialog('later');
    }
    if (clientUpdateTitle) clientUpdateTitle.textContent = t(state.forced ? 'client_update_title_required' : 'client_update_title_optional');
    if (clientUpdateBody) clientUpdateBody.textContent = t(state.forced ? 'client_update_body_required' : 'client_update_body_optional');
    if (clientUpdateCurrentLabel) clientUpdateCurrentLabel.textContent = t('client_update_current');
    if (clientUpdateLatestLabel) clientUpdateLatestLabel.textContent = t('client_update_latest');
    if (clientUpdateMinimumLabel) clientUpdateMinimumLabel.textContent = t('client_update_minimum');
    if (clientUpdateCurrent) clientUpdateCurrent.textContent = state.current || '—';
    if (clientUpdateLatest) clientUpdateLatest.textContent = state.latest || '—';
    if (clientUpdateMinimum) clientUpdateMinimum.textContent = state.minimum || '—';
    if (clientUpdateNotesEl) {
        clientUpdateNotesEl.textContent = state.notes || '';
        clientUpdateNotesEl.hidden = !state.notes;
    }
    if (clientUpdateNoUrl) {
        clientUpdateNoUrl.textContent = state.updateUrl ? '' : t('client_update_no_url');
        clientUpdateNoUrl.hidden = !!state.updateUrl;
    }
    if (clientUpdateButton) {
        clientUpdateButton.textContent = t('client_update_button');
        clientUpdateButton.disabled = !state.updateUrl;
        clientUpdateButton.onclick = () => {
            if (state.updateUrl) {
                window.open(state.updateUrl, '_blank', 'noopener,noreferrer');
            }
        };
    }
    if (clientUpdateLaterButton) {
        clientUpdateLaterButton.textContent = t('client_update_later');
        clientUpdateLaterButton.hidden = !!state.forced;
        clientUpdateLaterButton.onclick = () => closeClientUpdateDialog('later');
    }
    if (clientUpdateDirectButton) {
        clientUpdateDirectButton.textContent = t('client_update_direct');
        clientUpdateDirectButton.hidden = !state.forced;
        clientUpdateDirectButton.onclick = () => closeClientUpdateDialog('direct');
    }
    clientUpdateOverlay.hidden = false;
    clientUpdateDialog.hidden = false;
    return new Promise((resolve) => {
        clientUpdateResolve = resolve;
    });
}

function switchToDirectModeForUpdate() {
    const s = loadServerSettings();
    s.mode = 'direct';
    s.modeChosen = true;
    saveServerSettings(s);
    setModeRadio('direct');
    applyModeSectionsVisibility('direct');
    updateAccountSection();
}

async function ensureHostedVersionAllowed({ candidateMode = null } = {}) {
    const mode = candidateMode || getConnectionMode();
    const state = getClientUpdateState(mode);
    if (!state.needed) {
        return true;
    }
    // Throttle optional (non-forced) update reminders so we don't nag on every page load.
    if (!state.forced) {
        let lastShown = 0;
        try {
            lastShown = parseInt(localStorage.getItem(CLIENT_UPDATE_REMINDER_KEY) || '0', 10) || 0;
        } catch (_) { /* ignore */ }
        const elapsed = Date.now() - lastShown;
        if (lastShown && elapsed < CLIENT_UPDATE_REMINDER_MIN_INTERVAL_MS) {
            return true;
        }
    }
    const action = await showClientUpdateDialog(state);
    if (!state.forced) {
        try {
            localStorage.setItem(CLIENT_UPDATE_REMINDER_KEY, String(Date.now()));
        } catch (_) { /* ignore */ }
    }
    if (state.forced && action === 'direct') {
        switchToDirectModeForUpdate();
        return false;
    }
    return !state.forced;
}

async function returnToModeChooser() {
    if (lockManualControls || !relayAvailable) {
        return;
    }
    clearConnectionModeChoice();
    pushedOverrideBootId = null;
    hideSettingsPanel();
    hideLogin();
    await openModeChooser();
    await ensureHostedVersionAllowed();
    await syncProviderFromStorage();
    maybeForceOpenSettings();
    updateBalanceBarVisibility();
}

async function maybeRunFirstLaunchFlow() {
    if (lockManualControls) {
        return;
    }
    const s = loadServerSettings();
    if ((s.mode === 'relay' || s.mode === 'direct') && hasExplicitConnectionMode(s)) {
        return;
    }
    if (!relayAvailable) {
        // No server configured: implicitly direct mode, no chooser shown.
        s.mode = 'direct';
        s.modeChosen = false;
        saveServerSettings(s);
        return;
    }
    await openModeChooser();
    await ensureHostedVersionAllowed();
}

// ---- Login overlay (VRChat profile proof) ----
function applyLoginI18n() {
    setElText('loginTitle', t('login_title'));
    setElText('loginUserInputLabel', t('login_user_input_label'));
    setElText('loginInputHint', t('login_input_hint'));
    setElText('loginRemoveHint', t('login_remove_hint'));
    setElText('loginCopyButton', t('login_copy'));
    setElText('loginPasteButton', t('login_paste'));
    setElText('loginModeBackButton', t('mode_back_to_chooser'));
    setElText('loginBackButton', t('login_back'));
    setElText('loginBonusLabel', t('login_bonus_label'));
    setElText('loginCodeHintText', t('login_code_hint_text'));
    setElText('loginCodeLink', t('login_code_link'));
    setElText('loginCodeHintSuffix', t('login_code_hint_suffix'));
    setElText('loginConfirmHint', t('login_confirm_identity'));
    setElText('loginMethodLabel', t('login_choose_method'));
    const serverHint = document.getElementById('loginServerHint');
    if (serverHint) serverHint.textContent = relayServerUrl ? t('login_server', { url: relayServerUrl }) : '';
    const codeHint = document.getElementById('loginCodeHint');
    if (codeHint) codeHint.hidden = !relayServerUrl;
    // Re-render any visible method picker so its labels follow the language.
    if (loginMethods.length) renderLoginMethods();
    renderLinkReuseHint(loginMethod);
}

// Show the "delete your old login link first" hint on the challenge step, but
// only when link is the active method and the server flagged that this profile
// still has a stale login link occupying one of its (full) link slots.
function renderLinkReuseHint(method) {
    const el = document.getElementById('loginLinkReuseHint');
    if (!el) return;
    const reuse = method === 'link' && loginProfile && loginProfile.recommended_link_reuse;
    el.textContent = reuse ? t('login_link_reuse_hint') : '';
}

// Localized labels/hints per verification method, mirroring the web user UI.
const LOGIN_METHOD_LABEL_KEYS = {
    bio: 'login_method_bio',
    link: 'login_method_link',
    status: 'login_method_status',
};
const LOGIN_METHOD_HINT_KEYS = {
    bio: 'login_challenge_hint',
    link: 'login_challenge_hint_link',
    status: 'login_challenge_hint_status',
};

function loginPrimaryLabel(step) {
    if (step === 'challenge') return t('login_check');
    if (step === 'method') return t('login_start');
    return t('login_next');
}

function setLoginStep(step) {
    const inputStep = document.getElementById('loginStepInput');
    const methodStep = document.getElementById('loginStepMethod');
    const challengeStep = document.getElementById('loginStepChallenge');
    if (inputStep) inputStep.hidden = (step !== 'input');
    if (methodStep) methodStep.hidden = (step !== 'method');
    if (challengeStep) challengeStep.hidden = (step !== 'challenge');
    if (loginBackButton) loginBackButton.hidden = (step === 'input');
    if (loginPrimaryButton) loginPrimaryButton.textContent = loginPrimaryLabel(step);
    loginForm && loginForm.setAttribute('data-step', step);
}

function setLoginBusy(busy) {
    if (!loginPrimaryButton) return;
    loginPrimaryButton.disabled = busy;
    if (!busy) loginPrimaryButton.textContent = loginPrimaryLabel(loginForm && loginForm.getAttribute('data-step'));
}

function resetLoginToInput() {
    stopLoginCountdown();
    loginChallengeId = '';
    loginProfile = null;
    if (loginErrorEl) loginErrorEl.textContent = '';
    setLoginStep('input');
}

function stopLoginCountdown() {
    if (loginExpiryTimer) {
        clearInterval(loginExpiryTimer);
        loginExpiryTimer = null;
    }
}

function startLoginCountdown(expiresAtIso) {
    stopLoginCountdown();
    const expiresAt = Date.parse(expiresAtIso);
    const expiryHint = document.getElementById('loginExpiryHint');
    const tick = () => {
        const remaining = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
        if (expiryHint) expiryHint.textContent = t('login_expiry', { seconds: remaining });
        if (remaining <= 0) {
            stopLoginCountdown();
            if (loginErrorEl) loginErrorEl.textContent = t('login_expired');
            resetLoginToInput();
        }
    };
    tick();
    loginExpiryTimer = setInterval(tick, 1000);
}

async function fetchRegistrationInfo() {
    const section = document.getElementById('loginBonusSection');
    try {
        const resp = await fetch('/account/registration-info');
        if (!resp.ok) {
            if (section) section.hidden = true;
            return;
        }
        loginRegistrationInfo = await resp.json();
        renderBonusLadder(null);
    } catch (e) {
        if (section) section.hidden = true;
    }
}

function rankLabel(rank) {
    return String(rank || '').replace(/_/g, ' ');
}

function renderBonusLadder(yourRank) {
    const section = document.getElementById('loginBonusSection');
    const list = document.getElementById('loginBonusList');
    const thresholdHint = document.getElementById('loginThresholdHint');
    if (!section || !list) return;
    const info = loginRegistrationInfo;
    const bonuses = (info && Array.isArray(info.bonuses)) ? info.bonuses : [];
    list.innerHTML = '';
    if (!bonuses.length) {
        section.hidden = false;
        const p = document.createElement('div');
        p.className = 'bonus-row';
        p.textContent = t('login_bonus_none');
        list.appendChild(p);
    } else {
        section.hidden = false;
        bonuses.forEach((b) => {
            const row = document.createElement('div');
            const isYours = yourRank && String(b.trust_rank).toLowerCase() === String(yourRank).toLowerCase();
            row.className = isYours ? 'bonus-row bonus-yours' : 'bonus-row';
            const key = isYours ? 'login_bonus_yours' : 'login_bonus_row';
            row.textContent = t(key, { rank: rankLabel(b.trust_rank), credits: b.grant_credits });
            list.appendChild(row);
        });
    }
    const freeNote = document.getElementById('loginFreeQuotaNote');
    if (freeNote) {
        // Some models grant periodic free quota to some ranks beyond the sign-up bonus.
        freeNote.hidden = false;
        freeNote.textContent = t('login_free_quota_note');
    }
    if (thresholdHint) {
        const threshold = info && info.registration_threshold;
        if (threshold) {
            thresholdHint.hidden = false;
            thresholdHint.textContent = t('login_threshold', { rank: rankLabel(threshold) });
        } else {
            thresholdHint.hidden = true;
            thresholdHint.textContent = '';
        }
    }
}

function openLogin({ forced = false } = {}) {
    if (lockManualControls) return;
    loginForcedOpen = !!forced;
    applyLoginI18n();
    resetLoginToInput();
    if (loginUserInput) loginUserInput.value = '';
    void fetchRegistrationInfo();
    if (loginOverlay) loginOverlay.hidden = false;
    if (loginPanel) loginPanel.hidden = false;
    if (loginCloseButton) loginCloseButton.style.display = loginForcedOpen ? 'none' : '';
    if (loginModeBackButton) loginModeBackButton.hidden = !(loginForcedOpen && relayAvailable);
}

function hideLogin() {
    stopLoginCountdown();
    if (loginOverlay) loginOverlay.hidden = true;
    if (loginPanel) loginPanel.hidden = true;
    loginForcedOpen = false;
}

function closeLogin() {
    if (loginForcedOpen) return;
    hideLogin();
}

/** A one-time login code is a 24-char URL-safe token (randomToken(18) on the
 *  server). Distinguish it from a VRChat user id / profile URL / display name so
 *  the same input box can accept either. */
function looksLikeLoginCode(value) {
    return /^[A-Za-z0-9_-]{20,40}$/.test(value) && !/^usr_/i.test(value);
}

// Step 1 "Next": redeem a one-time login code, or resolve a VRChat profile.
async function handleLoginInput() {
    const raw = (loginUserInput && loginUserInput.value || '').trim();
    if (!raw) {
        if (loginErrorEl) loginErrorEl.textContent = t('login_user_input_label');
        return;
    }
    setLoginBusy(true);
    if (loginErrorEl) loginErrorEl.textContent = '';
    try {
        if (looksLikeLoginCode(raw)) {
            const outcome = await tryLoginCode(raw);
            // 'notcode' falls through to VRChat resolution (it may be a display name).
            if (outcome !== 'notcode') return;
        }
        await resolveIdentity(raw);
    } catch (e) {
        if (loginErrorEl) loginErrorEl.textContent = String(e);
    } finally {
        setLoginBusy(false);
    }
}

async function tryLoginCode(code) {
    const resp = await fetch('/account/login-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data && data.success && data.api_key) {
        await onLoginSuccess(data);
        return 'success';
    }
    if (resp.status === 404) return 'notcode'; // unknown code — maybe it's a display name
    if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, data);
    return 'error';
}

async function resolveIdentity(raw) {
    const resp = await fetch('/account/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_input: raw }),
    });
    const profile = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, profile);
        return;
    }
    loginProfile = profile;
    if (!loginMethods.length) {
        try {
            const mResp = await fetch('/account/methods');
            const mData = await mResp.json().catch(() => ({}));
            loginMethods = (mData && Array.isArray(mData.methods) && mData.methods.length) ? mData.methods : ['bio'];
        } catch (e) {
            loginMethods = ['bio'];
        }
    }
    // Prefer the server's recommended method for this profile, if it's enabled.
    const rec = profile && profile.recommended_method;
    if (rec && loginMethods.includes(rec)) loginMethod = rec;
    else if (!loginMethods.includes(loginMethod)) loginMethod = loginMethods[0];
    renderLoginProfile(profile);
    renderLoginMethods();
    renderBonusLadder(profile.trust_rank);
    setLoginStep('method');
}

function renderLoginProfile(p) {
    setElText('loginProfileName', (p && p.display_name) || '');
    setElText('loginProfileId', (p && p.vrc_user_id) || '');
    setElText('loginProfileRank', rankLabel((p && p.trust_rank) || ''));
}

function renderLoginMethods() {
    if (!loginMethodList) return;
    loginMethodList.innerHTML = '';
    const methodLabel = document.getElementById('loginMethodLabel');
    // Hide the picker entirely when the server offers a single method.
    if (methodLabel) methodLabel.hidden = (loginMethods.length <= 1);
    if (loginMethods.length <= 1) return;
    loginMethods.forEach((m) => {
        const option = document.createElement('label');
        option.className = 'method-option' + (m === loginMethod ? ' method-selected' : '');
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'loginMethod';
        radio.value = m;
        radio.checked = (m === loginMethod);
        radio.addEventListener('change', () => { loginMethod = m; renderLoginMethods(); });
        const span = document.createElement('span');
        let label = t(LOGIN_METHOD_LABEL_KEYS[m] || LOGIN_METHOD_LABEL_KEYS.bio);
        if (loginProfile && loginProfile.recommended_method === m) label += ' (' + t('login_method_recommended') + ')';
        span.textContent = label;
        option.appendChild(radio);
        option.appendChild(span);
        loginMethodList.appendChild(option);
    });
}

// Step 2 "Start verification": request a challenge for the chosen method.
async function startVerify() {
    if (!loginProfile) { resetLoginToInput(); return; }
    setLoginBusy(true);
    if (loginErrorEl) loginErrorEl.textContent = '';
    try {
        const resp = await fetch('/account/verify/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vrc_user_id: loginProfile.vrc_user_id, method: loginMethod }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, data);
            return;
        }
        loginChallengeId = data.challenge_id || '';
        const method = data.method || loginMethod;
        const challengeText = document.getElementById('loginChallengeText');
        if (challengeText) challengeText.textContent = data.text || '';
        const challengeHint = document.getElementById('loginChallengeHint');
        if (challengeHint) challengeHint.textContent = t(LOGIN_METHOD_HINT_KEYS[method] || LOGIN_METHOD_HINT_KEYS.bio);
        renderLinkReuseHint(method);
        setLoginStep('challenge');
        if (data.expires_at) startLoginCountdown(data.expires_at);
    } catch (e) {
        if (loginErrorEl) loginErrorEl.textContent = String(e);
    } finally {
        setLoginBusy(false);
    }
}

// Shared on successful sign-in (via verification or a one-time login code).
async function onLoginSuccess(data) {
    const server = loadServerSettings();
    server.mode = 'relay';
    server.modeChosen = true;
    server.token = data.api_key;
    server.displayName = data.display_name || '';
    server.trustRank = data.trust_rank || '';
    saveServerSettings(server);
    renderBonusLadder(data.trust_rank);
    // Persist provider override and start a relay session.
    const settings = loadProviderSettings();
    const provider = settings.providerOverride || translationProvider || 'soniox';
    await pushSetup(provider, null, { silent: true, mode: 'relay', token: data.api_key });
    showToast(t('login_success', { name: server.displayName || data.display_name || '' }));
    hideLogin();
    updateBalanceBarVisibility();
    void fetchBalance();
    clearSubtitleState();
}

async function checkVerification() {
    if (!loginChallengeId) {
        resetLoginToInput();
        return;
    }
    if (loginPrimaryButton) { loginPrimaryButton.disabled = true; loginPrimaryButton.textContent = t('login_checking'); }
    if (loginErrorEl) loginErrorEl.textContent = '';
    try {
        const resp = await fetch('/account/verify/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ challenge_id: loginChallengeId }),
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data && data.success && data.api_key) {
            await onLoginSuccess(data);
            return;
        }
        if (resp.ok) {
            // success=false: the verification string was not found yet.
            if (loginErrorEl) loginErrorEl.textContent = t('login_not_verified');
            return;
        }
        if (resp.status === 410 || resp.status === 409) {
            if (loginErrorEl) loginErrorEl.textContent = t('login_expired');
            resetLoginToInput();
            return;
        }
        if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, data);
    } catch (e) {
        if (loginErrorEl) loginErrorEl.textContent = String(e);
    } finally {
        if (loginPrimaryButton) { loginPrimaryButton.disabled = false; loginPrimaryButton.textContent = loginPrimaryLabel(loginForm && loginForm.getAttribute('data-step')); }
    }
}

function mapVerifyError(status, data) {
    if (status === 429) return t('login_rate_limited');
    if (status === 403) {
        const threshold = (loginRegistrationInfo && loginRegistrationInfo.registration_threshold) || '';
        if (threshold) return t('login_threshold', { rank: rankLabel(threshold) });
    }
    const msg = data && (data.detail || data.message);
    return localizeBackendMessage(msg || t('connection_error_try_again'));
}

if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const step = loginForm.getAttribute('data-step');
        if (step === 'challenge') {
            void checkVerification();
        } else if (step === 'method') {
            void startVerify();
        } else {
            void handleLoginInput();
        }
    });
}
if (loginBackButton) {
    loginBackButton.addEventListener('click', () => {
        const step = loginForm && loginForm.getAttribute('data-step');
        if (step === 'challenge') {
            // Back to method selection without discarding the resolved profile.
            stopLoginCountdown();
            loginChallengeId = '';
            if (loginErrorEl) loginErrorEl.textContent = '';
            setLoginStep('method');
        } else {
            resetLoginToInput();
        }
    });
}
if (loginCodeLink) {
    loginCodeLink.addEventListener('click', (event) => {
        event.preventDefault();
        const base = (relayServerUrl || '').replace(/\/+$/, '');
        if (!base) return;
        const url = base + '/app/#/login-code';
        // In the desktop webview, window.open hands the URL to the system browser
        // and returns null. Do NOT fall back to location.href — that would navigate
        // the app's own window. If opening is blocked, copy the URL instead.
        try {
            window.open(url, '_blank', 'noopener,noreferrer');
        } catch (e) {
            if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
            showToast(url);
        }
    });
}
if (loginModeBackButton) {
    loginModeBackButton.addEventListener('click', () => returnToModeChooser());
}
if (loginCloseButton) {
    loginCloseButton.addEventListener('click', () => closeLogin());
}
if (loginOverlay) {
    loginOverlay.addEventListener('click', () => closeLogin());
}
if (loginCopyButton) {
    loginCopyButton.addEventListener('click', async () => {
        const challengeText = document.getElementById('loginChallengeText');
        const text = challengeText ? challengeText.textContent : '';
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            loginCopyButton.textContent = t('login_copied');
            setTimeout(() => { loginCopyButton.textContent = t('login_copy'); }, 1500);
        } catch (e) {
            // Clipboard may be unavailable; selection fallback is the <code> user-select:all.
        }
    });
}
if (loginPasteButton) {
    loginPasteButton.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (loginUserInput) {
                loginUserInput.value = (text || '').trim();
                loginUserInput.focus();
            }
        } catch (e) {
            // Clipboard read may be unavailable/denied; user can paste manually.
        }
    });
}

// ---- Account actions ----
async function handleRedeem() {
    const input = document.getElementById('redeemInput');
    const code = (input && input.value || '').trim();
    if (!code) return;
    try {
        const resp = await fetch('/account/redeem', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data && data.success) {
            if (input) input.value = '';
            showToast(t('account_redeem_success', {
                credits: formatCredits(data.granted_credits),
                balance: formatCredits(data.new_balance),
            }));
            void fetchBalance();
        } else {
            showToast(localizeBackendMessage((data && (data.detail || data.message)) || t('connection_error_try_again')), true);
        }
    } catch (e) {
        showToast(String(e), true);
    }
}

async function handleCopyInvite() {
    if (copyInviteButton) copyInviteButton.disabled = true;
    try {
        const resp = await fetch('/account/invite');
        const data = await resp.json().catch(() => ({}));
        const link = data && data.invite_link;
        if (!resp.ok || !link) {
            showToast(localizeBackendMessage((data && (data.detail || data.message)) || t('account_invite_failed')), true);
            return;
        }
        try {
            await navigator.clipboard.writeText(link);
            showToast(t('account_invite_copied'));
        } catch (e) {
            // Clipboard may be unavailable; show the link so the user can copy it manually.
            showToast(link);
        }
    } catch (e) {
        showToast(String(e), true);
    } finally {
        if (copyInviteButton) copyInviteButton.disabled = false;
    }
}

async function handleOpenUserWeb() {
    if (openUserWebButton) openUserWebButton.disabled = true;
    try {
        const resp = await fetch('/account/web-login-url');
        const data = await resp.json().catch(() => ({}));
        const url = data && data.url;
        if (!resp.ok || !url) {
            showToast(localizeBackendMessage((data && (data.detail || data.message)) || t('account_open_web_failed')), true);
            return;
        }
        // In the desktop webview, window.open hands the URL to the system browser
        // and returns null. Do NOT fall back to location.href — that would navigate
        // the app's own window. If opening is blocked, copy the URL instead.
        try {
            window.open(url, '_blank', 'noopener,noreferrer');
        } catch (e) {
            if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
            showToast(url);
        }
    } catch (e) {
        showToast(String(e), true);
    } finally {
        if (openUserWebButton) openUserWebButton.disabled = false;
    }
}

async function handleLogout() {
    const confirmed = await showConfirm(t('account_logout_confirm'), {
        okLabel: t('account_logout'),
        cancelLabel: t('cancel'),
        danger: true,
    });
    if (!confirmed) return;
    try {
        await fetch('/account/logout', { method: 'POST' });
    } catch (e) {
        // ignore
    }
    const server = loadServerSettings();
    server.token = '';
    server.displayName = '';
    server.trustRank = '';
    saveServerSettings(server);
    backendLoggedIn = false;
    pushedOverrideBootId = null;
    updateAccountSection();
    updateBalanceBarVisibility();
    hideSettingsPanel();
    openLogin({ forced: true });
}

// ---- Balance bar + this-session cost ----
function formatCredits(value) {
    if (value === null || value === undefined) return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return (Math.round(n * 100) / 100).toString();
}

function balanceBarShouldShow() {
    return getConnectionMode() === 'relay' && (backendLoggedIn || !!loadServerSettings().token);
}

function updateBalanceBarVisibility() {
    if (!balanceBar) return;
    if (balanceBarShouldShow()) {
        balanceBar.hidden = false;
        startBalancePolling();
    } else {
        balanceBar.hidden = true;
        stopBalancePolling();
    }
}

function startBalancePolling() {
    void fetchBalance();
    if (!balancePollTimer) {
        balancePollTimer = setInterval(fetchBalance, 45000);
    }
}

function stopBalancePolling() {
    if (balancePollTimer) {
        clearInterval(balancePollTimer);
        balancePollTimer = null;
    }
}

async function fetchBalance() {
    if (!balanceBarShouldShow()) return;
    try {
        const resp = await fetch('/account/balance');
        if (!resp.ok) return;
        const data = await resp.json();
        pricePerSecond = Number(data.price_per_second) || 0;
        renderBalance(data);
    } catch (e) {
        // ignore transient errors
    }
}

// Localized short label for a free pool ("免费(日)" etc).
function freePoolLabel(period) {
    if (period === 'weekly') return t('balance_free_week');
    if (period === 'monthly') return t('balance_free_month');
    return t('balance_free_day');
}

// Compact short label for a pool period, for one-line summaries ("日"/"周"/"月").
function freePoolPeriodShort(period) {
    if (period === 'weekly') return t('free_period_week');
    if (period === 'monthly') return t('free_period_month');
    return t('free_period_day');
}

// One-line summary of a model's free pools (caps), e.g. "日 100 / 周 500 / 月 ∞".
// Returns '' when there are no configured pools.
function freePoolsSummary(pools) {
    if (!Array.isArray(pools) || !pools.length) return '';
    return pools
        .map((p) => `${freePoolPeriodShort(p.period)} ${p.unlimited ? t('balance_free_unlimited') : formatCredits(p.max_credits)}`)
        .join(' / ');
}

// Value text for one free pool: "剩 X / Y" or "无限".
function freePoolValue(pool) {
    if (pool.unlimited) return t('balance_free_unlimited');
    return t('balance_free_remaining', {
        remaining: formatCredits(pool.remaining),
        cap: formatCredits(pool.max_credits),
    });
}

// Render every configured free pool (daily/weekly/monthly) as balance items into
// `container`. Pools the model doesn't offer are simply absent from the list.
function renderFreePools(container, pools) {
    if (!container) return;
    container.innerHTML = '';
    if (!Array.isArray(pools) || !pools.length) return;
    for (const pool of pools) {
        const item = document.createElement('span');
        item.className = 'balance-item';
        const label = document.createElement('span');
        label.className = 'balance-label';
        label.textContent = freePoolLabel(pool.period);
        const value = document.createElement('span');
        value.className = 'balance-value';
        value.textContent = freePoolValue(pool);
        item.append(label, value);
        container.appendChild(item);
    }
}

function hasPositiveCredits(value) {
    return Number(value || 0) > 0;
}

function hasUsableFreePool(free) {
    const pools = free && Array.isArray(free.pools) ? free.pools : [];
    return pools.some((pool) => pool.unlimited || Number(pool.remaining || 0) > 0);
}

function hasUsableSubscription(subscriptions) {
    return Array.isArray(subscriptions) && subscriptions.some((sub) => Number(sub.remaining_credits || 0) > 0);
}

function isAccountExhausted(data) {
    if (!data) return false;
    return !hasPositiveCredits(data.prepaid_balance)
        && !hasUsableFreePool(data.free)
        && !hasUsableSubscription(data.subscriptions);
}

function renderBalance(data) {
    lastBalanceData = data;
    setElText('balanceLabel', t('balance_label'));
    setElText('balanceValue', formatCredits(data.prepaid_balance));
    setElText('sessionLabel', t('balance_session'));
    if (balanceOpenSettingsButton) {
        balanceOpenSettingsButton.textContent = t('open_settings');
    }
    if (balanceActionItem) {
        balanceActionItem.hidden = !isAccountExhausted(data);
    }

    // Free quota: one item per configured pool (daily/weekly/monthly).
    renderFreePools(document.getElementById('freePools'), data.free && data.free.pools);

    // Mirror the balance into the account panel if it's open.
    updateAccountBalance();

    // Subscription quota: show remaining for the first active plan, if any.
    const subItem = document.getElementById('subItem');
    if (subItem) {
        const subs = Array.isArray(data.subscriptions) ? data.subscriptions : [];
        if (subs.length) {
            const sub = subs[0];
            subItem.hidden = false;
            setElText('subLabel', t('balance_subscription'));
            setElText('subValue', t('balance_free_remaining', {
                remaining: formatCredits(sub.remaining_credits),
                cap: formatCredits(sub.quota_credits),
            }));
        } else {
            subItem.hidden = true;
        }
    }

    updateSessionCostDisplay();
}

function sessionElapsedMs() {
    let total = sessionAccumMs;
    if (sessionRunSince != null) {
        total += (Date.now() - sessionRunSince);
    }
    return total;
}

function formatSessionCost(cost, pricePerSecond) {
    const p = Number(pricePerSecond);
    if (!Number.isFinite(p) || p <= 0) {
        return formatCredits(cost);
    }
    const roundedCost = Math.round(cost / p) * p;
    const priceStr = p.toString();
    const dotIdx = priceStr.indexOf('.');
    const decimals = dotIdx >= 0 ? priceStr.length - dotIdx - 1 : 0;
    return Number(roundedCost.toFixed(Math.max(decimals, 0))).toString();
}

function updateSessionCostDisplay() {
    const cost = (sessionElapsedMs() / 1000) * pricePerSecond;
    setElText('sessionValue', formatSessionCost(cost, pricePerSecond));
}

function sessionCostResume() {
    if (getConnectionMode() !== 'relay') return;
    if (sessionRunSince == null) {
        sessionRunSince = Date.now();
    }
    if (!sessionCostTimer) {
        sessionCostTimer = setInterval(updateSessionCostDisplay, 1000);
    }
    updateSessionCostDisplay();
}

function sessionCostPause() {
    if (sessionRunSince != null) {
        sessionAccumMs += (Date.now() - sessionRunSince);
        sessionRunSince = null;
    }
    if (sessionCostTimer) {
        clearInterval(sessionCostTimer);
        sessionCostTimer = null;
    }
    updateSessionCostDisplay();
}

function sessionCostReset() {
    const wasRunning = sessionRunSince != null;
    sessionAccumMs = 0;
    sessionRunSince = wasRunning ? Date.now() : null;
    updateSessionCostDisplay();
}

document.addEventListener('DOMContentLoaded', () => {
    (async () => {
        await fetchUiConfig();
        await maybeRunFirstLaunchFlow();
        await ensureHostedVersionAllowed();
        await syncProviderFromStorage();
        await fetchLlmRefineStatus();
        fetchApiKeyStatus();
        fetchOscTranslationStatus();
        maybeForceOpenSettings();
        updateBalanceBarVisibility();
        connect();
    })();
});

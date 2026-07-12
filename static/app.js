// Hook to replace browser native tooltips with a custom instant tooltip
(function() {
    const originalSetAttribute = Element.prototype.setAttribute;
    const originalGetAttribute = Element.prototype.getAttribute;
    const originalRemoveAttribute = Element.prototype.removeAttribute;

    // Convert initial elements with title attributes to data-custom-title
    document.querySelectorAll('[title]').forEach(el => {
        const titleVal = el.getAttribute('title');
        if (titleVal) {
            el.setAttribute('data-custom-title', titleVal);
            el.removeAttribute('title');
        }
    });

    let tooltipEl = null;
    let activeTooltipTarget = null;

    function createTooltip() {
        tooltipEl = document.createElement('div');
        tooltipEl.className = 'custom-tooltip';
        document.body.appendChild(tooltipEl);
    }

    function updateTooltipText(text) {
        if (!tooltipEl) createTooltip();
        if (!text) {
            hideTooltip();
            return;
        }
        tooltipEl.textContent = text;
        if (activeTooltipTarget) {
            positionTooltip(activeTooltipTarget);
        }
    }

    function showTooltip(target) {
        const text = target.getAttribute('data-custom-title');
        if (!text) return;
        if (!tooltipEl) createTooltip();
        tooltipEl.textContent = text;
        tooltipEl.classList.add('visible');
        positionTooltip(target);
    }

    function hideTooltip() {
        if (tooltipEl) {
            tooltipEl.classList.remove('visible');
        }
        activeTooltipTarget = null;
    }

    function positionTooltip(target) {
        if (!tooltipEl) return;
        const rect = target.getBoundingClientRect();
        const margin = 8;
        const gap = 8;
        const maxTooltipWidth = 350;

        tooltipEl.style.left = '0px';
        tooltipEl.style.top = '0px';
        tooltipEl.style.maxWidth = '';

        const leftSpace = Math.max(0, rect.left - margin - gap);
        const rightSpace = Math.max(0, window.innerWidth - rect.right - margin - gap);
        const placeLeft = leftSpace >= rightSpace;
        const availableWidth = placeLeft ? leftSpace : rightSpace;
        tooltipEl.style.maxWidth = Math.max(1, Math.min(maxTooltipWidth, availableWidth)) + 'px';

        let left;
        if (placeLeft) {
            const tooltipWidth = tooltipEl.offsetWidth;
            left = rect.left - tooltipWidth - gap;
            if (left < margin) left = margin;
        } else {
            const tooltipWidth = tooltipEl.offsetWidth;
            left = rect.right + gap;
            if (left + tooltipWidth > window.innerWidth - margin) {
                left = window.innerWidth - tooltipWidth - margin;
            }
        }

        const tooltipHeight = tooltipEl.offsetHeight;
        let top = rect.top + (rect.height - tooltipHeight) / 2;
        if (top < margin) top = margin;
        if (top + tooltipHeight > window.innerHeight - margin) {
            top = window.innerHeight - tooltipHeight - margin;
        }

        tooltipEl.style.left = left + 'px';
        tooltipEl.style.top = top + 'px';
    }

    // Define HTMLElement.prototype.title getter/setter
    Object.defineProperty(HTMLElement.prototype, 'title', {
        get: function() {
            return this.getAttribute('data-custom-title') || '';
        },
        set: function(val) {
            if (val) {
                this.setAttribute('data-custom-title', val);
                originalRemoveAttribute.call(this, 'title');
            } else {
                this.removeAttribute('data-custom-title');
                originalRemoveAttribute.call(this, 'title');
            }
            if (activeTooltipTarget === this) {
                updateTooltipText(val);
            }
        }
    });

    // Override Element methods
    Element.prototype.setAttribute = function(name, value) {
        if (name && name.toLowerCase() === 'title') {
            this.setAttribute('data-custom-title', value);
            originalRemoveAttribute.call(this, 'title');
            if (activeTooltipTarget === this) {
                updateTooltipText(value);
            }
        } else {
            originalSetAttribute.call(this, name, value);
        }
    };

    Element.prototype.getAttribute = function(name) {
        if (name && name.toLowerCase() === 'title') {
            return this.getAttribute('data-custom-title') || '';
        }
        return originalGetAttribute.call(this, name);
    };

    Element.prototype.removeAttribute = function(name) {
        if (name && name.toLowerCase() === 'title') {
            this.removeAttribute('data-custom-title');
            originalRemoveAttribute.call(this, 'title');
            if (activeTooltipTarget === this) {
                hideTooltip();
            }
        } else {
            originalRemoveAttribute.call(this, name);
        }
    };

    // Event listeners
    document.addEventListener('mouseover', (e) => {
        const target = e.target.closest('[data-custom-title]');
        if (target) {
            if (activeTooltipTarget === target) return;
            activeTooltipTarget = target;
            showTooltip(target);
        } else {
            if (activeTooltipTarget) {
                hideTooltip();
            }
        }
    }, { passive: true });

    document.addEventListener('mouseout', (e) => {
        if (activeTooltipTarget && !activeTooltipTarget.contains(e.relatedTarget)) {
            hideTooltip();
        }
    }, { passive: true });

    document.addEventListener('click', (e) => {
        if (activeTooltipTarget && activeTooltipTarget.contains(e.target)) {
            const target = activeTooltipTarget;
            // Delay check to run after the click's DOM updates (e.g. changing title, disabling, or removing the button)
            setTimeout(() => {
                if (activeTooltipTarget === target) {
                    const rect = target.getBoundingClientRect();
                    const isVisible = rect.width > 0 && rect.height > 0 && document.body.contains(target);
                    const text = target.getAttribute('data-custom-title');
                    if (isVisible && text) {
                        updateTooltipText(text);
                    } else {
                        hideTooltip();
                    }
                }
            }, 0);
        }
    }, { passive: true });
})();

let ws;
let wsClient = null;
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
        'Speaker label switching is disabled by server config': 'backend_speaker_labels_disabled',
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

const INITIAL_UI_CONFIG = (window.__INITIAL_UI_CONFIG__ && typeof window.__INITIAL_UI_CONFIG__ === 'object')
    ? window.__INITIAL_UI_CONFIG__
    : {};

// 由后端下发：锁定“手动控制”相关 UI
let lockManualControls = !!INITIAL_UI_CONFIG.lock_manual_controls;

// 由后端下发：LLM 译文修复能力是否可用（缺少 API key 时为 false）
let llmRefineAvailable = false;

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
const bundledCjkFontPickerHost = document.getElementById('bundledCjkFontPicker');
const runtimeControlsSection = document.getElementById('runtimeControlsSection');
const autoRestartPickerHost = document.getElementById('autoRestartPicker');
const speakerLabelsSettingField = document.getElementById('speakerLabelsSettingField');
const speakerLabelsPickerHost = document.getElementById('speakerLabelsPicker');
const segmentModeSettingField = document.getElementById('segmentModeSettingField');
const segmentModePickerHost = document.getElementById('segmentModePicker');
const translationModeSection = document.getElementById('translationModeSection');
const translationModeSettingField = document.getElementById('translationModeSettingField');
const translationModePickerHost = document.getElementById('translationModePicker');
const translationModeHintEl = document.getElementById('translationModeHint');
const toastEl = document.getElementById('toast');

const SONIOX_REGIONS = ['us', 'eu', 'jp'];
// Custom-select element (built lazily); mirrors the language picker styling.
let sonioxRegionPickerEl = null;
let microphoneDevicePickerEl = null;
let autoRestartPickerEl = null;
let speakerLabelsPickerEl = null;
let segmentModePickerEl = null;
let translationModePickerEl = null;
let bundledCjkFontPickerEl = null;
let microphoneDeviceData = { available: false, default: null, devices: [], selected_id: '' };

// Where users obtain an API key for each provider (shown as a link in Settings).
const PROVIDER_KEY_URLS = {
    soniox: 'https://console.soniox.com/api-keys',
    gemini: 'https://aistudio.google.com/apikey',
};

const PROVIDER_SETTINGS_STORAGE_KEY = 'providerSettings.v1';
const UI_TRANSLATION_MODE_STORAGE_KEY = 'uiTranslationMode';
const BUNDLED_CJK_FONT_STORAGE_KEY = 'useBundledCjkFont';

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
let useBundledCjkFont = localStorage.getItem(BUNDLED_CJK_FONT_STORAGE_KEY) === 'true';
let customFontAvailable = false;

function applyBundledCjkFontPreference(enabled, { persist = false, sync = false } = {}) {
    useBundledCjkFont = !!enabled;
    document.body.classList.toggle('use-bundled-cjk-fonts', useBundledCjkFont);
    renderBundledCjkFontPicker();
    if (persist) {
        localStorage.setItem(BUNDLED_CJK_FONT_STORAGE_KEY, useBundledCjkFont ? 'true' : 'false');
    }
    if (sync) {
        void syncBundledCjkFontPreference(useBundledCjkFont);
    }
}

async function syncBundledCjkFontPreference(enabled) {
    try {
        await fetch('/subtitle-font', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_bundled_cjk_fonts: !!enabled }),
        });
    } catch (error) {
        console.warn('Failed to sync subtitle font preference:', error);
    }
}

applyBundledCjkFontPreference(useBundledCjkFont, { sync: true });
let toastTimer = null;

// ---- Subtitle-server relay (hosted mode) state ----
const SUBTITLE_SERVER_STORAGE_KEY = 'subtitleServer.v1';
// Optional client-update reminders throttle: at least this many ms between popups.
const CLIENT_UPDATE_REMINDER_MIN_INTERVAL_MS = 20 * 60 * 60 * 1000;
const CLIENT_UPDATE_REMINDER_KEY = 'clientUpdateReminderLastShown';
let relayAvailable = !!INITIAL_UI_CONFIG.relay_available;
let relayServerUrl = typeof INITIAL_UI_CONFIG.server_url === 'string' ? INITIAL_UI_CONFIG.server_url : '';
const settingsStore = SettingsStore.create({
    storage: localStorage,
    getRelayServerUrl: () => relayServerUrl,
});
let creditsPurchaseUrl = '';
let firstRedeemBonusCredits = 0;
let firstRedeemBonusEligible = false;
let clientVersion = '0.1.0';
let clientLatestVersion = '';
let clientMinimumVersion = '';
let clientUpdateUrl = '';
let clientUpdateNotes = '';
let backendMode = 'direct';
let backendLoggedIn = false;
let loginForcedOpen = false;
let relayPricing = null; // { soniox: {price_per_second, free_*}, gemini: {...} }
let loginRegistrationInfo = null; // { bonuses: [...], registration_threshold }
let loginSubmitBusy = false;
let loginWaitingForBrowser = false;
// Balance bar / this-session cost meter.
let balancePollTimer = null;
let balancePollIntervalMs = 0; // cadence the poll timer is currently running at
let sessionCostTimer = null;
let sessionAccumMs = 0;
let sessionRunSince = null;
let pricePerSecond = 0;
// STT billing factor for soniox 准确 mode (built-in translation off), delivered by
// the server via /ui-config. 1 = no discount; applied to the live cost estimate.
let sonioxNoTranslationFactor = 1;
let lastBalanceData = null; // last /account/balance payload (raw), for the account panel
let balanceBaseline = null; // balance payload the live in-flight estimate is subtracted from
// Maps backend relay close-code tags to localized message keys.
const RELAY_ERROR_KEYS = {
    billing_exhausted: 'relay_err_billing_exhausted',
    upstream_key_error: 'relay_err_upstream_key_error',
    forbidden: 'relay_err_forbidden',
    model_not_allowed: 'relay_err_model_not_allowed',
    concurrency_limit: 'relay_err_concurrency_limit',
};

const { safeHttpUrl, normalizeServerUrl } = SettingsStore;

function loadServerSettingsRaw() {
    return settingsStore.loadServerSettingsRaw();
}

function loadServerSettings() {
    return settingsStore.loadServerSettings();
}

function saveServerSettings(settings) {
    settingsStore.saveServerSettings(settings);
}
function hasExplicitConnectionMode(settings) {
    if (!settings || typeof settings !== 'object') {
        return false;
    }
    if (settings.modeChosen === true) {
        return true;
    }
    // A saved direct/own-key mode is also an explicit choice. Older settings may
    // not have modeChosen yet; do not reinterpret them as hosted mode just
    // because a subtitle-server URL is configured.
    if (settings.mode === 'direct') {
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

let uiTranslationMode = settingsStore.loadUiTranslationMode();

function loadProviderSettings() {
    return settingsStore.loadProviderSettings();
}

function saveProviderSettings(settings) {
    settingsStore.saveProviderSettings(settings);
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
    llmRefineMode = 'off';
}
let llmRefineEnabled = llmRefineMode !== 'off';
let llmTranslateHideAfterSequence = llmRefineMode === 'translate' ? 0 : null;

// Unified 翻译模式 (fast/accurate/hybrid/refine). This is the single control in
// Settings; it drives llmRefineMode for display and is pushed to the backend via
// /translation-mode (which owns soniox-translation suppression + billing factor).
const TRANSLATION_UI_MODE_STORAGE_KEY = 'translationUiMode';
// 混合 now shows the STT draft immediately and refines it in place, so it maps
// to the internal 'refine' LLM mode. The old separate 改进 UI mode is gone.
const TRANSLATION_UI_MODES = ['fast', 'accurate', 'hybrid'];
const DEFAULT_TRANSLATION_UI_MODE = 'hybrid';
const TRANSLATION_UI_MODE_TO_LLM = { fast: 'off', accurate: 'translate', hybrid: 'refine' };
let translationModeSynced = false; // pushed the stored mode to the backend once
function readStoredTranslationUiMode() {
    return settingsStore.readTranslationUiMode();
}

function getStoredTranslationUiMode() {
    const stored = readStoredTranslationUiMode();
    if (stored) return stored;
    // Transient default before the backend's authoritative translation_ui_mode
    // (fast/accurate/hybrid) arrives. Default 翻译模式 is 混合 (hybrid).
    return DEFAULT_TRANSLATION_UI_MODE;
}
let translationUiMode = getStoredTranslationUiMode();
// 混合 mode: token-sequence threshold at/after which STT translations are shown
// muted (gray) as provisional, awaiting their LLM replacement. Sentences from
// before switching into 混合 are never grayed (no LLM result is coming for them).
// null = not in 混合. See setTranslationUiMode / clearSubtitleState.
let hybridInterimAfterSequence = translationUiMode === 'hybrid' ? 0 : null;
// Per-request hosted LLM cost reported by the relay, accumulated for this session.
let sessionLlmCost = 0;
let sessionHadLlmCost = false;

// Refine/retraction/speculative-translation caches are private to their state
// module; app.js only coordinates their effects with token and DOM state.
const refineState = RefineState.createRefineState();

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
const subtitleHtmlRenderer = RenderHtml.createRenderHtml({ document, escapeHtml, t });
const subtitleDomPatcher = subtitleHtmlRenderer.createDomPatcher({
    container: subtitleContainer,
    renderedSentences,
    renderedBlocks,
});

const SCROLL_STICKY_THRESHOLD = 50;
let autoStickToBottom = true;
let tokenSequenceCounter = 0;

// 分段模式: 'translation' | 'endpoint' | 'punctuation'
let segmentMode = settingsStore.loadSegmentMode();
const SEGMENT_MODES = ['translation', 'endpoint', 'punctuation'];
if (!SEGMENT_MODES.includes(segmentMode)) {
    segmentMode = 'punctuation';
}

// 显示模式: 'both', 'original', 'translation'
let displayMode = settingsStore.loadDisplayMode();

// 自动重启识别开关（默认开启；已有保存值优先）
let autoRestartEnabled = settingsStore.loadAutoRestartEnabled();

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
let bottomSafeAreaEnabled = settingsStore.loadBottomSafeAreaEnabled();

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

    if (pauseButton) {
        updatePauseButtonUi();
    }

    if (overlayButton) {
        updateOverlayButton();
    }

    if (settingsButton) {
        settingsButton.title = t('settings');
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
    return settingsStore.loadLlmRefineMode();
}

function isLlmTranslateMode() {
    return llmRefineMode === 'translate';
}

function applyLlmRefineMode(mode, options = {}) {
    const normalized = normalizeLlmRefineMode(mode);
    const previous = llmRefineMode;
    const wasTranslate = previous === 'translate';
    llmRefineMode = normalized;
    llmRefineEnabled = llmRefineMode !== 'off';

    const shouldPersist = options.persist !== false;
    if (shouldPersist) {
        if (!settingsStore.saveLlmRefineMode(llmRefineMode)) {
            console.warn('Unable to persist LLM refine mode');
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

function getSegmentModes() {
    return isLlmTranslateMode() ? ['endpoint', 'punctuation'] : SEGMENT_MODES;
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
    if (!displayModeButton) {
        return;
    }

    let nextKey, currentKey;
    if (displayMode === 'both') {
        nextKey = 'display_mode_original';
        currentKey = 'display_mode_both';
    } else if (displayMode === 'original') {
        nextKey = 'display_mode_translation';
        currentKey = 'display_mode_original';
    } else {
        nextKey = 'display_mode_both';
        currentKey = 'display_mode_translation';
    }

    const currentName = t(currentKey);
    const nextName = t(nextKey);
    displayModeButton.title = t('display_mode_format', { current: currentName, next: nextName });
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
        if (data && Number.isFinite(Number(data.soniox_no_translation_factor)) && Number(data.soniox_no_translation_factor) > 0) {
            sonioxNoTranslationFactor = Math.min(1, Number(data.soniox_no_translation_factor));
        }
        // 翻译模式: localStorage is the source of truth when the user has made a
        // choice. Without a saved choice, default to 混合; an unavailable LLM only
        // hides the Settings control and must not poison the later login default.
        translationUiMode = readStoredTranslationUiMode() || DEFAULT_TRANSLATION_UI_MODE;
        if (!availableTranslationModes().includes(translationUiMode)) {
            translationUiMode = DEFAULT_TRANSLATION_UI_MODE;
        }
        renderTranslationModePicker();
        // Backend restarted (new boot id): its in-memory translation mode reset
        // to the default, so the stored mode must be re-pushed.
        if (typeof data.boot_id === 'string' && backendBootId && data.boot_id !== backendBootId) {
            translationModeSynced = false;
        }
        // Push the stored mode to the backend session once per backend boot.
        // restartIfNeeded: the boot stream opens before this push lands, so 准确
        // needs a reopen to actually run soniox with translation disabled (and
        // bill at the reduced factor); needs_restart is false when nothing
        // changed, so ordinary page reloads never trigger a restart here.
        if (llmRefineAvailable && !translationModeSynced) {
            translationModeSynced = true;
            void setTranslationUiMode(translationUiMode, { restartIfNeeded: true });
        }
        if (data && typeof data.llm_refine_default_mode === 'string') {
            defaultLlmRefineMode = normalizeLlmRefineMode(data.llm_refine_default_mode);
        }
        if (data && typeof data.translation_target_lang === 'string' && data.translation_target_lang.trim()) {
            defaultTranslationTargetLang = data.translation_target_lang.trim().toLowerCase();
            currentTranslationTargetLang = defaultTranslationTargetLang;
        }
        if (data && typeof data.provider === 'string' && data.provider.trim()) {
            const oldProvider = translationProvider;
            translationProvider = data.provider.trim().toLowerCase();
            if (translationProvider !== oldProvider) {
                sessionCostReset();
            }
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
        firstRedeemBonusCredits = Math.max(0, Number(data && data.first_redeem_bonus_credits) || 0);
        firstRedeemBonusEligible = false;
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
        if (data && typeof data.custom_font_available === 'boolean') {
            customFontAvailable = data.custom_font_available;
            if (!customFontAvailable) {
                applyBundledCjkFontPreference(false, { persist: false, sync: false });
            }
        }
        renderBundledCjkFontPicker();

        if (data && typeof data.speaker_diarization_enabled === 'boolean') {
            speakerDiarizationEnabled = data.speaker_diarization_enabled;
        }
        if (data && typeof data.hide_speaker_labels === 'boolean') {
            hideSpeakerLabels = data.hide_speaker_labels;
        }
        const storedHideSpeakerLabels = getStoredHideSpeakerLabelsSetting();
        if (!lockManualControls && translationProvider === 'soniox' && storedHideSpeakerLabels !== null) {
            hideSpeakerLabels = storedHideSpeakerLabels;
            if (data && data.hide_speaker_labels !== storedHideSpeakerLabels) {
                void setSpeakerLabelsHidden(storedHideSpeakerLabels);
            }
        }
        applySpeakerLabelVisibility();
        renderRuntimeSettingsPickers();
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
        enforceTranslateSegmentMode();
        return;
    }

    // The unified 翻译模式 sync (fetchUiConfig -> setTranslationUiMode) owns
    // pushing the stored mode now; a second push through the legacy /llm-refine
    // endpoint would race it. Keep this endpoint only for locked (env-driven)
    // setups where the unified sync is skipped.
    if (!lockManualControls) {
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
        applyLlmRefineMode(preferredDefault, { persist: false });
    } catch (error) {
        console.error('Error fetching LLM refine status:', error);
    }
}

function getLlmSentenceId(sentence) {
    return RenderModel.resolveLlmSentenceId(sentence);
}

function getDisplayTranslationForSentence(sentence, source, originalTranslation) {
    const sentenceId = getLlmSentenceId(sentence);
    return (sentenceId && refineState.getRefinedTranslation(sentenceId)) || originalTranslation;
}

function shouldHideBuiltinTranslation(sentence, sourceText, hasRefined) {
    // 准确 (accurate) only: withhold the provider's built-in translation until the
    // LLM translate result arrives, so the user never sees the lower-quality fast
    // translation in this mode. soniox suppresses its built-in entirely (so there
    // are usually no tokens to hide); gemini always translates, so we hide its
    // built-in tokens here. 混合 (hybrid) shows the draft immediately — never hides.
    if (!isLlmTranslateMode()) {
        return false;
    }
    if (!sourceText) {
        return false;
    }
    if (hasRefined) {
        return false;
    }
    return sentenceHasTranslationTokenAtOrAfter(sentence, llmTranslateHideAfterSequence);
}

// True when any of the sentence's translation tokens carries a sequence index
// at or after `threshold`, i.e. it was produced after a mode switch marked at
// that sequence. `threshold === null` means "no marker", so nothing matches.
function sentenceHasTranslationTokenAtOrAfter(sentence, threshold) {
    if (threshold === null || threshold === undefined) {
        return false;
    }
    if (!sentence || !Array.isArray(sentence.translationTokens)) {
        return false;
    }
    return sentence.translationTokens.some((token) => {
        const seq = token && typeof token._sequenceIndex === 'number' ? token._sequenceIndex : null;
        return seq !== null && seq >= threshold;
    });
}

function sentenceHasNonFinalTranslation(sentence) {
    if (!sentence || !Array.isArray(sentence.translationTokens)) {
        return false;
    }
    return sentence.translationTokens.some((token) => token && token.is_final === false);
}

function handleBackendRefineResult(data) {
    if (refineState.applyRefineResult(data, { translateMode: isLlmTranslateMode() })) {
        renderSubtitles();
    }
}

function cleanupSentenceCaches(sentenceId) {
    refineState.cleanupSentenceCaches(sentenceId);
}

function handleSubtitleRetract(data) {
    const sentenceId = data && data.sentence_id ? String(data.sentence_id).trim() : '';
    if (!sentenceId) {
        return;
    }

    cleanupSentenceCaches(sentenceId);

    const removal = refineState.removeSentenceTokens(allFinalTokens, sentenceId);
    if (!removal.removed) {
        return;
    }

    allFinalTokens = removal.tokens;

    lastMergedIndex = Math.max(0, allFinalTokens.length - 1);
    renderedSentences.clear();
    renderedBlocks.clear();
    mergeFinalTokens();
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
    settingsStore.saveUiTranslationMode(mode);
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

const starFilledSvg = `<svg class="star-icon filled" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="currentColor"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>`;
const starEmptySvg = `<svg class="star-icon empty" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>`;

function getFavoriteLanguages() {
    try {
        const stored = localStorage.getItem('favoriteLanguages');
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed)) {
                return parsed.map(c => c.toLowerCase());
            }
        }
    } catch (e) {
        console.warn('Failed to parse favorite languages:', e);
    }
    // Seed with current target languages if not set
    const defaultFavs = [];
    const currentLangs = [
        currentTranslationTargetLang,
        backendTargetLang1,
        backendTargetLang2
    ].filter(Boolean).map(c => c.toLowerCase());

    for (const lang of currentLangs) {
        if (!defaultFavs.includes(lang)) {
            defaultFavs.push(lang);
        }
    }
    // Default seeds in ordered preference: 中 (Simplified), 英, 日, 韩
    const standardSeeds = ['zh-hans', 'zh', 'en', 'ja', 'ko'];
    for (const seed of standardSeeds) {
        if (!defaultFavs.includes(seed)) {
            const supported = SUPPORTED_TRANSLATION_LANGUAGES.some(l => l.code.toLowerCase() === seed);
            if (supported) {
                defaultFavs.push(seed);
            }
        }
    }
    return defaultFavs;
}

function saveFavoriteLanguages(favs) {
    try {
        localStorage.setItem('favoriteLanguages', JSON.stringify(favs));
    } catch (e) {
        console.warn('Failed to save favorite languages:', e);
    }
}

function findLangSelectOption(menu, normalizedCode, section) {
    if (!menu) {
        return null;
    }
    const rows = menu.querySelectorAll('.lang-select-option[data-code]');
    for (const row of rows) {
        if ((row.dataset.code || '').toLowerCase() !== normalizedCode) {
            continue;
        }
        if (section && row.dataset.section !== section) {
            continue;
        }
        return row;
    }
    return null;
}

function toggleFavoriteLanguage(code, anchorRow = null) {
    const normalized = code.toLowerCase();
    let favs = getFavoriteLanguages();
    const index = favs.indexOf(normalized);
    let isFavNow = false;
    if (index > -1) {
        favs.splice(index, 1);
    } else {
        favs.push(normalized);
        isFavNow = true;
    }
    saveFavoriteLanguages(favs);

    if (langSelectMenuEl && !langSelectMenuEl.hidden && activeLangPickerEl) {
        const anchorSection = anchorRow ? anchorRow.dataset.section : null;
        const anchorTop = anchorRow ? anchorRow.getBoundingClientRect().top : null;

        renderLangSelectMenuContent(activeLangPickerEl);

        const updatedAnchor = findLangSelectOption(langSelectMenuEl, normalized, anchorSection);
        if (updatedAnchor && anchorTop !== null) {
            langSelectMenuEl.scrollTop += updatedAnchor.getBoundingClientRect().top - anchorTop;
        }
    }
}

function createOptionRow(lang, picker, section = 'all') {
    const isSelected = lang.code === picker.value;
    const favorited = getFavoriteLanguages().includes(lang.code.toLowerCase());

    const option = document.createElement('div');
    option.className = 'lang-select-option';
    option.dataset.code = lang.code;
    option.dataset.section = section;

    const selectBtn = document.createElement('button');
    selectBtn.type = 'button';
    selectBtn.className = 'lang-select-option-btn';
    if (isSelected) {
        selectBtn.classList.add('selected');
    }
    selectBtn.setAttribute('role', 'option');
    selectBtn.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    selectBtn.textContent = `${lang.en} - ${lang.native}`;
    selectBtn.addEventListener('click', () => {
        setLangPickerValue(picker, lang.code);
        closeLangSelectMenu();
        picker.dispatchEvent(new Event('change'));
    });

    const favBtn = document.createElement('button');
    favBtn.type = 'button';
    favBtn.className = 'lang-favorite-btn';
    favBtn.innerHTML = favorited ? starFilledSvg : starEmptySvg;
    favBtn.title = favorited ? t('unfavorite') : t('favorite');
    favBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleFavoriteLanguage(lang.code, option);
    });

    option.appendChild(selectBtn);
    option.appendChild(favBtn);
    return option;
}

function renderLangSelectMenuContent(picker) {
    const menu = ensureLangSelectMenu();
    menu.innerHTML = '';

    const favCodes = getFavoriteLanguages();
    const favLangs = SUPPORTED_TRANSLATION_LANGUAGES.filter(lang => favCodes.includes(lang.code.toLowerCase()));

    // 1. Favorites section (only show if there is at least one favorite)
    if (favLangs.length > 0) {
        const favHeader = document.createElement('div');
        favHeader.className = 'lang-select-section-title';
        favHeader.textContent = t('favorites_section_title');
        menu.appendChild(favHeader);

        for (const lang of favLangs) {
            const option = createOptionRow(lang, picker, 'favorites');
            menu.appendChild(option);
        }

        const divider = document.createElement('div');
        divider.className = 'lang-select-divider';
        menu.appendChild(divider);

        const allHeader = document.createElement('div');
        allHeader.className = 'lang-select-section-title';
        allHeader.textContent = t('all_languages_section_title');
        menu.appendChild(allHeader);
    }

    // 2. All languages section (always show)
    for (const lang of SUPPORTED_TRANSLATION_LANGUAGES) {
        const option = createOptionRow(lang, picker, 'all');
        menu.appendChild(option);
    }
}

function openLangSelectMenu(picker) {
    const menu = ensureLangSelectMenu();
    activeLangPickerEl = picker;

    renderLangSelectMenuContent(picker);

    picker.classList.add('open');
    const trigger = picker.querySelector('.lang-picker-button');
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'true');
    }
    menu.hidden = false;
    positionLangSelectMenu(picker, menu);

    const selectedOption = menu.querySelector('.lang-select-option-btn.selected');
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
        const target = event && event.target;
        const scrollsPickerAncestor = target && typeof target.contains === 'function' && target.contains(picker);
        const scrollsViewport = target === window
            || target === document
            || target === document.body
            || target === document.documentElement;
        if (scrollsPickerAncestor || scrollsViewport) {
            reposition();
        }
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

function updateAudioSourceButton() {
    if (!audioSourceButton || !audioSourceIcon) {
        return;
    }

    audioSource = normalizeAudioSource(audioSource);

    let nextKey, currentKey;
    if (audioSource === 'microphone') {
        setControlIcon(audioSourceIcon, 'mic');
        nextKey = 'audio_to_mix_val';
        currentKey = 'audio_source_microphone';
    } else if (audioSource === 'mix') {
        setControlIcon(audioSourceIcon, 'blend');
        nextKey = 'audio_to_system_val';
        currentKey = 'audio_source_mix';
    } else {
        setControlIcon(audioSourceIcon, 'volume-2');
        nextKey = 'audio_to_mic_val';
        currentKey = 'audio_source_system';
    }

    const currentName = t(currentKey);
    const nextName = t(nextKey);
    audioSourceButton.title = t('audio_source_format', { current: currentName, next: nextName });
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

function getStoredHideSpeakerLabelsSetting() {
    const settings = loadProviderSettings();
    return typeof settings.hideSpeakerLabels === 'boolean' ? settings.hideSpeakerLabels : null;
}

function getDesiredHideSpeakerLabels() {
    const stored = getStoredHideSpeakerLabelsSetting();
    return stored === null ? !!hideSpeakerLabels : stored;
}

function renderSpeakerLabelsPicker() {
    const provider = getSelectedProvider();
    const supported = provider === 'soniox';
    if (speakerLabelsSettingField) {
        speakerLabelsSettingField.hidden = !supported;
    }
    if (!speakerLabelsPickerHost) {
        return;
    }
    speakerLabelsPickerHost.innerHTML = '';
    if (!supported) {
        speakerLabelsPickerEl = null;
        return;
    }
    speakerLabelsPickerEl = buildCustomSelect([
        { value: 'show', label: t('speaker_labels_enabled') },
        { value: 'hide', label: t('speaker_labels_disabled') },
    ], {
        value: getDesiredHideSpeakerLabels() ? 'hide' : 'show',
    });
    speakerLabelsPickerHost.appendChild(speakerLabelsPickerEl);
}

function renderBundledCjkFontPicker() {
    if (!bundledCjkFontPickerHost) {
        return;
    }
    bundledCjkFontPickerHost.innerHTML = '';
    
    const hintEl = document.getElementById('bundledCjkFontHint');
    
    if (!customFontAvailable) {
        const statusSpan = document.createElement('span');
        statusSpan.className = 'font-not-detected-status';
        statusSpan.style.color = '#ef4444';
        statusSpan.style.fontSize = '0.95em';
        statusSpan.style.fontWeight = '500';
        statusSpan.textContent = t('custom_font_not_detected') || 'Not detected';
        bundledCjkFontPickerHost.appendChild(statusSpan);
        
        if (hintEl) {
            hintEl.textContent = t('custom_font_missing_hint');
        }
        return;
    }
    
    bundledCjkFontPickerEl = buildCustomSelect([
        { value: 'true', label: t('bundled_cjk_font_enabled') },
        { value: 'false', label: t('bundled_cjk_font_disabled') },
    ], {
        value: useBundledCjkFont ? 'true' : 'false',
    });
    bundledCjkFontPickerHost.appendChild(bundledCjkFontPickerEl);
    
    if (hintEl) {
        hintEl.textContent = t('bundled_cjk_font_hint');
    }
}

function translationModeLabel(mode) {
    return t('translation_mode_' + mode) || mode;
}

function availableTranslationModes() {
    return ['fast', 'accurate', 'hybrid'];
}

function translationModeCostHint(mode) {
    // 快速(fast) uses no LLM, so no hint. 准确/混合 both call the LLM and show
    // the same note.
    if (mode === 'fast') return '';
    return t('translation_cost_llm');
}

function updateTranslationModeHint() {
    if (!translationModeHintEl) return;
    // Preview the currently-highlighted (possibly unsaved) dropdown option so the
    // hint tracks the selection; fall back to the applied mode before the picker
    // is built.
    const mode = (translationModePickerEl && typeof translationModePickerEl.value === 'string')
        ? translationModePickerEl.value
        : translationUiMode;
    const hint = translationModeCostHint(mode);
    if (hint) {
        translationModeHintEl.textContent = hint;
        translationModeHintEl.hidden = false;
    } else {
        translationModeHintEl.textContent = '';
        translationModeHintEl.hidden = true;
    }
}

function renderTranslationModePicker() {
    const shown = llmRefineAvailable && !lockManualControls;
    // The mode field now lives in its own section (above 账户); hide the whole
    // section when unavailable so it doesn't leave an empty flex gap.
    if (translationModeSection) {
        translationModeSection.hidden = !shown;
    }
    if (translationModeSettingField) {
        translationModeSettingField.hidden = !shown;
    }
    if (!translationModePickerHost) return;
    translationModePickerHost.innerHTML = '';
    if (!shown) {
        translationModePickerEl = null;
        if (translationModeHintEl) translationModeHintEl.hidden = true;
        return;
    }
    const modes = availableTranslationModes();
    let selected = translationUiMode;
    if (!modes.includes(selected)) selected = DEFAULT_TRANSLATION_UI_MODE;
    translationModePickerEl = buildCustomSelect(modes.map((mode) => ({
        value: mode,
        label: translationModeLabel(mode),
    })), {
        value: selected,
        // Don't apply on change — like every other setting, 翻译模式 is applied
        // only when the user clicks 保存 (applyRuntimeControlSettings). Here we
        // just preview the cost hint for the highlighted option.
        onChange: () => { updateTranslationModeHint(); },
    });
    translationModePickerHost.appendChild(translationModePickerEl);
    updateTranslationModeHint();
}

// Record the token-sequence boundary when 混合 mode is entered so only STT
// translations arriving afterwards are shown as provisional (gray). Switching
// away clears the marker. Called on both the forward apply and the error rollback.
function noteHybridInterimBoundary(mode, previous) {
    if (mode === 'hybrid') {
        if (previous !== 'hybrid') {
            hybridInterimAfterSequence = tokenSequenceCounter + 1;
        }
    } else {
        hybridInterimAfterSequence = null;
    }
}

async function setTranslationUiMode(mode, options = {}) {
    const normalized = TRANSLATION_UI_MODES.includes(mode) ? mode : DEFAULT_TRANSLATION_UI_MODE;
    const previous = translationUiMode;
    translationUiMode = normalized;
    noteHybridInterimBoundary(normalized, previous);
    try { localStorage.setItem(TRANSLATION_UI_MODE_STORAGE_KEY, normalized); } catch (e) { /* ignore */ }
    // Keep local display state (llmRefineMode) in sync without hitting the legacy
    // /llm-refine endpoint.
    applyLlmRefineMode(TRANSLATION_UI_MODE_TO_LLM[normalized], { persist: true });
    updateTranslationModeHint();
    if (options.silent) return true;
    try {
        const resp = await fetch('/translation-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: normalized }),
        });
        if (!resp.ok) {
            throw new Error(`translation-mode returned ${resp.status}`);
        }
        const data = await resp.json().catch(() => ({}));
        if (data && data.needs_restart && options.restartIfNeeded) {
            // Reopen the stream so soniox translation on/off takes effect.
            void restartRecognition({ auto: true });
        }
        return true;
    } catch (e) {
        console.warn('Failed to set translation mode:', e);
        // Roll back so the UI doesn't claim a mode the backend never applied.
        translationUiMode = previous;
        noteHybridInterimBoundary(previous, normalized);
        try { localStorage.setItem(TRANSLATION_UI_MODE_STORAGE_KEY, previous); } catch (err) { /* ignore */ }
        applyLlmRefineMode(TRANSLATION_UI_MODE_TO_LLM[previous], { persist: true });
        renderTranslationModePicker();
        return false;
    }
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
    renderSpeakerLabelsPicker();
    renderSegmentModePicker();
}

async function applyRuntimeControlSettings() {
    if (autoRestartPickerEl && typeof autoRestartPickerEl.value === 'string') {
        autoRestartEnabled = autoRestartPickerEl.value !== 'false';
        localStorage.setItem('autoRestartEnabled', autoRestartEnabled ? 'true' : 'false');
        updateAutoRestartButton();
    }

    if (speakerLabelsPickerEl && getSelectedProvider() === 'soniox') {
        const requestedHideSpeakerLabels = speakerLabelsPickerEl.value === 'hide';
        if (requestedHideSpeakerLabels !== hideSpeakerLabels) {
            const ok = await setSpeakerLabelsHidden(requestedHideSpeakerLabels);
            if (!ok) {
                return { ok: false, message: t('backend_speaker_labels_disabled') };
            }
        }
    }

    const requestedSegmentMode = normalizeSegmentMode(segmentModePickerEl && segmentModePickerEl.value);
    if (requestedSegmentMode && requestedSegmentMode !== segmentMode) {
        const ok = await setSegmentMode(requestedSegmentMode);
        if (!ok) {
            return { ok: false, message: t('backend_segment_mode_disabled') };
        }
    }

    // 翻译模式: applied here on Save (not on dropdown change). setTranslationUiMode
    // persists + pushes to the backend, and reopens the stream when soniox's
    // built-in translation on/off changed (准确 toggled).
    if (translationModePickerEl && typeof translationModePickerEl.value === 'string') {
        const requestedMode = translationModePickerEl.value;
        if (TRANSLATION_UI_MODES.includes(requestedMode) && requestedMode !== translationUiMode) {
            const ok = await setTranslationUiMode(requestedMode, { restartIfNeeded: true });
            if (!ok) {
                return { ok: false, message: t('validation_error') };
            }
        }
    }

    return { ok: true };
}

async function setSpeakerLabelsHidden(hidden) {
    if (lockManualControls) {
        return false;
    }
    try {
        const response = await fetch('/speaker-labels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hide_speaker_labels: !!hidden })
        });
        if (!response.ok) {
            console.error('Failed to set speaker labels');
            return false;
        }
        const data = await response.json().catch(() => ({}));
        if (typeof data.hide_speaker_labels === 'boolean') {
            hideSpeakerLabels = data.hide_speaker_labels;
        } else {
            hideSpeakerLabels = !!hidden;
        }
        applySpeakerLabelVisibility();
        renderSpeakerLabelsPicker();
        renderSubtitles();
        return true;
    } catch (error) {
        console.error('Error setting speaker labels:', error);
        return false;
    }
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
        }
        console.log(auto ? 'Auto restart: new recognition session requested.' : 'Recognition restarted successfully');

        await delay(1500);

        // Restart is done: swap the "restarting…" placeholder for the waiting
        // state (matches the Qt overlay's "等待字幕…") until real subtitles
        // arrive. Guard so we don't clobber subtitles that already streamed in.
        if (!auto && subtitleContainer.innerHTML === manualStatusHtml) {
            subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
            subtitleContainer.scrollTop = 0;
        }

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
    overlayButton.title = overlayOpen ? t('overlay_close') : t('overlay_open');
    overlayButton.classList.toggle('active', overlayOpen);
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
    if (!wsClient) {
        wsClient = WsClient.createClient({
            WebSocketImpl: window.WebSocket,
            getUrl: () => {
                const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
                return `${protocol}://${window.location.host}/ws${window.location.search}`;
            },
            onSocketChange: (socket) => { ws = socket; },
            onOpen: () => console.log('WebSocket connected'),
            onFrame: handleMessage,
            onError: (error) => console.error('WebSocket error:', error),
            onClose: () => console.log('WebSocket closed'),
            getAutoRestartEnabled: () => autoRestartEnabled,
            onAutoRestart: triggerAutoRestart,
            getShouldReconnect: () => shouldReconnect,
            getIsRestarting: () => isRestarting,
            reconnectDelayMs: 2000,
        });
    }
    return wsClient.connect();
}

const MESSAGE_TYPES = [
    'subtitle_font_preference', 'recognition_paused', 'overlay_visibility', 'ipc_status', 'error',
    'spec_translation_pending', 'spec_translation', 'refine_result', 'subtitle_retract', 'llm_cost',
    'translation_mode_fallback', 'segment_mode_changed', 'speaker_labels_changed',
    'session_connected', 'session_idle', 'session_disconnected', 'clear', 'update',
];
const MESSAGE_HANDLERS = Object.fromEntries(
    MESSAGE_TYPES.map((type) => [type, handleMessageFrame])
);
MESSAGE_HANDLERS.default = handleMessageFrame;

function handleMessage(data) {
    return WsClient.dispatchFrame(data, MESSAGE_HANDLERS);
}

function handleMessageFrame(data) {
    if (data.type === 'subtitle_font_preference') {
        const enabled = !!data.use_bundled_cjk_fonts;
        applyBundledCjkFontPreference(enabled, { persist: true });
        return;
    }
    if (data.type === 'recognition_paused') {
        isPaused = !!data.paused;
        updatePauseButtonUi();
        if (isPaused) {
            sessionCostPause();
        }
        return;
    }
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
    if (data.type === 'spec_translation_pending') {
        const src = (data.source || '').toString().trim();
        if (refineState.markSpecPending(src, data.target_lang)) {
            renderSubtitles();
        }
        return;
    }
    if (data.type === 'spec_translation') {
        if (refineState.applySpecTranslation(data)) renderSubtitles();
        return;
    }
    if (data.type === 'refine_result') {
        handleBackendRefineResult(data);
        return;
    }
    if (data.type === 'subtitle_retract') {
        handleSubtitleRetract(data);
        return;
    }
    if (data.type === 'llm_cost') {
        const credits = Number(data.credits);
        if (Number.isFinite(credits) && credits > 0) {
            sessionLlmCost += credits;
            sessionHadLlmCost = true;
            renderBalanceView();
        }
        return;
    }
    if (data.type === 'translation_mode_fallback') {
        // Prepaid exhausted: the backend session dropped to fast mode. Push
        // 'fast' through the normal path so localStorage AND the backend
        // manager stay in sync (a later session rebuild must not resurrect the
        // old mode); the session-side set is idempotent.
        void setTranslationUiMode('fast', { restartIfNeeded: false });
        renderTranslationModePicker();
        showToast(t('translation_mode_fallback_toast'), true);
        if (data.needs_restart) {
            void restartRecognition({ auto: true });
        }
        return;
    }
    if (data.type === 'segment_mode_changed') {
        handleSegmentModeChanged(data);
        return;
    }
    if (data.type === 'speaker_labels_changed') {
        if (typeof data.hide_speaker_labels === 'boolean') {
            hideSpeakerLabels = data.hide_speaker_labels;
        } else if (typeof data.enabled === 'boolean') {
            hideSpeakerLabels = !data.enabled;
        }
        applySpeakerLabelVisibility();
        renderSpeakerLabelsPicker();
        renderSubtitles();
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
            // The stream is reopening (e.g. 翻译模式 switched to 准确). Built-in
            // translations hidden awaiting an LLM replacement may never get one
            // (the old stream's LLM calls die with it) — reveal them instead of
            // leaving a permanently empty translation line. Their override still
            // applies if the LLM result does arrive late. The next assigned
            // sequence index is exactly tokenSequenceCounter, so this reveals
            // everything already displayed and hides anything newer.
            if (llmTranslateHideAfterSequence !== null) {
                llmTranslateHideAfterSequence = tokenSequenceCounter;
                renderSubtitles();
            }
            // Same reasoning for 混合: STT translations already shown as provisional
            // (gray) won't get their LLM replacement from the dying stream — reveal
            // them as normal text and only gray translations from the new stream.
            if (hybridInterimAfterSequence !== null) {
                hybridInterimAfterSequence = tokenSequenceCounter;
                renderSubtitles();
            }
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
        currentNonFinalTokens.forEach((token) => assignSequenceIndex(token));

        // 合并新增的final tokens
        if (hasNewFinalContent) {
            mergeFinalTokens();
        }

        // 重新渲染
        renderSubtitles();
    }
}

function insertFinalToken(token) {
    tokenSequenceCounter = TokenStream.insertFinalToken(allFinalTokens, token, tokenSequenceCounter);
}

const joinTokenText = TokenStream.joinTokenText;

/**
 * 合并连续的final tokens以减少token数量
 * 只合并从lastMergedIndex开始的新tokens
 * 合并条件：相同speaker、相同language、相同translation_status、is_final=true、非分隔符
 */
function mergeFinalTokens() {
    lastMergedIndex = TokenStream.mergeFinalTokens(allFinalTokens, lastMergedIndex);
}

const { getLangDir, getLanguageTag, wrapSubtitleLineBody } = subtitleHtmlRenderer;

// Pure sentence-boundary logic lives in segmentation.js and mirrors the
// backend. These aliases keep the orchestration code readable.
const {
    endsWithSentenceEnding,
    splitIntoSentenceSegments,
} = Segmentation;

function assignSequenceIndex(token) {
    tokenSequenceCounter = TokenStream.assignSequenceIndex(token, tokenSequenceCounter);
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
    return wsClient
        ? wsClient.isUsable()
        : !!ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
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

    refineState.clear();

    if (isLlmTranslateMode()) {
        llmTranslateHideAfterSequence = tokenSequenceCounter;
    } else {
        llmTranslateHideAfterSequence = null;
    }
    // Fresh session: any 混合 STT translation from here on is provisional.
    hybridInterimAfterSequence = translationUiMode === 'hybrid' ? tokenSequenceCounter : null;
}

const {
    renderTokenSpan,
    renderTokenSpansTrimmed,
} = subtitleHtmlRenderer;
function getSentenceId(sentence, fallbackIndex) {
    return sentence.renderKey || RenderModel.getSentenceRenderKey(sentence, fallbackIndex);
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
    return RenderModel.buildRenderTokens({ allFinalTokens, currentNonFinalTokens });
}

function renderSubtitles() {
    const scrollState = captureScrollState();
    const tokens = buildRenderTokens();
    tokens.forEach((token) => assignSequenceIndex(token));

    if (tokens.length === 0) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        subtitleContainer.scrollTop = 0;
        autoStickToBottom = true;
        return;
    }

    const renderModel = RenderModel.buildRenderModel({
        tokens,
        displayMode,
        suppressTranslationDisplay,
    });
    const { speakerBlocks, showOriginal, showTranslation } = renderModel;

    if (speakerBlocks.length === 0) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    let html = '';
    let previousSpeaker = null;
    let fallbackCounter = 0;
    let blockingUpdate = false;

    for (const block of speakerBlocks) {
        if (blockingUpdate) {
            break;
        }

        const firstSentenceDir = block.sentences.length > 0 ? getLangDir(block.sentences[0].originalLang) : 'ltr';
        const sentencesHtml = [];

        for (const sentence of block.sentences) {
            const sentenceId = getSentenceId(sentence, fallbackCounter++);

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
                        sentenceParts.push(`<div class="subtitle-line original-line" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${langTag}${wrapSubtitleLineBody(lineContent, sentenceDir, sentence.originalLang)}</div>`);
                    } else {
                        const rubyHtml = furiganaCache.get(plainText);

                        if (rubyHtml) {
                            const classes = ['subtitle-text'];
                            if (hasNonFinal) {
                                classes.push('non-final');
                            }
                            const rubySpan = `<span class="${classes.join(' ')}">${rubyHtml}</span>`;
                            sentenceParts.push(`<div class="subtitle-line original-line subtitle-line--furigana" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${wrapSubtitleLineBody(`${langTag}${rubySpan}`, sentenceDir, sentence.originalLang)}</div>`);
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
                    sentenceParts.push(`<div class="subtitle-line original-line" lang="${sentence.originalLang || ''}" dir="${sentenceDir}">${langTag}${wrapSubtitleLineBody(lineContent, sentenceDir, sentence.originalLang)}</div>`);
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
                const sentenceLlmId = getLlmSentenceId(sentence);
                const overrideTranslation = sentenceLlmId
                    ? refineState.getTranslationOverride(sentenceLlmId)
                    : null;
                if (overrideTranslation) {
                    baseTranslationNormalized = overrideTranslation;
                }
                const hasRefined = !!(sentenceLlmId && refineState.getRefinedTranslation(sentenceLlmId));
                // The LLM reviewed this sentence and left it unchanged: treat it
                // as resolved so the STT translation is revealed as final text.
                const isLlmConfirmed = !!(sentenceLlmId && refineState.isConfirmed(sentenceLlmId));
                const shouldHide = shouldHideBuiltinTranslation(sentence, sourceText, hasRefined || isLlmConfirmed);

                if (!shouldHide) {
                    const displayTranslation = overrideTranslation
                        ? overrideTranslation
                        : ((sourceText && baseTranslationNormalized)
                            ? getDisplayTranslationForSentence(sentence, sourceText, baseTranslationNormalized)
                            : baseTranslationNormalized);

                    if (displayTranslation && displayTranslation !== baseTranslationNormalized) {
                        // LLM-refined text replaces the draft in place as plain
                        // text — no diff highlighting (it's a distraction to read).
                        const html = escapeHtml(displayTranslation);
                        sentenceParts.push(`<div class="subtitle-line" lang="${sentence.translationLang || ''}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text" lang="${sentence.translationLang || ''}">${html}</span>`, translationDir, sentence.translationLang)}</div>`);
                    } else if (overrideTranslation) {
                        const html = escapeHtml(displayTranslation || '');
                        sentenceParts.push(`<div class="subtitle-line" lang="${sentence.translationLang || ''}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text" lang="${sentence.translationLang || ''}">${html}</span>`, translationDir, sentence.translationLang)}</div>`);
                    } else {
                        // Raw Soniox translation with no LLM result yet.
                        // 混合 only applies its provisional treatment to sentences
                        // produced after switching into the mode; pre-existing ones
                        // get no LLM result, so they render as normal final text.
                        const afterHybridBoundary = translationUiMode === 'hybrid'
                            && !isLlmConfirmed
                            && sentenceHasTranslationTokenAtOrAfter(sentence, hybridInterimAfterSequence);

                        // 混合 mode should surface STT/Soniox's provisional
                        // translation as soon as it arrives, including the first
                        // finalized chunk of a sentence. The LLM result still
                        // replaces it later via overrideTranslation above.
                        const lineContent = renderTokenSpansTrimmed(sentence.translationTokens, null, {
                            normalizeTranslationSpacing: true
                        });
                        const lineClass = afterHybridBoundary
                            ? 'subtitle-line subtitle-line--stt-interim'
                            : 'subtitle-line';
                        sentenceParts.push(`<div class="${lineClass}" lang="${sentence.translationLang || ''}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(lineContent, translationDir, sentence.translationLang)}</div>`);
                    }
                } else {
                    const placeholderText = '&nbsp;';
                    sentenceParts.push(`<div class="subtitle-line" lang="${sentence.translationLang || ''}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text placeholder" lang="${sentence.translationLang || ''}">${placeholderText}</span>`, translationDir, sentence.translationLang)}</div>`);
                }
            } else if (showTranslation && translationUiMode === 'accurate' && isLlmTranslateMode()) {
                // 准确 mode: soniox translation is disabled so there are no
                // translation tokens; synthesize the translation line from the
                // LLM result once it arrives (matched by sentence id).
                const sentenceLlmId = getLlmSentenceId(sentence);
                const overrideTranslation = sentenceLlmId
                    ? (refineState.getTranslationOverride(sentenceLlmId) || '').trim()
                    : '';
                const srcText = sentence.originalTokens.map(t => (t && t.text) ? String(t.text) : '').join('').trim();
                if (overrideTranslation && overrideTranslation !== srcText) {
                    const lang = refineState.getTranslationLanguage(sentenceLlmId) || currentTranslationTargetLang || '';
                    const translationDir = getLangDir(lang);
                    const langTag = getLanguageTag(lang);
                    sentenceParts.push(`<div class="subtitle-line" lang="${lang}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(`<span class="subtitle-text" lang="${lang}">${escapeHtml(overrideTranslation)}</span>`, translationDir, lang)}</div>`);
                } else if (!overrideTranslation && srcText) {
                    // LLM translation in progress or speculatively done for
                    // this sentence: show the result — or a placeholder while
                    // the call runs — until the finalized line replaces it.
                    const parts = [];
                    let pending = false;
                    let lang = '';
                    // Whole-sentence key first: the finalize-path pending
                    // marker uses the full source (which may lack ending
                    // punctuation when the sentence was endpoint-split).
                    const whole = refineState.getSpecTranslation(srcText);
                    if (whole) {
                        parts.push(whole.text);
                        lang = whole.lang;
                    } else if (refineState.isSpecPending(srcText)) {
                        pending = true;
                        lang = refineState.getSpecPendingLanguage(srcText) || '';
                    } else {
                        // Per-segment speculative results from the non-final text.
                        for (const seg of splitIntoSentenceSegments(srcText)) {
                            if (!endsWithSentenceEnding(seg)) continue; // partial tail
                            const hit = refineState.getSpecTranslation(seg);
                            if (hit) {
                                parts.push(hit.text);
                                if (!lang) lang = hit.lang;
                            } else if (refineState.isSpecPending(seg)) {
                                pending = true;
                                if (!lang) lang = refineState.getSpecPendingLanguage(seg) || '';
                            }
                        }
                    }
                    if (parts.length || pending) {
                        lang = lang || currentTranslationTargetLang || '';
                        const translationDir = getLangDir(lang);
                        const langTag = getLanguageTag(lang);
                        const body = parts.length
                            ? `<span class="subtitle-text non-final" lang="${lang}">${escapeHtml(parts.join(''))}</span>`
                            : `<span class="subtitle-text placeholder" lang="${lang}">&nbsp;</span>`;
                        sentenceParts.push(`<div class="subtitle-line" lang="${lang}" dir="${translationDir}">${langTag}${wrapSubtitleLineBody(body, translationDir, lang)}</div>`);
                    }
                }
            }

            if (sentenceParts.length === 0) {
                continue;
            }

            const sentenceHtml = subtitleHtmlRenderer.renderSentenceHtml(sentenceId, sentenceParts);
            sentencesHtml.push(sentenceHtml);
        }

        if (blockingUpdate) {
            break;
        }

        const blockHtml = subtitleHtmlRenderer.renderSpeakerBlockHtml({
            speaker: block.speaker,
            sentenceHtml: sentencesHtml,
            previousSpeaker,
            direction: firstSentenceDir,
            showSpeakerLabel: speakerDiarizationEnabled && !hideSpeakerLabels,
        });
        if (blockHtml) {
            html += blockHtml;
            previousSpeaker = block.speaker;
        }
    }

    if (blockingUpdate) {
        return;
    }

    if (!html) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    subtitleDomPatcher.patch(html);
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
    const redeemPasteEl = document.getElementById('redeemPasteButton');
    if (redeemPasteEl) {
        const pasteLabel = t('login_paste');
        redeemPasteEl.setAttribute('aria-label', pasteLabel);
        redeemPasteEl.setAttribute('title', pasteLabel);
    }
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
    setText('speakerLabelsSettingLabel', 'speaker_labels_setting');
    setText('segmentModeSettingLabel', 'segment_mode_setting');
    setText('translationModeSettingLabel', 'translation_mode_setting');
    setText('appearanceLabel', 'appearance');
    setText('bundledCjkFontLabel', 'bundled_cjk_font');
    setText('bundledCjkFontHint', 'bundled_cjk_font_hint');
    // Rebuild the region picker so its option labels follow the active language.
    renderSonioxRegionPicker(getSelectedSonioxRegion());
    renderMicrophoneDevicePicker();
    renderRuntimeSettingsPickers();
    renderBundledCjkFontPicker();
    if (settingsSaveButton) settingsSaveButton.textContent = t('save');
    if (settingsCancelButton) settingsCancelButton.textContent = t('cancel');
    if (settingsModeBackButton) settingsModeBackButton.textContent = t('mode_back_to_chooser');
    if (resetAllButton) resetAllButton.textContent = t('reset_all');
    if (settingsButton) settingsButton.title = t('settings');
    if (settingsCloseButton) settingsCloseButton.title = t('close');
    const versionEl = document.getElementById('settingsVersion');
    if (versionEl) {
        versionEl.textContent = t('client_version', { version: clientVersion });
    }
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
        const pricePerSec = Number(info.price_per_second) || 0;
        return t('provider_relay_desc', {
            price: formatRate(pricePerSec),
            minutePrice: formatRate(pricePerSec * 60)
        });
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
    renderBundledCjkFontPicker();
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
    const firstBonusHint = document.getElementById('firstRedeemBonusHint');
    if (serverHint) {
        serverHint.textContent = relayServerUrl ? t('account_server', { url: relayServerUrl }) : '';
    }
    if (identityHint) {
        const server = loadServerSettings();
        if (backendLoggedIn || server.token) {
            const name = server.displayName || '—';
            const rank = rankLabel(server.trustRank) || '—';
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
    if (firstBonusHint) {
        const showFirstBonus = (backendLoggedIn || !!loadServerSettings().token)
            && firstRedeemBonusEligible
            && Number(firstRedeemBonusCredits) > 0;
        firstBonusHint.textContent = showFirstBonus
            ? t('account_first_redeem_bonus', { credits: formatCredits(firstRedeemBonusCredits) })
            : '';
        firstBonusHint.hidden = !showFirstBonus;
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
    // Mirror the same live (estimate-adjusted) view the balance bar shows.
    const view = currentBalanceView();
    if (balanceHint) {
        if (signedIn && view && view.prepaid_balance != null) {
            balanceHint.textContent = t('account_balance', {
                balance: formatCredits(view.prepaid_balance),
            });
            balanceHint.hidden = false;
        } else {
            balanceHint.textContent = '';
            balanceHint.hidden = true;
        }
    }
    if (poolsBox) {
        const pools = (signedIn && view && view.free) ? view.free.pools : null;
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
    // Rebuild the 翻译模式 picker from the applied mode so an unsaved selection
    // from a previously cancelled panel doesn't linger.
    renderTranslationModePicker();
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
        const oldProvider = translationProvider;
        translationProvider = data.provider || provider;
        if (translationProvider !== oldProvider) {
            sessionCostReset();
        }
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

function directSettingsNeedSetup({ provider, region, apiKeyToPush, previousKey }) {
    const desiredKeySource = apiKeyToPush ? 'localstorage' : 'env';
    const keyChanged = String(previousKey || '') !== String(apiKeyToPush || '');
    const providerMismatch = provider !== translationProvider;
    const modeMismatch = backendMode !== 'direct';
    const regionMismatch = !backendSonioxCustomUrl
        && provider === 'soniox'
        && region
        && normalizeSonioxRegion(region) !== backendSonioxRegion;
    const keySourceMismatch = backendKeySource !== desiredKeySource;

    return setupRequired
        || providerMismatch
        || modeMismatch
        || regionMismatch
        || keyChanged
        || keySourceMismatch;
}

function relaySettingsNeedSetup({ provider, token }) {
    const providerMismatch = provider !== translationProvider;
    const modeMismatch = backendMode !== 'relay';
    const needsLoginPush = !!token && !backendLoggedIn;

    return setupRequired || providerMismatch || modeMismatch || needsLoginPush;
}

function finishHotSettingsSave() {
    setupRequired = false;
    hideSettingsPanel();
    showToast(t('settings_saved'));
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
    const targetCjkFontValue = customFontAvailable && bundledCjkFontPickerEl ? (bundledCjkFontPickerEl.value === 'true') : false;
    applyBundledCjkFontPreference(targetCjkFontValue, {
        persist: true,
        sync: true,
    });
    const provider = getSelectedProvider();
    const region = getSelectedSonioxRegion();
    const mode = getSettingsMode();
    const settings = loadProviderSettings();
    const previousProviderKey = settings.keys && settings.keys[provider] ? String(settings.keys[provider]) : '';
    settings.providerOverride = provider;
    if (region) {
        settings.sonioxRegion = region;
    }
    settings.keys = settings.keys || {};
    if (speakerLabelsPickerEl && provider === 'soniox') {
        settings.hideSpeakerLabels = speakerLabelsPickerEl.value === 'hide';
    }

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
        if (!relaySettingsNeedSetup({ provider, token: server.token })) {
            if (settingsSaveButton) { settingsSaveButton.disabled = false; settingsSaveButton.textContent = t('save'); }
            finishHotSettingsSave();
            return;
        }
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
    if (!directSettingsNeedSetup({ provider, region, apiKeyToPush, previousKey: previousProviderKey })) {
        if (settingsSaveButton) settingsSaveButton.disabled = false;
        if (settingsSaveButton) settingsSaveButton.textContent = t('save');
        finishHotSettingsSave();
        return;
    }
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
        if (!token) {
            return;
        }
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

let startupLoginPreopened = false;

function shouldPreopenHostedLogin() {
    if (lockManualControls || !relayAvailable || !relayServerUrl) {
        return false;
    }
    const server = loadServerSettings();
    if (server.token) {
        return false;
    }
    if (server.mode === 'direct' && hasExplicitConnectionMode(server)) {
        return false;
    }
    return server.mode === 'relay' || !hasExplicitConnectionMode(server);
}

function preopenHostedLoginIfNeeded() {
    if (!shouldPreopenHostedLogin()) {
        return false;
    }
    const server = loadServerSettings();
    if (!hasExplicitConnectionMode(server)) {
        server.mode = 'relay';
        server.modeChosen = true;
        saveServerSettings(server);
    }
    startupLoginPreopened = true;
    openLogin({ forced: true });
    return true;
}

function refreshPreopenedHostedLogin() {
    if (!startupLoginPreopened) {
        return;
    }
    if (lockManualControls || !relayAvailable || !relayServerUrl || getConnectionMode() !== 'relay') {
        hideLogin();
        return;
    }
    applyLoginI18n();
    updateLoginSubmitState();
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
            renderRuntimeSettingsPickers();
            const server = loadServerSettings();
            if (backendLoggedIn || !!server.token) {
                void fetchBalance({ provider, force: true });
            }
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
                const server = loadServerSettings();
                if (backendLoggedIn || !!server.token) {
                    void fetchBalance({ provider, force: true });
                }
            }
        });
    });
}

// Account actions (relay/hosted mode).
const redeemButton = document.getElementById('redeemButton');
const redeemInput = document.getElementById('redeemInput');
const redeemPasteButton = document.getElementById('redeemPasteButton');
const reLoginButton = document.getElementById('reLoginButton');
const logoutButton = document.getElementById('logoutButton');
const copyInviteButton = document.getElementById('copyInviteButton');
const openUserWebButton = document.getElementById('openUserWebButton');
if (redeemButton) {
    redeemButton.addEventListener('click', () => handleRedeem());
}
if (redeemPasteButton) {
    redeemPasteButton.addEventListener('click', async (event) => {
        event.preventDefault();
        try {
            const text = await navigator.clipboard.readText();
            if (redeemInput) {
                redeemInput.value = (text || '').trim();
                redeemInput.focus();
            }
        } catch (e) {
            // Clipboard read may be unavailable/denied; user can paste manually.
        }
    });
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
const loginPasteButton = document.getElementById('loginPasteButton');
const loginCodeLink = document.getElementById('loginCodeLink');
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
        clientUpdateDirectButton.onclick = async () => {
            clientUpdateOverlay.hidden = true;
            clientUpdateDialog.hidden = true;
            const confirmed = await showConfirm(t('client_update_direct_confirm'), {
                okLabel: t('client_update_direct_confirm_ok'),
                cancelLabel: t('client_update_direct_confirm_cancel'),
                danger: true,
            });
            if (confirmed) {
                closeClientUpdateDialog('direct');
                return;
            }
            clientUpdateOverlay.hidden = false;
            clientUpdateDialog.hidden = false;
        };
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

// Escape hatch from the hosted-login panel: switch to own-key (direct) mode and
// jump straight into Settings so the user can paste their own provider key.
async function switchToOwnKeyMode() {
    if (lockManualControls || !relayAvailable) {
        return;
    }
    const s = loadServerSettings();
    s.mode = 'direct';
    s.modeChosen = true;
    saveServerSettings(s);
    pushedOverrideBootId = null;
    hideLogin();
    setModeRadio('direct');
    applyModeSectionsVisibility('direct');
    await syncProviderFromStorage();
    updateAccountSection();
    updateBalanceBarVisibility();
    openSettings({ forced: true });
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
    // Server address is built in: default to hosted mode and surface the login
    // panel directly (it carries a small "use own key" escape hatch) instead of
    // making the user pick a connection mode up front.
    s.mode = 'relay';
    s.modeChosen = true;
    saveServerSettings(s);
    await ensureHostedVersionAllowed({ candidateMode: 'relay' });
}

// ---- Login overlay (web-generated one-time code only) ----
function applyLoginI18n() {
    setElText('loginTitle', t('login_hosted_title'));
    setElText('loginUserInputLabel', t('login_code_input_label'));
    const loginPasteEl = document.getElementById('loginPasteButton');
    if (loginPasteEl) {
        const pasteLabel = t('login_paste');
        loginPasteEl.setAttribute('aria-label', pasteLabel);
        loginPasteEl.setAttribute('title', pasteLabel);
    }
    setElText('loginModeBackButton', t('use_own_key_mode'));
    setElText('loginBackButton', t('login_back'));
    setElText('loginCodeLink', t('login_vrchat_button'));
    setElText('loginManualToggle', t('login_manual_toggle'));
    setElText('loginWaitingHint', t('login_waiting'));
    const serverHint = document.getElementById('loginServerHint');
    if (serverHint) serverHint.textContent = relayServerUrl ? t('login_server', { url: relayServerUrl }) : '';
    const codeHint = document.getElementById('loginCodeHint');
    if (codeHint) codeHint.hidden = !relayServerUrl;
}

function loginPrimaryLabel(step) {
    return t('login_submit_code');
}

function setLoginStep(step) {
    const inputStep = document.getElementById('loginStepInput');
    const methodStep = document.getElementById('loginStepMethod');
    const challengeStep = document.getElementById('loginStepChallenge');
    if (inputStep) inputStep.hidden = (step !== 'input');
    if (methodStep) methodStep.hidden = (step !== 'method');
    if (challengeStep) challengeStep.hidden = (step !== 'challenge');
    if (loginBackButton) loginBackButton.hidden = true;
    if (loginPrimaryButton) loginPrimaryButton.textContent = loginPrimaryLabel(step);
    loginForm && loginForm.setAttribute('data-step', step);
}

function hasLoginCodeInput() {
    return !!((loginUserInput && loginUserInput.value || '').trim());
}

function updateLoginSubmitState() {
    if (!loginPrimaryButton) return;
    loginPrimaryButton.hidden = !loginManualShown;
    loginPrimaryButton.disabled = loginSubmitBusy || loginWaitingForBrowser || !hasLoginCodeInput();
    if (!loginSubmitBusy) {
        loginPrimaryButton.textContent = loginPrimaryLabel(loginForm && loginForm.getAttribute('data-step'));
    }
    if (loginCodeLink) {
        loginCodeLink.disabled = loginSubmitBusy || loginWaitingForBrowser;
        if (!loginSubmitBusy) loginCodeLink.textContent = t('login_vrchat_button');
    }
}

function setLoginBusy(busy) {
    loginSubmitBusy = !!busy;
    if (loginSubmitBusy && loginPrimaryButton) {
        loginPrimaryButton.disabled = true;
        loginPrimaryButton.textContent = t('login_verifying');
    }
    if (loginCodeLink) {
        loginCodeLink.disabled = loginSubmitBusy || loginWaitingForBrowser;
        loginCodeLink.textContent = loginSubmitBusy ? t('login_verifying') : t('login_vrchat_button');
    }
    if (loginSubmitBusy) return;
    updateLoginSubmitState();
}

function resetLoginToInput() {
    if (loginErrorEl) loginErrorEl.textContent = '';
    setLoginStep('input');
    updateLoginSubmitState();
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
        renderBonusLadder();
    } catch (e) {
        if (section) section.hidden = true;
    }
}

function rankLabel(rank) {
    const key = String(rank || '').toLowerCase();
    const labels = {
        zh: {
            visitor: '游客 (Visitor)',
            new_user: '萌新 (New User)',
            user: '玩家 (User)',
            known_user: '长期玩家 (Known User)',
            trusted_user: '资深玩家 (Trusted User)',
        },
        en: {
            visitor: 'Visitor',
            new_user: 'New User',
            user: 'User',
            known_user: 'Known User',
            trusted_user: 'Trusted User',
        },
        ja: {
            visitor: 'Visitor',
            new_user: 'New User',
            user: 'User',
            known_user: 'Known User',
            trusted_user: 'Trusted User',
        },
    };
    const lang = (window.I18N && window.I18N.lang) || 'en';
    return (labels[lang] && labels[lang][key]) || labels.en[key] || String(rank || '').replace(/_/g, ' ');
}

function renderBonusLadder() {
    const section = document.getElementById('loginBonusSection');
    const thresholdHint = document.getElementById('loginThresholdHint');
    if (!section || !thresholdHint) return;
    const info = loginRegistrationInfo;
    const threshold = info && info.registration_threshold;
    if (threshold) {
        thresholdHint.hidden = false;
        thresholdHint.textContent = t('login_threshold', { rank: rankLabel(threshold) });
        section.hidden = false;
    } else {
        thresholdHint.hidden = true;
        thresholdHint.textContent = '';
        section.hidden = true;
    }
}

let loginManualShown = false;
let loginPollTimer = null;
let loginPollState = null;
let loginPollDeadline = 0;

function setLoginManualShown(show) {
    loginManualShown = !!show;
    const field = document.getElementById('loginManualField');
    if (field) field.hidden = !loginManualShown;
    if (loginManualShown) {
        if (loginUserInput) loginUserInput.focus();
    }
    updateLoginSubmitState();
}

function setLoginWaiting(waiting) {
    loginWaitingForBrowser = !!waiting;
    const hint = document.getElementById('loginWaitingHint');
    if (hint) hint.hidden = !waiting;
    if (loginCodeLink) loginCodeLink.disabled = loginSubmitBusy || loginWaitingForBrowser;
    updateLoginSubmitState();
}

function openLogin({ forced = false } = {}) {
    if (lockManualControls) return;
    loginForcedOpen = !!forced;
    applyLoginI18n();
    resetLoginToInput();
    if (loginUserInput) loginUserInput.value = '';
    setLoginManualShown(false);
    setLoginWaiting(false);
    updateLoginSubmitState();
    void fetchRegistrationInfo();
    if (loginOverlay) loginOverlay.hidden = false;
    if (loginPanel) loginPanel.hidden = false;
    const manualToggle = document.getElementById('loginManualToggle');
    if (manualToggle) manualToggle.hidden = false;
    if (loginCloseButton) loginCloseButton.style.display = loginForcedOpen ? 'none' : '';
    if (loginModeBackButton) loginModeBackButton.hidden = !relayAvailable;
}

function hideLogin() {
    if (loginOverlay) loginOverlay.hidden = true;
    if (loginPanel) loginPanel.hidden = true;
    loginForcedOpen = false;
    stopLoginPolling();
    setLoginWaiting(false);
    setLoginBusy(false);
}

function stopLoginPolling() {
    if (loginPollTimer) {
        clearTimeout(loginPollTimer);
        loginPollTimer = null;
    }
    loginPollState = null;
}

// Primary hosted-login path: open the web verification page in the system
// browser with a loopback callback, then poll our own backend until the web
// page bounces the one-time code back — no manual copy/paste needed.
async function startHostedLogin() {
    const base = (relayServerUrl || '').replace(/\/+$/, '');
    if (!base) {
        showToast(t('server_not_configured'), true);
        return;
    }
    if (loginErrorEl) loginErrorEl.textContent = '';
    let state = '';
    try {
        const resp = await fetch('/account/login-begin', { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        state = data && data.state;
    } catch (e) {
        // ignore — fall back to opening without a callback (manual paste)
    }
    const origin = window.location.origin;
    let url = base + '/app/#/login?next=' + encodeURIComponent('/login-code');
    if (state) {
        const cb = origin + '/account/login-callback';
        url += '&client_callback=' + encodeURIComponent(cb) + '&state=' + encodeURIComponent(state);
    }
    try {
        window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e) {
        if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
        showToast(url);
    }
    if (state) {
        loginPollState = state;
        loginPollDeadline = Date.now() + 5 * 60 * 1000;
        setLoginWaiting(true);
        scheduleLoginPoll();
    }
}

function scheduleLoginPoll() {
    if (loginPollTimer) clearTimeout(loginPollTimer);
    loginPollTimer = setTimeout(() => { void pollLoginCallback(); }, 1500);
}

async function pollLoginCallback() {
    const state = loginPollState;
    if (!state) return;
    if (Date.now() > loginPollDeadline) {
        stopLoginPolling();
        setLoginWaiting(false);
        return;
    }
    try {
        const resp = await fetch('/account/login-poll?state=' + encodeURIComponent(state));
        const data = await resp.json().catch(() => ({}));
        if (data && data.status === 'done' && data.api_key) {
            stopLoginPolling();
            setLoginBusy(true);
            setLoginWaiting(false);
            try {
                await onLoginSuccess(data);
            } catch (e) {
                if (loginErrorEl) loginErrorEl.textContent = String(e);
                setLoginBusy(false);
            }
            return;
        }
        if (data && data.status === 'error') {
            stopLoginPolling();
            setLoginWaiting(false);
            if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, data);
            return;
        }
    } catch (e) {
        // transient — keep polling
    }
    if (loginPollState === state) scheduleLoginPoll();
}

function closeLogin() {
    if (loginForcedOpen) return;
    hideLogin();
}

// Redeem the one-time login code generated by the user web page.
async function handleLoginInput() {
    const raw = (loginUserInput && loginUserInput.value || '').trim();
    if (!raw) {
        if (loginErrorEl) loginErrorEl.textContent = t('login_code_required');
        updateLoginSubmitState();
        return;
    }
    setLoginBusy(true);
    if (loginErrorEl) loginErrorEl.textContent = '';
    try {
        await tryLoginCode(raw);
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
    if (loginErrorEl) loginErrorEl.textContent = mapVerifyError(resp.status, data);
    return 'error';
}

// Shared on successful sign-in via a one-time login code.
async function onLoginSuccess(data) {
    const server = loadServerSettings();
    server.mode = 'relay';
    server.modeChosen = true;
    server.token = data.api_key;
    server.displayName = data.display_name || '';
    server.trustRank = data.trust_rank || '';
    saveServerSettings(server);
    showToast(t('login_success', { name: server.displayName || data.display_name || '' }));
    hideLogin();
    updateBalanceBarVisibility();
    void fetchBalance();
    clearSubtitleState();

    // Persist provider override and start a relay session after the login UI is gone.
    const settings = loadProviderSettings();
    const provider = settings.providerOverride || translationProvider || 'soniox';
    translationModeSynced = false;
    try {
        await pushSetup(provider, null, { silent: true, mode: 'relay', token: data.api_key });
    } catch (e) {
        showToast(String(e), true);
    }
}

function mapVerifyError(status, data) {
    if (status === 429) return t('login_rate_limited');
    const msg = data && (data.detail || data.message);
    return localizeBackendMessage(msg || t('connection_error_try_again'));
}

function openExternalUrl(url) {
    if (!url) return;
    try {
        window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e) {
        if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
        showToast(url);
    }
}

if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleLoginInput();
    });
}
if (loginBackButton) {
    loginBackButton.addEventListener('click', () => resetLoginToInput());
}
if (loginUserInput) {
    loginUserInput.addEventListener('input', () => updateLoginSubmitState());
}
if (loginCodeLink) {
    loginCodeLink.addEventListener('click', (event) => {
        event.preventDefault();
        void startHostedLogin();
    });
}
const loginManualToggle = document.getElementById('loginManualToggle');
if (loginManualToggle) {
    loginManualToggle.addEventListener('click', () => setLoginManualShown(!loginManualShown));
}
if (loginModeBackButton) {
    loginModeBackButton.addEventListener('click', () => switchToOwnKeyMode());
}
if (loginCloseButton) {
    loginCloseButton.addEventListener('click', () => closeLogin());
}
if (loginOverlay) {
    loginOverlay.addEventListener('click', () => closeLogin());
}
if (loginPasteButton) {
    loginPasteButton.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (loginUserInput) {
                loginUserInput.value = (text || '').trim();
                loginUserInput.focus();
                updateLoginSubmitState();
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
            firstRedeemBonusCredits = Math.max(0, Number(data.first_redeem_bonus_credits) || 0);
            firstRedeemBonusEligible = false;
            updateAccountSection();
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

// Poll fast while a session is actively metering (the balance is being spent),
// slow when idle (nothing is billed, so the balance is static and a low-frequency
// refresh is enough). "Recognizing" == the session-cost meter is running.
const BALANCE_POLL_ACTIVE_MS = 45 * 1000;      // recognizing: keep the balance fresh
const BALANCE_POLL_IDLE_MS = 5 * 60 * 1000; // idle: refresh once every 5 minutes

function balanceIsMetering() {
    return sessionRunSince != null;
}

function desiredBalancePollMs() {
    return balanceIsMetering() ? BALANCE_POLL_ACTIVE_MS : BALANCE_POLL_IDLE_MS;
}

function startBalancePolling({ immediate = true } = {}) {
    if (immediate) void fetchBalance();
    scheduleBalancePolling();
}

// (Re)arm the poll timer at the cadence appropriate for the current state.
// Cheap no-op when the running timer is already at the desired cadence, so this
// can be called freely on every session state change.
function scheduleBalancePolling() {
    if (!balanceBarShouldShow()) {
        stopBalancePolling();
        return;
    }
    const desired = desiredBalancePollMs();
    if (balancePollTimer && balancePollIntervalMs === desired) return;
    if (balancePollTimer) clearInterval(balancePollTimer);
    balancePollIntervalMs = desired;
    balancePollTimer = setInterval(fetchBalance, desired);
}

function stopBalancePolling() {
    if (balancePollTimer) {
        clearInterval(balancePollTimer);
        balancePollTimer = null;
    }
    balancePollIntervalMs = 0;
}

async function fetchBalance({ provider = null, force = false } = {}) {
    if (!force && !balanceBarShouldShow()) return;
    try {
        const url = provider ? `/account/balance?provider=${encodeURIComponent(provider)}` : '/account/balance';
        const resp = await fetch(url);
        if (!resp.ok) return;
        const data = await resp.json();
        pricePerSecond = Number(data.price_per_second) || 0;
        firstRedeemBonusCredits = Math.max(0, Number(data.first_redeem_bonus_credits) || firstRedeemBonusCredits || 0);
        firstRedeemBonusEligible = !!data.first_redeem_bonus_eligible && firstRedeemBonusCredits > 0;
        renderBalance(data);
        updateAccountSection();
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

// Total spendable credits in a payload (prepaid + finite free pools). Used only
// to decide whether a fresh fetch went up (top-up / quota rollover) or down
// (server billed a finished session) relative to the current baseline.
function balanceTotalRemaining(data) {
    if (!data) return 0;
    let total = Math.max(0, Number(data.prepaid_balance || 0));
    const pools = data.free && Array.isArray(data.free.pools) ? data.free.pools : [];
    for (const pool of pools) {
        if (pool.unlimited) continue;
        total += Math.max(0, Number(pool.remaining || 0));
    }
    return total;
}

// Estimated in-flight consumption of the current session, in credits, rounded to
// a whole number of per-second ticks — the same value the "this session" meter
// shows. This is the deduction we optimistically apply between server refreshes.
// Only the deduction total is rounded, not the resulting balance.
// Server bills Soniox STT-only streams at a reduced rate. Mirror that factor
// when built-in translation is disabled by either 翻译语言=关闭 or 准确 mode.
function sttRateMultiplier() {
    if (translationProvider === 'soniox' && (uiTranslationMode === 'none' || translationUiMode === 'accurate')) {
        const f = Number(sonioxNoTranslationFactor);
        if (Number.isFinite(f) && f > 0) return f;
    }
    return 1;
}

function effectivePricePerSecond() {
    return Number(pricePerSecond) * sttRateMultiplier();
}

function estimatedSessionCost() {
    const p = effectivePricePerSecond();
    if (!Number.isFinite(p) || p <= 0) return 0;
    const seconds = sessionElapsedMs() / 1000;
    return Math.max(0, Math.round(seconds) * p);
}

// Apply the estimated in-flight deduction to a copy of a balance payload,
// mirroring the server's charge order: free pools daily -> weekly -> monthly,
// then prepaid. Subscriptions are never metered server-side, so they're left
// untouched.
function applyEstimatedDeduction(data, estCost) {
    if (!data) return data;
    let remaining = Math.max(0, Number(estCost) || 0);
    const out = Object.assign({}, data);
    if (data.free && Array.isArray(data.free.pools)) {
        const pools = data.free.pools.map((pool) => Object.assign({}, pool));
        for (const pool of pools) {
            if (remaining <= 0) break;
            if (pool.unlimited) {
                // An unlimited pool absorbs the rest; nothing reaches prepaid.
                remaining = 0;
                break;
            }
            const avail = Math.max(0, Number(pool.remaining || 0));
            const take = Math.min(avail, remaining);
            if (take > 0) {
                pool.remaining = avail - take;
                remaining -= take;
            }
        }
        out.free = Object.assign({}, data.free, { pools });
    }
    if (remaining > 0) {
        const prepaid = Math.max(0, Number(data.prepaid_balance || 0));
        out.prepaid_balance = Math.max(0, prepaid - remaining);
    }
    return out;
}

// The balance to display right now: the baseline (last authoritative fetch that
// wasn't superseded by our own estimate) minus the live in-flight estimate.
function currentBalanceView() {
    const base = balanceBaseline || lastBalanceData;
    if (!base) return null;
    const view = applyEstimatedDeduction(base, estimatedSessionCost());
    // Hosted LLM spend is prepaid-only; deduct it after the STT free->prepaid split.
    if (view && sessionLlmCost > 0) {
        const prepaid = Math.max(0, Number(view.prepaid_balance || 0));
        view.prepaid_balance = Math.max(0, prepaid - sessionLlmCost);
    }
    return view;
}

function renderBalance(data) {
    lastBalanceData = data;
    // Re-baseline unless the server balance dropped while an estimate is live: a
    // mid-session drop means the server just billed a finished session that our
    // local estimate already accounts for, so keeping the old baseline (and
    // trusting the estimate) avoids double-counting. Idle refreshes, top-ups and
    // quota rollovers (balance flat or up) re-baseline.
    if (!balanceBaseline
        || estimatedSessionCost() <= 0
        || balanceTotalRemaining(data) >= balanceTotalRemaining(balanceBaseline)) {
        balanceBaseline = data;
    }
    renderBalanceView();
}

// Render the balance bar (and the account-panel mirror) from the current
// estimated view. Called on every fetch and once per second while a session
// runs, so the displayed balance and free/paid quotas tick down live.
function renderBalanceView() {
    updateSessionCostDisplay();
    const view = currentBalanceView();
    if (!view) return;
    setElText('balanceLabel', t('balance_label'));
    setElText('balanceValue', formatCredits(view.prepaid_balance));
    setElText('sessionLabel', t('balance_session'));
    if (balanceOpenSettingsButton) {
        balanceOpenSettingsButton.textContent = t('open_settings');
    }
    if (balanceActionItem) {
        // Base the "top up" prompt on the authoritative server state, not the
        // optimistic estimate, so it only shows when actually exhausted.
        balanceActionItem.hidden = !isAccountExhausted(lastBalanceData);
    }

    // Free quota: one item per configured pool (daily/weekly/monthly).
    renderFreePools(document.getElementById('freePools'), view.free && view.free.pools);

    // Mirror the balance into the account panel if it's open.
    updateAccountBalance();

    // Subscription quota: show remaining for the first active plan, if any.
    const subItem = document.getElementById('subItem');
    if (subItem) {
        const subs = Array.isArray(view.subscriptions) ? view.subscriptions : [];
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
    const effRate = effectivePricePerSecond();
    const sttCost = (sessionElapsedMs() / 1000) * effRate;
    if (sessionHadLlmCost || sessionLlmCost > 0) {
        // Hosted LLM spend this session -> break the cost down as
        // "<total> (<stt> + LLM <llm>)". The STT figure keeps its existing
        // rounding; the total is that rounded STT figure plus the LLM cost,
        // shown to 2 decimals.
        const sttStr = formatSessionCost(sttCost, effRate);
        const sttRounded = Number(sttStr);
        const llmCost = Math.max(0, sessionLlmCost);
        const total = (Number.isFinite(sttRounded) ? sttRounded : 0) + llmCost;
        const totalStr = (Math.round(total * 100) / 100).toFixed(2);
        const llmStr = (Math.round(llmCost * 100) / 100).toFixed(2);
        setElText('sessionValue', `${totalStr} (${sttStr} + LLM ${llmStr})`);
    } else {
        setElText('sessionValue', formatSessionCost(sttCost, effRate));
    }
}

function sessionCostResume() {
    if (getConnectionMode() !== 'relay') return;
    if (sessionRunSince == null) {
        sessionRunSince = Date.now();
    }
    if (!sessionCostTimer) {
        // Tick the whole balance view (balance + free/paid quotas + this-session
        // cost) every second so they draw down live between server refreshes.
        sessionCostTimer = setInterval(renderBalanceView, 1000);
    }
    renderBalanceView();
    scheduleBalancePolling(); // recognizing again -> fast cadence
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
    renderBalanceView();
    scheduleBalancePolling(); // no longer recognizing -> slow cadence
}

function sessionCostReset() {
    sessionAccumMs = 0;
    sessionRunSince = null;
    sessionLlmCost = 0;
    sessionHadLlmCost = false;
    if (sessionCostTimer) {
        clearInterval(sessionCostTimer);
        sessionCostTimer = null;
    }
    // A fresh session starts from zero, so drop the old session's estimate and
    // re-anchor to the latest known balance. Pull a fresh figure too: the just
    // finished session has now been billed server-side (and the provider/price
    // may have changed), so the next fetch re-baselines to the real balance.
    balanceBaseline = lastBalanceData;
    renderBalanceView();
    scheduleBalancePolling();
    void fetchBalance();
}

document.addEventListener('DOMContentLoaded', () => {
    (async () => {
        preopenHostedLoginIfNeeded();
        await fetchUiConfig();
        refreshPreopenedHostedLogin();
        await maybeRunFirstLaunchFlow();
        await ensureHostedVersionAllowed();
        refreshPreopenedHostedLogin();
        await syncProviderFromStorage();
        await fetchLlmRefineStatus();
        fetchApiKeyStatus();
        fetchOscTranslationStatus();
        maybeForceOpenSettings();
        updateBalanceBarVisibility();
        connect();
    })();
});

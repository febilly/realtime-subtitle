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

const SONIOX_REGIONS = SettingsPolicy.SONIOX_REGIONS;
// Custom-select element (built lazily); mirrors the language picker styling.
let sonioxRegionPickerEl = null;

// Where users obtain an API key for each provider (shown as a link in Settings).
const PROVIDER_KEY_URLS = {
    soniox: 'https://console.soniox.com/api-keys',
    gemini: 'https://aistudio.google.com/apikey',
};

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

const settingsRuntime = SettingsRuntime.create({
    document,
    fetch,
    storage: localStorage,
    t,
    localizeBackendMessage,
    buildCustomSelect,
    console,
    elements: {
        runtimeControlsSection,
        microphoneDeviceSection,
        microphoneDevicePickerHost,
        microphoneDeviceHint,
        autoRestartPickerHost,
        speakerLabelsSettingField,
        speakerLabelsPickerHost,
        bundledCjkFontPickerHost,
        bundledCjkFontHint: document.getElementById('bundledCjkFontHint'),
        translationModeSection,
        translationModeSettingField,
        translationModePickerHost,
        translationModeHint: translationModeHintEl,
        segmentModeSettingField,
        segmentModePickerHost,
    },
    getState: () => ({
        get selectedProvider() { return getSelectedProvider(); },
        get providerSettings() { return loadProviderSettings(); },
        get autoRestartEnabled() { return autoRestartEnabled; },
        get hideSpeakerLabels() { return hideSpeakerLabels; },
        get customFontAvailable() { return customFontAvailable; },
        get useBundledCjkFont() { return useBundledCjkFont; },
        get llmRefineAvailable() { return llmRefineAvailable; },
        get lockManualControls() { return lockManualControls; },
        get translationUiMode() { return translationUiMode; },
        get defaultTranslationUiMode() { return DEFAULT_TRANSLATION_UI_MODE; },
        get translationUiModes() { return TRANSLATION_UI_MODES; },
        get segmentModeSupported() { return segmentModeSupported; },
        get segmentMode() { return segmentMode; },
        get segmentModes() { return getSegmentModes(); },
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'autoRestartEnabled')) {
            autoRestartEnabled = patch.autoRestartEnabled;
        }
        if (Object.prototype.hasOwnProperty.call(patch, 'useBundledCjkFont')) {
            useBundledCjkFont = patch.useBundledCjkFont;
        }
    },
    actions: {
        updateAutoRestartButton,
        setSpeakerLabelsHidden,
        setSegmentMode,
        setTranslationUiMode,
    },
});

function applyBundledCjkFontPreference(enabled, { persist = false, sync = false } = {}) {
    return settingsRuntime.applyBundledCjkFontPreference(enabled, { persist, sync });
}

function syncBundledCjkFontPreference(enabled) {
    return settingsRuntime.syncBundledCjkFontPreference(enabled);
}

applyBundledCjkFontPreference(useBundledCjkFont, { sync: true });
let toastTimer = null;

// ---- Subtitle-server relay (hosted mode) state ----
const SUBTITLE_SERVER_STORAGE_KEY = 'subtitleServer.v1';
let relayAvailable = !!INITIAL_UI_CONFIG.relay_available;
let relayServerUrl = typeof INITIAL_UI_CONFIG.server_url === 'string' ? INITIAL_UI_CONFIG.server_url : '';
const settingsStore = SettingsStore.create({
    storage: localStorage,
    getRelayServerUrl: () => relayServerUrl,
});
let creditsPurchaseUrl = '';
let clientVersion = '0.1.0';
let clientLatestVersion = '';
let clientMinimumVersion = '';
let clientUpdateUrl = '';
let clientUpdateNotes = '';
let backendMode = 'direct';
let backendLoggedIn = false;
let relayPricing = null; // { soniox: {price_per_second, free_*}, gemini: {...} }
// STT billing factor for soniox 准确 mode (built-in translation off), delivered by
// the server via /ui-config. 1 = no discount; applied to the live cost estimate.
let sonioxNoTranslationFactor = 1;
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
const { hasExplicitConnectionMode } = SettingsPolicy;

// Resolved connection mode: 'relay' | 'direct' | null (undecided / first launch).
function getConnectionMode() {
    return SettingsPolicy.resolveConnectionMode({
        relayAvailable,
        serverSettings: loadServerSettings(),
    });
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
// Refine/retraction/speculative-translation caches are private to their state
// module; app.js only coordinates their effects with token and DOM state.
const refineState = RefineState.createRefineState();

// 由后端下发：默认翻译目标语言（ISO 639-1）
let defaultTranslationTargetLang = 'en';
let currentTranslationTargetLang = 'en';

// Language metadata and transient selector UI live outside the application
// coordinator. The backend remains authoritative for the provider-specific list.
const languageCatalog = LanguageCatalog.create();

function setLanguageListFromCodes(codes) {
    if (languageCatalog.setCodes(codes)) {
        languageUi.invalidate();
    }
}

const languageUi = LanguageUI.create({
    document,
    window,
    storage: localStorage,
    t,
    button: translationLangButton,
    catalog: languageCatalog,
    getState: () => ({
        uiTranslationMode,
        backendTranslationMode,
        twoWaySupported,
        currentTranslationTargetLang,
        defaultTranslationTargetLang,
        backendTargetLang1,
        backendTargetLang2,
        suppressTranslationDisplay,
        translationProvider,
        lockManualControls,
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'currentTranslationTargetLang')) {
            currentTranslationTargetLang = patch.currentTranslationTargetLang;
        }
        if (Object.prototype.hasOwnProperty.call(patch, 'backendTargetLang1')) {
            backendTargetLang1 = patch.backendTargetLang1;
        }
        if (Object.prototype.hasOwnProperty.call(patch, 'backendTargetLang2')) {
            backendTargetLang2 = patch.backendTargetLang2;
        }
    },
    setUiTranslationMode,
    restartRecognition,
    renderSubtitles,
});
languageUi.init();

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
const furiganaService = Furigana.createService({
    kuromoji: window.kuromoji,
    escapeHtml,
    onReady: () => renderSubtitles(),
});
furiganaService.setEnabled(furiganaEnabled, { clearState: false });
const furiganaCache = furiganaService.cache;
const pendingFuriganaRequests = furiganaService.pending;

// 移动端底部留白开关（默认关闭）
let bottomSafeAreaEnabled = settingsStore.loadBottomSafeAreaEnabled();

// 控制标志
let shouldReconnect = true;  // 是否应该自动重连
let isRestarting = false;    // 是否正在重启中
let isPaused = false;        // 是否暂停中
let audioSource = 'system';  // 音频输入来源

const runtimeControls = RuntimeControls.create({
    elements: {
        displayModeButton,
        pauseButton,
        pauseIcon,
        audioSourceButton,
        audioSourceIcon,
        overlayButton,
    },
    fetch,
    storage: localStorage,
    t,
    setControlIcon,
    renderSubtitles,
    sessionCostPause,
    console,
    getState: () => ({
        displayMode,
        isPaused,
        audioSource,
        overlayOpen,
        lockManualControls,
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'displayMode')) displayMode = patch.displayMode;
        if (Object.prototype.hasOwnProperty.call(patch, 'isPaused')) isPaused = patch.isPaused;
        if (Object.prototype.hasOwnProperty.call(patch, 'audioSource')) audioSource = patch.audioSource;
        if (Object.prototype.hasOwnProperty.call(patch, 'overlayOpen')) overlayOpen = patch.overlayOpen;
    },
});

const recognitionControls = RecognitionControls.create({
    elements: {
        restartButton,
        autoRestartButton,
        autoRestartIcon,
        subtitleContainer,
    },
    fetch,
    storage: localStorage,
    t,
    escapeHtml,
    logger: console,
    getState: () => ({
        autoRestartEnabled,
        currentTranslationTargetLang,
        isPaused,
        isRestarting,
        lockManualControls,
        segmentModeSupported,
        shouldReconnect,
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'autoRestartEnabled')) autoRestartEnabled = patch.autoRestartEnabled;
        if (Object.prototype.hasOwnProperty.call(patch, 'isPaused')) isPaused = patch.isPaused;
        if (Object.prototype.hasOwnProperty.call(patch, 'isRestarting')) isRestarting = patch.isRestarting;
        if (Object.prototype.hasOwnProperty.call(patch, 'shouldReconnect')) shouldReconnect = patch.shouldReconnect;
    },
    getSocket: () => ws,
    setSocket: (value) => { ws = value; },
    finalizeCurrentNonFinalTokens,
    clearSubtitleState,
    sessionCostReset,
    updatePauseButtonUi: () => runtimeControls.updatePauseButtonUi(),
    hasUsableWebSocket,
    connect,
});

runtimeControls.init({ refreshOverlay: false });
recognitionControls.init();

const settingsSetup = SettingsSetup.create({
    policy: SettingsPolicy,
    fetch,
    t,
    getState: () => ({
        translationProvider,
        backendBootId,
        setupRequired,
        backendMode,
        backendLoggedIn,
        backendSonioxCustomUrl,
        backendSonioxRegion,
        backendKeySource,
        pushedOverrideBootId,
        uiTranslationMode,
        lockManualControls,
        providerSettings: loadProviderSettings(),
        connectionMode: getConnectionMode(),
        serverSettings: loadServerSettings(),
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'translationProvider')) translationProvider = patch.translationProvider;
        if (Object.prototype.hasOwnProperty.call(patch, 'backendBootId')) backendBootId = patch.backendBootId;
        if (Object.prototype.hasOwnProperty.call(patch, 'setupRequired')) setupRequired = patch.setupRequired;
        if (Object.prototype.hasOwnProperty.call(patch, 'backendMode')) backendMode = patch.backendMode;
        if (Object.prototype.hasOwnProperty.call(patch, 'backendLoggedIn')) backendLoggedIn = patch.backendLoggedIn;
        if (Object.prototype.hasOwnProperty.call(patch, 'pushedOverrideBootId')) pushedOverrideBootId = patch.pushedOverrideBootId;
        if (Object.prototype.hasOwnProperty.call(patch, 'uiTranslationMode')) uiTranslationMode = patch.uiTranslationMode;
    },
    actions: {
        sessionCostReset,
        showToast,
        setUiTranslationMode,
        fetchUiConfig,
    },
});

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
let currentTheme = 'dark';
let lastWindowOnTopState = null;
let enableChromaTheme = false;
const settingsUi = SettingsUI.create({
    document,
    window,
    storage: localStorage,
    actions: {
        openSettings,
        closeSettings,
        returnToModeChooser,
        handleResetAll,
        handleSettingsSave,
    },
});

function getAvailableThemes() {
    return settingsUi.getAvailableThemes(enableChromaTheme);
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
    currentTheme = settingsUi.applyTheme(theme, {
        enableChromaTheme,
        themeIcon,
        setControlIcon,
    });
    if (enableChromaTheme) {
        void syncWindowOnTopByTheme(currentTheme);
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
    runtimeControls.updatePauseButtonUi();
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
    runtimeControls.updateDisplayModeButton();
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
    recognitionControls.updateAutoRestartButton();
}

function applyLockPauseRestartControlsUI() {
    if (restartButton) restartButton.style.display = lockManualControls ? 'none' : '';
    if (pauseButton) pauseButton.style.display = lockManualControls ? 'none' : '';
    if (audioSourceButton) audioSourceButton.style.display = lockManualControls ? 'none' : '';
    if (oscTranslationButton) oscTranslationButton.style.display = lockManualControls ? 'none' : '';
    if (translationLangButton) translationLangButton.style.display = lockManualControls ? 'none' : '';
    if (segmentModeButton) {
        segmentModeButton.style.display = (lockManualControls || !segmentModeSupported) ? 'none' : '';
    }
    if (lockManualControls) autoRestartEnabled = true;
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
        hostedBalance.resetFirstRedeemBonus(data && data.first_redeem_bonus_credits);
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

// Generic settings dropdown primitives remain available to the settings form.
function positionDropdownMenu(trigger, menu) {
    return settingsUi.positionDropdownMenu(trigger, menu);
}

function buildCustomSelect(options, config = {}) {
    return settingsUi.buildCustomSelect(options, config);
}

function updateAudioSourceButton() {
    runtimeControls.updateAudioSourceButton();
}

function fetchInitialAudioSource() {
    return runtimeControls.fetchInitialAudioSource();
}
function renderMicrophoneDevicePicker() {
    return settingsRuntime.renderMicrophoneDevicePicker();
}

function fetchMicrophoneDevices() {
    return settingsRuntime.fetchMicrophoneDevices();
}

function saveMicrophoneDeviceSelection() {
    return settingsRuntime.saveMicrophoneDeviceSelection();
}

function renderAutoRestartPicker() {
    return settingsRuntime.renderAutoRestartPicker();
}

function getStoredHideSpeakerLabelsSetting() {
    return settingsRuntime.getStoredHideSpeakerLabelsSetting();
}

function renderSpeakerLabelsPicker() {
    return settingsRuntime.renderSpeakerLabelsPicker();
}

function renderBundledCjkFontPicker() {
    return settingsRuntime.renderBundledCjkFontPicker();
}

function availableTranslationModes() {
    return [...SettingsRuntime.TRANSLATION_UI_MODES];
}

function updateTranslationModeHint() {
    settingsRuntime.updateTranslationModeHint();
}

function renderTranslationModePicker() {
    return settingsRuntime.renderTranslationModePicker();
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
    return settingsRuntime.renderSegmentModePicker();
}

function renderRuntimeSettingsPickers() {
    return settingsRuntime.renderRuntimeSettingsPickers();
}

function applyRuntimeControlSettings() {
    return settingsRuntime.applyRuntimeControlSettings();
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
        furiganaService.setEnabled(furiganaEnabled);
        renderedSentences.clear();
        renderSubtitles();
        console.log(`Furigana ${furiganaEnabled ? 'enabled' : 'disabled'}`);
    });
}

function restartRecognition(options = {}) {
    return recognitionControls.restartRecognition(options);
}

function triggerAutoRestart() {
    return recognitionControls.triggerAutoRestart();
}

// --- 原生字幕悬浮窗（PySide6）开关 ---
function updateOverlayButton() {
    runtimeControls.updateOverlayButton();
}

function refreshOverlayState() {
    return runtimeControls.refreshOverlayState();
}

void refreshOverlayState();

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
        runtimeControls.syncPauseState(data.paused);
        hostedController.handleSessionFrame(data);
        return;
    }
    if (data.type === 'overlay_visibility') {
        runtimeControls.syncOverlayState(data.visible);
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
        hostedBalance.addLlmCost(data.credits);
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
        hostedController.handleSessionFrame(data);
        return;
    }
    if (data.type === 'session_idle') {
        hostedController.handleSessionFrame(data);
        return;
    }
    if (data.type === 'session_disconnected') {
        console.warn('Recognition session disconnected:', data.reason || 'unknown');
        hostedController.handleSessionFrame(data);
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

function requestFurigana(text) {
    return furiganaService.request(text);
}
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
    return Hosted.Billing.formatRate(value);
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

const { normalizeSonioxRegion } = SettingsPolicy;

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
    const firstBonus = hostedBalance.getFirstRedeemBonus();
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
            && firstBonus.eligible
            && Number(firstBonus.credits) > 0;
        firstBonusHint.textContent = showFirstBonus
            ? t('account_first_redeem_bonus', { credits: formatCredits(firstBonus.credits) })
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

function pushSetup(provider, apiKey, options = {}) {
    return settingsSetup.push(provider, apiKey, options);
}

function directSettingsNeedSetup(input) {
    return settingsSetup.directNeedsSetup(input);
}

function relaySettingsNeedSetup(input) {
    return settingsSetup.relayNeedsSetup(input);
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
    settingsRuntime.applyBundledCjkFontDraft();
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
    settingsRuntime.writeProviderSettingsDraft(settings, provider);

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
    await settingsSetup.syncFromStorage();
}

function maybeForceOpenSettings() {
    const action = SettingsPolicy.resolveForceOpenAction({
        lockManualControls,
        connectionMode: getConnectionMode(),
        serverSettings: loadServerSettings(),
        setupRequired,
    });
    if (action === 'login') openLogin({ forced: true });
    if (action === 'settings') openSettings({ forced: true });
}

function shouldPreopenHostedLogin() {
    return hostedMode.shouldPreopenHostedLogin();
}

function preopenHostedLoginIfNeeded() {
    return hostedMode.preopenHostedLoginIfNeeded();
}

function refreshPreopenedHostedLogin() {
    hostedMode.refreshPreopenedHostedLogin();
}

settingsUi.init({
    settingsButton,
    closeButton: settingsCloseButton,
    cancelButton: settingsCancelButton,
    backButton: settingsModeBackButton,
    resetButton: resetAllButton,
    overlay: settingsOverlay,
    form: settingsForm,
});
if (settingsForm) {
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
const hostedUpdate = HostedUpdate.create({
    Billing: Hosted.Billing,
    window,
    storage: localStorage,
    t,
    showConfirm,
    getState: () => ({
        relayAvailable,
        connectionMode: getConnectionMode(),
        currentVersion: clientVersion,
        latestVersion: clientLatestVersion,
        minimumVersion: clientMinimumVersion,
        updateUrl: clientUpdateUrl,
        notes: clientUpdateNotes,
    }),
    onSwitchDirect: () => {
        const settings = loadServerSettings();
        settings.mode = 'direct';
        settings.modeChosen = true;
        saveServerSettings(settings);
        setModeRadio('direct');
        applyModeSectionsVisibility('direct');
        updateAccountSection();
    },
    elements: {
        overlay: clientUpdateOverlay,
        dialog: clientUpdateDialog,
        title: clientUpdateTitle,
        body: clientUpdateBody,
        currentLabel: clientUpdateCurrentLabel,
        latestLabel: clientUpdateLatestLabel,
        minimumLabel: clientUpdateMinimumLabel,
        currentValue: clientUpdateCurrent,
        latestValue: clientUpdateLatest,
        minimumValue: clientUpdateMinimum,
        notes: clientUpdateNotesEl,
        noUrl: clientUpdateNoUrl,
        directButton: clientUpdateDirectButton,
        laterButton: clientUpdateLaterButton,
        updateButton: clientUpdateButton,
    },
});
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
const hostedMode = HostedMode.create({
    policy: SettingsPolicy,
    document,
    t,
    loadServerSettings,
    saveServerSettings,
    getState: () => ({
        lockManualControls,
        relayAvailable,
        relayServerUrl,
        connectionMode: getConnectionMode(),
    }),
    actions: {
        openLogin,
        hideLogin,
        applyLoginI18n,
        updateLoginSubmitState,
        resetBootGuard: () => { pushedOverrideBootId = null; },
        hideSettingsPanel,
        ensureHostedVersionAllowed,
        syncProviderFromStorage,
        maybeForceOpenSettings,
        updateBalanceBarVisibility,
        setModeRadio,
        applyModeSectionsVisibility,
        updateAccountSection,
        openSettings,
    },
    elements: {
        chooserOverlay: modeChooserOverlay,
        chooser: modeChooserEl,
        relayButton: document.getElementById('modeChooserRelay'),
        directButton: document.getElementById('modeChooserDirect'),
    },
});
const hostedLogin = HostedLogin.create({
    document,
    window,
    navigator,
    fetch,
    t,
    localizeBackendMessage,
    getRuntimeState: () => ({
        lockManualControls,
        relayAvailable,
        relayServerUrl,
        translationProvider,
    }),
    loadServerSettings,
    saveServerSettings,
    loadProviderSettings,
    actions: {
        showToast,
        updateBalanceBarVisibility,
        fetchBalance,
        clearSubtitleState,
        setTranslationModeSynced: (value) => { translationModeSynced = !!value; },
        pushSetup,
        switchToOwnKeyMode,
    },
    elements: {
        overlay: loginOverlay,
        panel: loginPanel,
        form: loginForm,
        closeButton: loginCloseButton,
        userInput: loginUserInput,
        primaryButton: loginPrimaryButton,
        modeBackButton: loginModeBackButton,
        backButton: loginBackButton,
        pasteButton: loginPasteButton,
        codeLink: loginCodeLink,
        errorElement: loginErrorEl,
        manualToggle: document.getElementById('loginManualToggle'),
    },
});
hostedLogin.init();
const balanceBar = document.getElementById('balanceBar');
const balanceActionItem = document.getElementById('balanceActionItem');
const balanceOpenSettingsButton = document.getElementById('balanceOpenSettingsButton');
const hostedBalance = HostedBalance.create({
    Billing: Hosted.Billing,
    document,
    fetch,
    t,
    getRuntimeState: () => ({
        connectionMode: getConnectionMode(),
        backendLoggedIn,
        hasToken: !!loadServerSettings().token,
        translationProvider,
        uiTranslationMode,
        translationUiMode,
        sonioxNoTranslationFactor,
    }),
    onAccountSectionChanged: updateAccountSection,
    onAccountBalanceChanged: updateAccountBalance,
    elements: {
        balanceBar,
        balanceActionItem,
        balanceOpenSettingsButton,
    },
});
if (balanceOpenSettingsButton) {
    balanceOpenSettingsButton.addEventListener('click', () => openSettings({ forced: false }));
}

function setElText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ---- First-launch mode chooser ----
function applyChooserI18n() {
    hostedMode.applyChooserI18n();
}

function openModeChooser() {
    return hostedMode.openModeChooser();
}

function clearConnectionModeChoice() {
    hostedMode.clearConnectionModeChoice();
}

function ensureHostedVersionAllowed(options = {}) {
    return hostedUpdate.ensure(options);
}

function returnToModeChooser() {
    return hostedMode.returnToModeChooser();
}

function switchToOwnKeyMode() {
    return hostedMode.switchToOwnKeyMode();
}

function maybeRunFirstLaunchFlow() {
    return hostedMode.maybeRunFirstLaunchFlow();
}

// ---- Login overlay (web-generated one-time code only) ----
function applyLoginI18n() {
    hostedLogin.applyI18n();
}

function updateLoginSubmitState() {
    hostedLogin.updateSubmitState();
}

function rankLabel(rank) {
    return hostedLogin.rankLabel(rank);
}

function openLogin(options = {}) {
    hostedLogin.open(options);
}

function hideLogin() {
    hostedLogin.hide();
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
            hostedBalance.resetFirstRedeemBonus(data.first_redeem_bonus_credits);
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
    return hostedBalance.formatCredits(value);
}

function updateBalanceBarVisibility() {
    hostedBalance.updateBalanceBarVisibility();
}

function fetchBalance(options = {}) {
    return hostedBalance.fetchBalance(options);
}

function freePoolsSummary(pools) {
    return hostedBalance.freePoolsSummary(pools);
}

function renderFreePools(container, pools) {
    hostedBalance.renderFreePools(container, pools);
}

function currentBalanceView() {
    return hostedBalance.currentBalanceView();
}

function renderBalanceView() {
    hostedBalance.renderBalanceView();
}

function sessionCostResume() {
    hostedBalance.sessionCostResume();
}

function sessionCostPause() {
    hostedBalance.sessionCostPause();
}

function sessionCostReset() {
    hostedBalance.sessionCostReset();
}

const hostedController = Hosted.createController({
    preopenHostedLoginIfNeeded,
    fetchUiConfig,
    refreshPreopenedHostedLogin,
    maybeRunFirstLaunchFlow,
    ensureHostedVersionAllowed,
    syncProviderFromStorage,
    fetchLlmRefineStatus,
    fetchApiKeyStatus,
    fetchOscTranslationStatus,
    maybeForceOpenSettings,
    updateBalanceBarVisibility,
    connect,
    sessionCostResume,
    sessionCostPause,
    isPaused: () => isPaused,
});

document.addEventListener('DOMContentLoaded', () => {
    void hostedController.startup();
});

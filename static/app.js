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
        'OSC translation toggle is disabled by server config': 'backend_osc_disabled',
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

// 由后端下发：refine 时携带的上文条数（默认 3，可为 0）
let llmRefineContextCount = 3;

// 由后端下发：是否展示 refined 译文的修订 diff（无前端开关）
let llmRefineShowDiff = false;

// 由后端下发：diff 高亮时是否显示“被删除”的文本（无前端开关）
let llmRefineShowDeletions = false;

// 由后端下发：是否启用说话人分离
let speakerDiarizationEnabled = true;
// 由后端下发：是否隐藏说话人标签
let hideSpeakerLabels = false;

const LLM_REFINE_MODES = ['off', 'refine', 'translate'];
const LLM_REFINE_ICON = '🪄';
const LLM_TRANSLATE_ICON = '🤖';
let defaultLlmRefineMode = null;

// 译文自动修复开关（默认关闭）
let llmRefineMode = localStorage.getItem('llmRefineMode');
if (!LLM_REFINE_MODES.includes(llmRefineMode)) {
    const legacy = localStorage.getItem('llmRefineEnabled');
    llmRefineMode = legacy === 'true' ? 'refine' : 'off';
}
let llmRefineEnabled = llmRefineMode !== 'off';
let llmTranslateHideAfterSequence = llmRefineMode === 'translate' ? 0 : null;

// 存储后端改进结果
const backendRefinedResults = new Map();
// LLM 直译模式下覆盖 Soniox 译文
const llmTranslationOverrides = new Map();

// 由后端下发：默认翻译目标语言（ISO 639-1）
let defaultTranslationTargetLang = 'en';
let currentTranslationTargetLang = 'en';

const SUPPORTED_TRANSLATION_LANGUAGES = [
    { code: 'af', en: 'Afrikaans', native: 'Afrikaans' },
    { code: 'sq', en: 'Albanian', native: 'Shqip' },
    { code: 'ar', en: 'Arabic', native: 'العربية' },
    { code: 'az', en: 'Azerbaijani', native: 'Azərbaycan dili' },
    { code: 'eu', en: 'Basque', native: 'Euskara' },
    { code: 'be', en: 'Belarusian', native: 'Беларуская' },
    { code: 'bn', en: 'Bengali', native: 'বাংলা' },
    { code: 'bs', en: 'Bosnian', native: 'Bosanski' },
    { code: 'bg', en: 'Bulgarian', native: 'Български' },
    { code: 'ca', en: 'Catalan', native: 'Català' },
    { code: 'zh', en: 'Chinese', native: '中文' },
    { code: 'hr', en: 'Croatian', native: 'Hrvatski' },
    { code: 'cs', en: 'Czech', native: 'Čeština' },
    { code: 'da', en: 'Danish', native: 'Dansk' },
    { code: 'nl', en: 'Dutch', native: 'Nederlands' },
    { code: 'en', en: 'English', native: 'English' },
    { code: 'et', en: 'Estonian', native: 'Eesti' },
    { code: 'fi', en: 'Finnish', native: 'Suomi' },
    { code: 'fr', en: 'French', native: 'Français' },
    { code: 'gl', en: 'Galician', native: 'Galego' },
    { code: 'de', en: 'German', native: 'Deutsch' },
    { code: 'el', en: 'Greek', native: 'Ελληνικά' },
    { code: 'gu', en: 'Gujarati', native: 'ગુજરાતી' },
    { code: 'he', en: 'Hebrew', native: 'עברית' },
    { code: 'hi', en: 'Hindi', native: 'हिन्दी' },
    { code: 'hu', en: 'Hungarian', native: 'Magyar' },
    { code: 'id', en: 'Indonesian', native: 'Bahasa Indonesia' },
    { code: 'it', en: 'Italian', native: 'Italiano' },
    { code: 'ja', en: 'Japanese', native: '日本語' },
    { code: 'kn', en: 'Kannada', native: 'ಕನ್ನಡ' },
    { code: 'kk', en: 'Kazakh', native: 'Қазақша' },
    { code: 'ko', en: 'Korean', native: '한국어' },
    { code: 'lv', en: 'Latvian', native: 'Latviešu' },
    { code: 'lt', en: 'Lithuanian', native: 'Lietuvių' },
    { code: 'mk', en: 'Macedonian', native: 'Македонски' },
    { code: 'ms', en: 'Malay', native: 'Bahasa Melayu' },
    { code: 'ml', en: 'Malayalam', native: 'മലയാളം' },
    { code: 'mr', en: 'Marathi', native: 'मराठी' },
    { code: 'no', en: 'Norwegian', native: 'Norsk' },
    { code: 'fa', en: 'Persian', native: 'فارسی' },
    { code: 'pl', en: 'Polish', native: 'Polski' },
    { code: 'pt', en: 'Portuguese', native: 'Português' },
    { code: 'pa', en: 'Punjabi', native: 'ਪੰਜਾਬੀ' },
    { code: 'ro', en: 'Romanian', native: 'Română' },
    { code: 'ru', en: 'Russian', native: 'Русский' },
    { code: 'sr', en: 'Serbian', native: 'Српски' },
    { code: 'sk', en: 'Slovak', native: 'Slovenčina' },
    { code: 'sl', en: 'Slovenian', native: 'Slovenščina' },
    { code: 'es', en: 'Spanish', native: 'Español' },
    { code: 'sw', en: 'Swahili', native: 'Kiswahili' },
    { code: 'sv', en: 'Swedish', native: 'Svenska' },
    { code: 'tl', en: 'Tagalog', native: 'Tagalog' },
    { code: 'ta', en: 'Tamil', native: 'தமிழ்' },
    { code: 'te', en: 'Telugu', native: 'తెలుగు' },
    { code: 'th', en: 'Thai', native: 'ไทย' },
    { code: 'tr', en: 'Turkish', native: 'Türkçe' },
    { code: 'uk', en: 'Ukrainian', native: 'Українська' },
    { code: 'ur', en: 'Urdu', native: 'اردو' },
    { code: 'vi', en: 'Vietnamese', native: 'Tiếng Việt' },
    { code: 'cy', en: 'Welsh', native: 'Cymraeg' },
];

let langPopoverEl = null;
let langPopoverOpen = false;
let langPopoverCleanup = null;

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

// 自动重启识别开关（默认关闭）
let autoRestartEnabled = localStorage.getItem('autoRestartEnabled') === 'true';

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

// 初始化按钮文本
updateSegmentModeButton();
updateDisplayModeButton();
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
        pauseButton.title = isPaused ? t('resume') : t('pause_resume');
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
            localStorage.setItem('llmRefineMode', llmRefineMode);
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

    translationRefineIcon.textContent = isTranslate ? LLM_TRANSLATE_ICON : LLM_REFINE_ICON;
    translationRefineButton.title = t(getLlmRefineTitleKey());

    if (llmRefineMode !== 'off') {
        translationRefineButton.classList.add('active');
    } else {
        translationRefineButton.classList.remove('active');
    }

    translationRefineButton.classList.toggle('mode-translate', isTranslate);
}


// 主题切换功能（默认深色）
let isDarkTheme = true;
document.body.classList.add('dark-theme');
themeIcon.textContent = '🌙';

// 从localStorage加载主题偏好，覆盖默认值
const savedTheme = localStorage.getItem('theme');
if (savedTheme === 'light') {
    isDarkTheme = false;
    document.body.classList.remove('dark-theme');
    themeIcon.textContent = '☀️';
}

themeToggle.addEventListener('click', () => {
    isDarkTheme = !isDarkTheme;
    
    if (isDarkTheme) {
        document.body.classList.add('dark-theme');
        themeIcon.textContent = '🌙';
        localStorage.setItem('theme', 'dark');
    } else {
        document.body.classList.remove('dark-theme');
        themeIcon.textContent = '☀️';
        localStorage.setItem('theme', 'light');
    }
});

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
        bottomSafeAreaIcon.textContent = '⬆️';
    } else {
        bottomSafeAreaButton.classList.remove('active');
        bottomSafeAreaButton.title = t('bottom_safe_area_off');
        bottomSafeAreaIcon.textContent = '⬇️';
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
        segmentModeButton.style.display = lockManualControls ? 'none' : '';
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
        llmRefineShowDiff = !!data.llm_refine_show_diff;
        llmRefineShowDeletions = !!data.llm_refine_show_deletions;
        if (data && typeof data.llm_refine_default_mode === 'string') {
            defaultLlmRefineMode = normalizeLlmRefineMode(data.llm_refine_default_mode);
        }
        if (data && Number.isFinite(data.llm_refine_context_count)) {
            llmRefineContextCount = Math.max(0, Math.trunc(data.llm_refine_context_count));
        }
        if (data && typeof data.translation_target_lang === 'string' && data.translation_target_lang.trim()) {
            defaultTranslationTargetLang = data.translation_target_lang.trim().toLowerCase();
            currentTranslationTargetLang = defaultTranslationTargetLang;
        }
        if (data && typeof data.segment_mode === 'string' && data.segment_mode.trim()) {
            segmentMode = data.segment_mode.trim();
            localStorage.setItem('segmentMode', segmentMode);
            updateSegmentModeButton();
        }
        if (data && typeof data.speaker_diarization_enabled === 'boolean') {
            speakerDiarizationEnabled = data.speaker_diarization_enabled;
        }
        if (data && typeof data.hide_speaker_labels === 'boolean') {
            hideSpeakerLabels = data.hide_speaker_labels;
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

        const rawStoredMode = localStorage.getItem('llmRefineMode');
        const hasStoredMode = LLM_REFINE_MODES.includes((rawStoredMode || '').toString().trim().toLowerCase());
        const storedMode = hasStoredMode ? normalizeLlmRefineMode(rawStoredMode) : null;

        if (lockManualControls) {
            applyLlmRefineMode(preferredDefault);
            return;
        }

        if (hasStoredMode && storedMode) {
            if (storedMode !== serverMode) {
                void setLlmRefineMode(storedMode);
            } else {
                applyLlmRefineMode(storedMode);
            }
            return;
        }

        if (preferredDefault !== serverMode) {
            void setLlmRefineMode(preferredDefault);
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
    const noChange = !!data.no_change;

    if (!source || !originalTranslation) {
        return;
    }

    if (!noChange && refinedTranslation) {
        const key = `${source}||${originalTranslation}`;
        backendRefinedResults.set(key, refinedTranslation);
        if (isLlmTranslateMode()) {
            llmTranslationOverrides.set(key, refinedTranslation);
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
    enforceTranslateSegmentMode();
}

function ensureLangPopover() {
    if (langPopoverEl) {
        return langPopoverEl;
    }

    const el = document.createElement('div');
    el.className = 'lang-popover';
    el.style.display = 'none';

    for (const lang of SUPPORTED_TRANSLATION_LANGUAGES) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'lang-option';
        btn.dataset.code = lang.code;
        btn.textContent = `${lang.en} - ${lang.native}`;
        btn.addEventListener('click', () => {
            const selected = btn.dataset.code;
            hideLangPopover();
            if (!selected) {
                return;
            }
            if (selected === currentTranslationTargetLang) {
                return;
            }
            currentTranslationTargetLang = selected;
            void restartRecognition({ auto: false, targetLang: selected });
        });
        el.appendChild(btn);
    }

    document.body.appendChild(el);
    langPopoverEl = el;
    return el;
}

function updateLangPopoverSelection() {
    if (!langPopoverEl) {
        return;
    }
    const buttons = langPopoverEl.querySelectorAll('.lang-option');
    buttons.forEach((btn) => {
        const code = btn.dataset.code;
        btn.classList.toggle('selected', code === currentTranslationTargetLang);
    });
}

function showLangPopover() {
    if (!translationLangButton) {
        return;
    }
    const el = ensureLangPopover();
    updateLangPopoverSelection();

    const rect = translationLangButton.getBoundingClientRect();
    const padding = 8;

    el.style.display = 'block';

    const popoverRect = el.getBoundingClientRect();

    // Place to the left of the button bar, vertically aligned with button.
    let top = rect.top - 10;
    if (top < padding) top = padding;
    if (top + popoverRect.height > window.innerHeight - padding) {
        top = Math.max(padding, window.innerHeight - padding - popoverRect.height);
    }

    let left = rect.left - popoverRect.width - 12;
    if (left < padding) {
        left = padding;
    }

    el.style.top = `${top}px`;
    el.style.left = `${left}px`;

    langPopoverOpen = true;

    const onDocMouseDown = (event) => {
        const target = event.target;
        if (!target) {
            return;
        }
        if (langPopoverEl && langPopoverEl.contains(target)) {
            return;
        }
        if (translationLangButton && translationLangButton.contains(target)) {
            return;
        }
        hideLangPopover();
    };

    const onKeyDown = (event) => {
        if (event.key === 'Escape') {
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
        } else {
            console.error('Failed to set LLM refine');
        }
    } catch (error) {
        console.error('Error setting LLM refine:', error);
    }
}

function updateAudioSourceButton() {
    if (!audioSourceButton || !audioSourceIcon) {
        return;
    }

    if (audioSource === 'microphone') {
        audioSourceIcon.textContent = '🎤';
        audioSourceButton.title = t('audio_to_system');
    } else {
        audioSourceIcon.textContent = '🔊';
        audioSourceButton.title = t('audio_to_mic');
    }
}

async function fetchInitialAudioSource() {
    try {
        const stored = localStorage.getItem('audioSource');
        if (stored === 'system' || stored === 'microphone') {
            audioSource = stored;
            updateAudioSourceButton();
        }
    } catch (storageError) {
        console.warn('Unable to access stored audio source preference:', storageError);
    }

    try {
        const response = await fetch('/audio-source');
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        if (data && (data.source === 'system' || data.source === 'microphone')) {
            audioSource = data.source;
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

async function setSegmentMode(mode) {
    if (lockManualControls) {
        return;
    }
    if (isLlmTranslateMode() && mode === 'translation') {
        return;
    }
    try {
        const response = await fetch('/segment-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode })
        });
        if (!response.ok) {
            console.error('Failed to set segment mode');
        }
    } catch (error) {
        console.error('Error setting segment mode:', error);
    }
}

// 分段模式切换
segmentModeButton.addEventListener('click', () => {
    const availableModes = getSegmentModes();
    const currentIndex = availableModes.indexOf(segmentMode);
    const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % availableModes.length : 0;
    const nextMode = availableModes[nextIndex];
    void setSegmentMode(nextMode);
});

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

async function restartRecognition({ auto = false, targetLang = null } = {}) {
    if (isRestarting) {
        return false;
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
        if (ws) {
            console.log('Closing old WebSocket connection...');
            try {
                ws.close();
            } catch (closeError) {
                console.warn('WebSocket close during restart raised an error:', closeError);
            }
            ws = null;
        }

        clearSubtitleState();

        if (!auto) {
            subtitleContainer.innerHTML = manualStatusHtml;
        }

        await delay(500);

        const payload = { auto: !!auto };
        const lang = (targetLang || currentTranslationTargetLang || '').toString().trim().toLowerCase();
        if (lang) {
            payload.target_lang = lang;
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

        console.log(auto ? 'Auto restart: new recognition session requested.' : 'Recognition restarted successfully');

        await delay(1500);

        shouldReconnect = true;
        connect();
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
                pauseIcon.textContent = '⏸️';
                pauseButton.title = t('pause');
                console.log('Recognition resumed');
            }
        } else {
            // 暂停识别
            const response = await fetch('/pause', { method: 'POST' });
            if (response.ok) {
                isPaused = true;
                pauseIcon.textContent = '▶️';
                pauseButton.title = t('resume');
                console.log('Recognition paused');
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
        const nextSource = audioSource === 'system' ? 'microphone' : 'system';

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
                audioSource = result.source;
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
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws${window.location.search}`);

    ws.onopen = () => {
        console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleMessage(data);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    ws.onclose = () => {
        console.log('WebSocket closed');

        if (autoRestartEnabled) {
            if (isRestarting) {
                console.log('Restart already in progress; skipping auto restart trigger.');
                return;
            }

            restartRecognition({ auto: true })
                .then((success) => {
                    if (!success && shouldReconnect && !isRestarting) {
                        console.log('Attempting to reconnect in 2 seconds...');
                        setTimeout(connect, 2000);
                    }
                })
                .catch((error) => {
                    console.error('Auto restart promise rejected:', error);
                    if (shouldReconnect && !isRestarting) {
                        console.log('Attempting to reconnect in 2 seconds...');
                        setTimeout(connect, 2000);
                    }
                });
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
    if (data.type === 'error') {
        displayErrorMessage(data.message);
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
    if (data.type === 'clear') {
        // 清空所有数据
        console.log('Clearing all subtitles...');
        clearSubtitleState();
        // 不修改UI,因为重启流程会处理
        return;
    }
    
    if (data.type === 'update') {
        let hasNewFinalContent = false;
        let hasSeparator = false;
        if (data.final_tokens && data.final_tokens.length > 0) {
            data.final_tokens.forEach(token => {
                if (token.text === '<end>') {
                    return;
                }
                if (token.is_separator) {
                    hasSeparator = true;
                }
                hasNewFinalContent = true;
                insertFinalToken(token);
            });
        }

        // 更新non-final tokens并过滤 <end>
        currentNonFinalTokens = (data.non_final_tokens || []).filter(token => token.text !== '<end>');
        currentNonFinalTokens.forEach(assignSequenceIndex);

        if (hasSeparator) {
            currentNonFinalTokens = [];
        }

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

        allFinalTokens[writeIndex] = mergedToken;
        writeIndex++;
        readIndex = nextIndex;
    }

    // 截断数组，移除已合并的重复项
    allFinalTokens.length = writeIndex;

    // 更新lastMergedIndex到新的末尾
    lastMergedIndex = allFinalTokens.length;
}

function getLanguageTag(language) {
    if (!language) return '';
    
    // 直接显示语言代码，支持任何语言
    return `<span class="language-tag">${language.toUpperCase()}</span>`;
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

function clearSubtitleState() {
    allFinalTokens = [];
    currentNonFinalTokens = [];
    lastMergedIndex = 0;
    renderedSentences.clear();
    renderedBlocks.clear();
    tokenSequenceCounter = 0;
    pendingFuriganaRequests.clear();

    backendRefinedResults.clear();
    llmTranslationOverrides.clear();
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

function renderTokenSpansTrimmed(tokens, useRubyHtml = null) {
    // Render token spans but remove leading/trailing whitespace from the concatenated output.
    // IMPORTANT: does NOT mutate token objects; trimming is display-only.
    if (!Array.isArray(tokens) || tokens.length === 0) {
        return '';
    }

    const getText = (tok) => ((tok && tok.text) ? String(tok.text) : '');

    let start = 0;
    let startText = '';
    while (start < tokens.length) {
        const raw = getText(tokens[start]);
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
        const raw = getText(tokens[end]);
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
        const raw = getText(tokens[start]);
        const both = raw.trim();
        if (!both) {
            return '';
        }
        return renderTokenSpanWithText(tokens[start], both, useRubyHtml);
    }

    const parts = [];
    for (let i = start; i <= end; i++) {
        const tok = tokens[i];
        let txt = getText(tok);
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

function renderSubtitles() {
    const scrollState = captureScrollState();
    const tokens = [...allFinalTokens, ...currentNonFinalTokens];
    tokens.forEach(assignSequenceIndex);

    if (tokens.length === 0) {
        subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
        subtitleContainer.scrollTop = 0;
        autoStickToBottom = true;
        return;
    }

    const sentences = [];
    let currentSentence = null;
    let pendingTranslationSentence = null;

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

    const canAcceptTranslation = (sentence, token) => {
        if (!sentence) return false;
        if (sentence.hasFakeTranslation) return false;

        if (sentence.isTranslationOnly) {
            if (sentence.originalLang && token.source_language && sentence.originalLang !== token.source_language) {
                return false;
            }
            if (sentence.translationLang && token.language && sentence.translationLang !== token.language) {
                return false;
            }
            return true;
        }

        if (sentence.requiresTranslation === false) return false;

        if (token.source_language && sentence.originalLang && sentence.originalLang !== token.source_language) {
            return false;
        }

        if (sentence.translationLang && token.language && sentence.translationLang !== token.language) {
            return false;
        }

        return true;
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
            // 分隔符也会打断 pending 状态，迫使新的译文重新寻找匹配
            pendingTranslationSentence = null;
            return;
        }

        const speaker = ensureSpeakerValue(token.speaker);
        const translationStatus = token.translation_status || 'original';

        if (translationStatus === 'translation') {
            let targetSentence = null;

            // 1. 尝试匹配 pending
            if (pendingTranslationSentence && pendingTranslationSentence.speaker === speaker && canAcceptTranslation(pendingTranslationSentence, token)) {
                targetSentence = pendingTranslationSentence;
            }

            // 2. 尝试匹配该说话人最近的一个可接受译文的句子
            if (!targetSentence) {
                targetSentence = findLastSentenceForSpeaker(
                    speaker,
                    (sentence) => canAcceptTranslation(sentence, token),
                    {
                        stopOnFakeTranslation: true
                    }
                );
            }

            // 3. 如果都匹配不到，创建一个纯译文句子
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
            pendingTranslationSentence = targetSentence;

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

    const showOriginal = (displayMode === 'both' || displayMode === 'original');
    const showTranslation = (displayMode === 'both' || displayMode === 'translation');

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

        if (speakerDiarizationEnabled && !hideSpeakerLabels && block.speaker !== previousSpeaker) {
            blockHtml += `<div class="speaker-label ${getSpeakerClass(block.speaker)}">${escapeHtml(t('speaker_label', { speaker: block.speaker }))}</div>`;
        }

        const sentencesHtml = [];

        for (const sentence of block.sentences) {
            const sentenceId = getSentenceId(sentence, fallbackCounter++);
            activeSentenceIds.add(sentenceId);

            const sentenceParts = [];

            if (showOriginal && sentence.originalTokens.length > 0) {
                const langTag = getLanguageTag(sentence.originalLang);
                const isJapanese = sentence.originalLang === 'ja';

                if (isJapanese && furiganaEnabled) {
                    const plainText = sentence.originalTokens.map(t => t.text).join('');
                    const hasNonFinal = sentence.originalTokens.some(t => !t.is_final);

                    if (plainText.trim().length === 0) {
                        const lineContent = sentence.originalTokens.map(t => renderTokenSpan(t)).join('');
                        sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${lineContent}</div>`);
                    } else {
                        const rubyHtml = furiganaCache.get(plainText);

                        if (rubyHtml) {
                            const classes = ['subtitle-text'];
                            if (hasNonFinal) {
                                classes.push('non-final');
                            }
                            const rubySpan = `<span class="${classes.join(' ')}">${rubyHtml}</span>`;
                            sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${rubySpan}</div>`);
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
                    sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${lineContent}</div>`);
                }
            }

            if (blockingUpdate) {
                break;
            }

            if (showTranslation && sentence.translationTokens.length > 0) {
                const langTag = getLanguageTag(sentence.translationLang);
                const baseTranslation = sentence.translationTokens.map(t => (t && t.text) ? String(t.text) : '').join('');
                let baseTranslationNormalized = baseTranslation.trim();

                const sourceText = sentence.originalTokens.map(t => (t && t.text) ? String(t.text) : '').join('').trim();
                const key = (sourceText && baseTranslationNormalized)
                    ? `${sourceText}||${baseTranslationNormalized}`
                    : null;
                const overrideTranslation = key ? llmTranslationOverrides.get(key) : null;
                if (overrideTranslation) {
                    baseTranslationNormalized = overrideTranslation;
                }
                const hasRefined = key ? backendRefinedResults.has(key) : false;
                const shouldHide = shouldHideSonioxTranslation(sentence, sourceText, hasRefined);

                if (!shouldHide) {
                    const displayTranslation = overrideTranslation
                        ? overrideTranslation
                        : ((sourceText && baseTranslationNormalized)
                            ? getDisplayTranslation(sourceText, baseTranslationNormalized)
                            : baseTranslationNormalized);

                    if (displayTranslation && displayTranslation !== baseTranslationNormalized) {
                        const showDiff = llmRefineShowDiff && !isLlmTranslateMode();
                        const html = showDiff
                            ? renderTranslationDiffHtml(baseTranslationNormalized, displayTranslation)
                            : escapeHtml(displayTranslation);
                        sentenceParts.push(`<div class="subtitle-line">${langTag}<span class="subtitle-text">${html}</span></div>`);
                    } else if (overrideTranslation) {
                        const html = escapeHtml(displayTranslation || '');
                        sentenceParts.push(`<div class="subtitle-line">${langTag}<span class="subtitle-text">${html}</span></div>`);
                    } else {
                        const lineContent = renderTokenSpansTrimmed(sentence.translationTokens);
                        sentenceParts.push(`<div class="subtitle-line">${langTag}${lineContent}</div>`);
                    }
                } else {
                    const placeholderText = '&nbsp;';
                    sentenceParts.push(`<div class="subtitle-line">${langTag}<span class="subtitle-text placeholder">${placeholderText}</span></div>`);
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
            html += `<div class="${blockClass}">${blockHtml}</div>`;
            previousSpeaker = block.speaker;
        }
    }

    if (blockingUpdate) {
        return;
    }

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
        // 以 subtitle-block 为单位进行增量更新，保证 speaker label 与分块结构被保留
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
        });

        const keepIds = new Set();

        // 遍历新的 subtitle-block，比较并替换/插入
        for (let i = 0; i < newBlocks.length; i++) {
            const newBlock = newBlocks[i];
            // 为新块生成稳定 id（基于其首个 sentence 的 id）
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

            const newHtml = newBlock.innerHTML;
            const existingNode = existingIndex.get(id);

            if (existingNode) {
                // 内容相同则跳过
                if (renderedBlocks.get(id) === newHtml) {
                    keepIds.add(id);
                    continue;
                }
                // 替换整个 subtitle-block 节点（保留新的 speaker label 和结构）
                const wrapper = document.createElement('div');
                wrapper.className = newBlock.className || 'subtitle-block';
                wrapper.dataset.blockId = id;
                wrapper.innerHTML = newHtml;
                existingNode.replaceWith(wrapper);
                renderedBlocks.set(id, newHtml);
                keepIds.add(id);
            } else {
                // 新的 subtitle-block，需要插入：尝试按新Blocks 中下一个已有块定位插入点
                const wrapper = document.createElement('div');
                wrapper.className = newBlock.className || 'subtitle-block';
                wrapper.dataset.blockId = id;
                wrapper.innerHTML = newHtml;

                let inserted = false;
                for (let j = i + 1; j < newBlocks.length; j++) {
                    const nextFirst = newBlocks[j].querySelector('.sentence-block');
                    const nextId = nextFirst && nextFirst.dataset.sentenceId ? `block-${nextFirst.dataset.sentenceId}` : newBlocks[j].dataset.blockId;
                    if (!nextId) continue;
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
                renderedBlocks.set(id, newHtml);
                keepIds.add(id);
            }
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

document.addEventListener('DOMContentLoaded', () => {
    (async () => {
        await fetchUiConfig();
        await fetchLlmRefineStatus();
        fetchApiKeyStatus();
        fetchOscTranslationStatus();
        connect();
    })();
});
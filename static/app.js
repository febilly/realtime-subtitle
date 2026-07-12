const customTooltip = CustomTooltip.create({ document, window });
customTooltip.init();

let ws;
let wsClient = null;
const subtitleContainer = document.getElementById('subtitleContainer');
const subtitleScroll = SubtitleScroll.create({ container: subtitleContainer, window });
subtitleScroll.init();
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

const localizeBackendMessage = BackendMessage.createLocalizer({ t });

const INITIAL_UI_CONFIG = (window.__INITIAL_UI_CONFIG__ && typeof window.__INITIAL_UI_CONFIG__ === 'object')
    ? window.__INITIAL_UI_CONFIG__
    : {};

// 由后端下发：锁定“手动控制”相关 UI
let lockManualControls = !!INITIAL_UI_CONFIG.lock_manual_controls;

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
const confirmController = ConfirmDialog.create({
    document,
    window,
    t,
    elements: {
        overlay: confirmOverlay,
        dialog: confirmDialog,
        message: confirmMessageEl,
        okButton: confirmOkButton,
        cancelButton: confirmCancelButton,
    },
});
confirmController.init();
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
const uiFeedbackController = UiFeedbackController.create({
    document,
    fetch,
    subtitleContainer,
    toast: document.getElementById('toast'),
    t,
    localizeBackendMessage,
    escapeHtml,
    console,
});
const {
    displayErrorMessage,
    fetchApiKeyStatus,
    showToast,
} = uiFeedbackController;

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
        get hideSpeakerLabels() { return speakerLabelController.isHidden(); },
        get customFontAvailable() { return customFontAvailable; },
        get useBundledCjkFont() { return useBundledCjkFont; },
        get llmRefineAvailable() { return translationModeController.isAvailable(); },
        get lockManualControls() { return lockManualControls; },
        get translationUiMode() { return translationModeController.getTranslationUiMode(); },
        get defaultTranslationUiMode() { return TranslationModeController.DEFAULT_TRANSLATION_UI_MODE; },
        get translationUiModes() { return translationModeController.getAvailableTranslationModes(); },
        get segmentModeSupported() { return segmentModeSupported; },
        get segmentMode() { return segmentModeController.getMode(); },
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
// STT billing factor for soniox 准确 mode (built-in translation off), delivered by
// the server via /ui-config. 1 = no discount; applied to the live cost estimate.
let sonioxNoTranslationFactor = 1;
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

const speakerLabelController = SpeakerLabelController.create({
    fetch,
    container: subtitleContainer,
    console,
    getRuntimeState: () => ({ lockManualControls, translationProvider }),
    getStoredPreference: () => settingsRuntime.getStoredHideSpeakerLabelsSetting(),
    renderPicker: () => settingsRuntime.renderSpeakerLabelsPicker(),
    renderSubtitles,
});
const oscTranslationController = OscTranslationController.create({
    fetch,
    button: oscTranslationButton,
    icon: oscTranslationIcon,
    t,
    console,
});
oscTranslationController.init();
const mobileSafeAreaController = MobileSafeAreaController.create({
    settingsStore,
    storage: localStorage,
    button: bottomSafeAreaButton,
    icon: bottomSafeAreaIcon,
    container: subtitleContainer,
    userAgent: navigator.userAgent,
    t,
    setControlIcon,
    console,
});
mobileSafeAreaController.init();
const furiganaToggleController = FuriganaToggleController.create({
    storage: sessionStorage,
    button: furiganaButton,
    icon: furiganaIcon,
    t,
    console,
    onChange: (enabled) => {
        furiganaService.setEnabled(enabled);
        subtitleRenderer.invalidateSentences();
        renderSubtitles();
    },
});
furiganaToggleController.init();
const segmentModeController = SegmentModeController.create({
    fetch,
    storage: localStorage,
    settingsStore,
    button: segmentModeButton,
    t,
    console,
    getRuntimeState: () => ({ lockManualControls, segmentModeSupported }),
    isTranslateMode: () => translationModeController.isTranslateMode(),
    renderPicker: () => settingsRuntime.renderSegmentModePicker(),
});
segmentModeController.init();
const translationModeController = TranslationModeController.create({
    fetch,
    storage: localStorage,
    settingsStore,
    console,
    getSession: () => subtitleSession,
    getRuntimeState: () => ({ lockManualControls }),
    actions: {
        enforceTranslateSegmentMode: segmentModeController.enforceTranslateMode,
        updateSegmentModeButton: segmentModeController.updateButton,
        renderSubtitles,
        updateTranslationModeHint,
        renderTranslationModePicker,
        restartRecognition,
    },
});
const subtitleSession = SubtitleSession.create({
    TokenStream,
    RenderModel,
    RefineState,
    translateMode: translationModeController.isTranslateMode(),
    translationUiMode: translationModeController.getTranslationUiMode(),
});

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

// 显示模式: 'both', 'original', 'translation'
let displayMode = settingsStore.loadDisplayMode();

// 自动重启识别开关（默认开启；已有保存值优先）
let autoRestartEnabled = settingsStore.loadAutoRestartEnabled();

const furiganaService = Furigana.createService({
    kuromoji: window.kuromoji,
    escapeHtml,
    onReady: () => renderSubtitles(),
});
furiganaService.setEnabled(furiganaToggleController.isEnabled(), { clearState: false });
const subtitleRenderer = SubtitleRenderer.create({
    document,
    container: subtitleContainer,
    session: subtitleSession,
    scroll: subtitleScroll,
    furiganaService,
    RenderModel,
    RenderHtml,
    Segmentation,
    t,
    escapeHtml,
    getViewState: () => ({
        displayMode,
        suppressTranslationDisplay,
        translateMode: isLlmTranslateMode(),
        translationUiMode: translationModeController.getTranslationUiMode(),
        currentTranslationTargetLang,
        furiganaEnabled: furiganaToggleController.isEnabled(),
        speakerDiarizationEnabled: speakerLabelController.isDiarizationEnabled(),
        hideSpeakerLabels: speakerLabelController.isHidden(),
    }),
});
const subtitleFrameController = SubtitleFrameController.create({
    session: subtitleSession,
    renderer: subtitleRenderer,
    console,
    getState: () => ({ translateMode: isLlmTranslateMode() }),
    renderSubtitles,
    finalizeCurrentNonFinalTokens,
    clearSubtitleState,
});

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

const appShellController = AppShellController.create({
    document,
    window,
    t,
    elements: {
        themeToggle,
        restartButton,
        pauseButton,
        audioSourceButton,
        overlayButton,
        settingsButton,
        translationLangButton,
        segmentModeButton,
        oscTranslationButton,
        subtitleContainer,
        ipcStatusButton: document.getElementById('ipcStatusButton'),
    },
    getState: () => ({ lockManualControls, segmentModeSupported }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'autoRestartEnabled')) {
            autoRestartEnabled = patch.autoRestartEnabled;
        }
    },
    actions: {
        updatePauseButtonUi: runtimeControls.updatePauseButtonUi,
        updateOverlayButton: runtimeControls.updateOverlayButton,
        updateAutoRestartButton: recognitionControls.updateAutoRestartButton,
    },
});
const sessionFrameController = SessionFrameController.create({
    t,
    console,
    getState: () => ({ lockManualControls, autoRestartEnabled, isRestarting }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'backendLoggedIn')) {
            backendLoggedIn = patch.backendLoggedIn;
        }
    },
    actions: {
        syncPauseState: runtimeControls.syncPauseState,
        handleHostedSessionFrame: (frame) => hostedController.handleSessionFrame(frame),
        showToast,
        openSettings,
        loadServerSettings,
        saveServerSettings,
        updateBalanceBarVisibility,
        openLogin,
        triggerAutoRestart,
    },
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
appShellController.applyManualControlPolicy();
appShellController.applyStaticText();

function applySpeakerLabelVisibility() {
    return speakerLabelController.applyVisibility();
}

function isLlmTranslateMode() {
    return translationModeController.isTranslateMode();
}

function enforceTranslateSegmentMode() {
    return segmentModeController.enforceTranslateMode();
}

function getSegmentModes() {
    return segmentModeController.getAvailableModes();
}

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
const themeController = ThemeController.create({
    settingsUi,
    fetch,
    storage: localStorage,
    toggle: themeToggle,
    themeIcon,
    setControlIcon,
});
themeController.init();

function updatePauseButtonUi() {
    runtimeControls.updatePauseButtonUi();
}

// 更新分段模式按钮文本
function updateSegmentModeButton() {
    return segmentModeController.updateButton();
}

// 更新显示模式按钮文本
function updateDisplayModeButton() {
    runtimeControls.updateDisplayModeButton();
}

function updateOscTranslationButton() {
    return oscTranslationController.updateButton();
}

function updateBottomSafeAreaButton() {
    return mobileSafeAreaController.updateButton();
}

function applyBottomSafeArea() {
    return mobileSafeAreaController.apply();
}

function updateAutoRestartButton() {
    recognitionControls.updateAutoRestartButton();
}

function updateUiConfigState(patch) {
    for (const [key, value] of Object.entries(patch || {})) {
        switch (key) {
            case 'lockManualControls': lockManualControls = value; break;
            case 'sonioxNoTranslationFactor': sonioxNoTranslationFactor = value; break;
            case 'defaultTranslationTargetLang': defaultTranslationTargetLang = value; break;
            case 'currentTranslationTargetLang': currentTranslationTargetLang = value; break;
            case 'translationProvider': translationProvider = value; break;
            case 'segmentModeSupported': segmentModeSupported = value; break;
            case 'twoWaySupported': twoWaySupported = value; break;
            case 'backendBootId': backendBootId = value; break;
            case 'setupRequired': setupRequired = value; break;
            case 'envKeyPresent': envKeyPresent = value; break;
            case 'backendKeySource': backendKeySource = value; break;
            case 'backendSonioxRegion': backendSonioxRegion = value; break;
            case 'backendSonioxCustomUrl': backendSonioxCustomUrl = value; break;
            case 'relayAvailable': relayAvailable = value; break;
            case 'relayServerUrl': relayServerUrl = value; break;
            case 'creditsPurchaseUrl': creditsPurchaseUrl = value; break;
            case 'clientVersion': clientVersion = value; break;
            case 'clientLatestVersion': clientLatestVersion = value; break;
            case 'clientMinimumVersion': clientMinimumVersion = value; break;
            case 'clientUpdateUrl': clientUpdateUrl = value; break;
            case 'clientUpdateNotes': clientUpdateNotes = value; break;
            case 'backendMode': backendMode = value; break;
            case 'backendLoggedIn': backendLoggedIn = value; break;
            case 'backendTranslationMode': backendTranslationMode = value; break;
            case 'backendTargetLang1': backendTargetLang1 = value; break;
            case 'backendTargetLang2': backendTargetLang2 = value; break;
            case 'uiTranslationMode': uiTranslationMode = value; break;
            case 'suppressTranslationDisplay': suppressTranslationDisplay = value; break;
            case 'customFontAvailable': customFontAvailable = value; break;
            default: break;
        }
    }
}

const uiConfigController = UiConfigController.create({
    fetch,
    safeHttpUrl,
    normalizeSonioxRegion: SettingsPolicy.normalizeSonioxRegion,
    translationModeController,
    segmentModeController,
    themeController,
    speakerLabelController,
    console,
    getState: () => ({
        backendBootId,
        translationProvider,
        backendTranslationMode,
        uiTranslationMode,
        lockManualControls,
    }),
    updateState: updateUiConfigState,
    actions: {
        sessionCostReset,
        setLanguageListFromCodes,
        resetFirstRedeemBonus: (value) => hostedBalance.resetFirstRedeemBonus(value),
        updateBalanceBarVisibility,
        updateAccountSection,
        updateSettingsButtonVisibility,
        applyBundledCjkFontPreference,
        renderBundledCjkFontPicker,
        renderRuntimeSettingsPickers,
        applyLockPauseRestartControlsUI: appShellController.applyManualControlPolicy,
        enforceTranslateSegmentMode,
    },
});

function fetchUiConfig() {
    return uiConfigController.fetch();
}

function fetchLlmRefineStatus() {
    return translationModeController.fetchLlmRefineStatus();
}

function handleSegmentModeChanged(data) {
    return segmentModeController.handleBackendChanged(data);
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

function renderAutoRestartPicker() {
    return settingsRuntime.renderAutoRestartPicker();
}

function renderBundledCjkFontPicker() {
    return settingsRuntime.renderBundledCjkFontPicker();
}

function updateTranslationModeHint() {
    settingsRuntime.updateTranslationModeHint();
}

function renderTranslationModePicker() {
    return settingsRuntime.renderTranslationModePicker();
}

function setTranslationUiMode(mode, options = {}) {
    return translationModeController.setTranslationUiMode(mode, options);
}

function renderSegmentModePicker() {
    return settingsRuntime.renderSegmentModePicker();
}

function renderRuntimeSettingsPickers() {
    return settingsRuntime.renderRuntimeSettingsPickers();
}

function setSpeakerLabelsHidden(hidden) {
    return speakerLabelController.setHidden(hidden);
}

function setSegmentMode(mode) {
    return segmentModeController.setMode(mode);
}

// 假名注音开关
function updateFuriganaButton() {
    return furiganaToggleController.updateButton();
}

function restartRecognition(options = {}) {
    return recognitionControls.restartRecognition(options);
}

function triggerAutoRestart() {
    return recognitionControls.triggerAutoRestart();
}

// --- 原生字幕悬浮窗（PySide6）开关 ---
function refreshOverlayState() {
    return runtimeControls.refreshOverlayState();
}

void refreshOverlayState();

function fetchOscTranslationStatus() {
    return oscTranslationController.fetchStatus();
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
    if (subtitleFrameController.handle(data)) return;
    if (sessionFrameController.handle(data)) return;
    if (data.type === 'subtitle_font_preference') {
        const enabled = !!data.use_bundled_cjk_fonts;
        applyBundledCjkFontPreference(enabled, { persist: true });
        return;
    }
    if (data.type === 'overlay_visibility') {
        runtimeControls.syncOverlayState(data.visible);
        return;
    }
    if (data.type === 'ipc_status') {
        appShellController.syncIpcStatus(data.connected);
        return;
    }
    if (data.type === 'error') {
        displayErrorMessage(data.message);
        if (data.code === 'api_key' && !lockManualControls) {
            openSettings({ forced: true });
        }
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
        speakerLabelController.handleBackendChanged(data);
        return;
    }
}

const joinTokenText = TokenStream.joinTokenText;
function hasUsableWebSocket() {
    return wsClient
        ? wsClient.isUsable()
        : !!ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
}

function finalizeCurrentNonFinalTokens({ render = true } = {}) {
    const result = subtitleSession.finalizeCurrentNonFinalTokens();
    if (!result.changed) return false;
    subtitleRenderer.invalidateAll();
    if (render) renderSubtitles();
    return true;
}

function clearSubtitleState() {
    subtitleSession.clear({
        translateMode: isLlmTranslateMode(),
        translationUiMode: translationModeController.getTranslationUiMode(),
    });
    subtitleRenderer.clearSession();
}


function renderSubtitles() {
    return subtitleRenderer.render();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===================== Settings panel (provider + API key) =====================

const settingsPanelController = SettingsPanel.create({
    policy: SettingsPolicy,
    billing: Hosted.Billing,
    document,
    fetch,
    t,
    buildCustomSelect,
    loadProviderSettings,
    freePoolsSummary,
    getState: () => ({
        lockManualControls,
        relayAvailable,
        connectionMode: getConnectionMode(),
        translationProvider,
        backendSonioxRegion,
        backendSonioxCustomUrl,
        envKeyPresent,
        setupRequired,
        clientVersion,
        canRefreshBalance: backendLoggedIn || !!loadServerSettings().token,
    }),
    actions: {
        renderMicrophoneDevicePicker,
        renderRuntimeSettingsPickers,
        renderBundledCjkFontPicker,
        renderTranslationModePicker,
        fetchMicrophoneDevices,
        fetchBalance,
        updateAccountSection,
    },
    elements: {
        settingsButton,
        overlayButton,
        overlay: settingsOverlay,
        panel: settingsPanel,
        form: settingsForm,
        closeButton: settingsCloseButton,
        cancelButton: settingsCancelButton,
        saveButton: settingsSaveButton,
        backButton: settingsModeBackButton,
        resetButton: resetAllButton,
        errorElement: settingsErrorEl,
        apiKeyLabel: document.getElementById('apiKeyLabel'),
        apiKeyInput,
        apiKeySourceHint,
        providerDescription,
        apiKeyGetLink,
        sonioxRegionSection,
        sonioxRegionPickerHost,
        modeSection: document.getElementById('modeSection'),
        accountSection: document.getElementById('accountSection'),
        apiKeySection: document.getElementById('apiKeySection'),
        modeDescription: document.getElementById('modeDescription'),
        redeemPasteButton: document.getElementById('redeemPasteButton'),
        versionElement: document.getElementById('settingsVersion'),
    },
});
settingsPanelController.init();
const settingsSaveController = SettingsSave.create({
    runtime: settingsRuntime,
    setup: settingsSetup,
    t,
    localizeBackendMessage,
    getDraft: () => ({
        provider: settingsPanelController.getSelectedProvider(),
        region: settingsPanelController.getSelectedSonioxRegion(),
        mode: settingsPanelController.getMode(),
        apiKey: apiKeyInput ? apiKeyInput.value : '',
    }),
    getState: () => ({ envKeyPresent }),
    loadProviderSettings,
    saveProviderSettings,
    loadServerSettings,
    saveServerSettings,
    ensureHostedVersionAllowed,
    actions: {
        setSaving: (saving) => {
            if (!settingsSaveButton) return;
            settingsSaveButton.disabled = saving;
            settingsSaveButton.textContent = t(saving ? 'saving' : 'save');
        },
        setError: (message) => {
            if (settingsErrorEl) settingsErrorEl.textContent = message;
        },
        refreshProviderFields: (provider) => {
            settingsPanelController.updateApiKeyField(provider);
            settingsPanelController.updateSonioxRegion(provider);
        },
        hideSettingsPanel,
        openLogin,
        finishHotSettingsSave,
        clearSubtitleState,
        populateSettingsForm: () => settingsPanelController.populate(),
    },
});

function updateSettingsButtonVisibility() {
    settingsPanelController.updateButtonVisibility();
}

function getSelectedProvider() {
    return settingsPanelController.getSelectedProvider();
}

function getSelectedSonioxRegion() {
    return settingsPanelController.getSelectedSonioxRegion();
}

function setModeRadio(mode) {
    settingsPanelController.setMode(mode);
}

function getSettingsMode() {
    return settingsPanelController.getMode();
}

function applyModeSectionsVisibility(mode) {
    settingsPanelController.applyModeVisibility(mode);
}

let hostedAccount = null;

function updateAccountSection() {
    if (hostedAccount) hostedAccount.updateSection();
}

// Show the signed-in user's current balance and free pools inside the account
// panel (requirement: account info also shows the current quota balance).
function updateAccountBalance() {
    if (hostedAccount) hostedAccount.updateBalance();
}

function openSettings({ forced = false } = {}) {
    return settingsPanelController.open({ forced });
}

function hideSettingsPanel() {
    settingsPanelController.hide();
}

function closeSettings() {
    return settingsPanelController.close();
}

function pushSetup(provider, apiKey, options = {}) {
    return settingsSetup.push(provider, apiKey, options);
}

function finishHotSettingsSave() {
    setupRequired = false;
    hideSettingsPanel();
    showToast(t('settings_saved'));
}

// 自定义确认对话框，替代浏览器自带的 confirm()。
function showConfirm(message, { okLabel, cancelLabel, danger = false } = {}) {
    return confirmController.show(message, { okLabel, cancelLabel, danger });
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

function handleSettingsSave(event) {
    return settingsSaveController.handleSubmit(event);
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
        setTranslationModeSynced: translationModeController.setTranslationModeSynced,
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
        translationUiMode: translationModeController.getTranslationUiMode(),
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
hostedAccount = HostedAccount.create({
    document,
    window,
    navigator,
    fetch,
    t,
    localizeBackendMessage,
    showConfirm,
    rankLabel: hostedLogin.rankLabel,
    getRuntimeState: () => ({
        backendLoggedIn,
        relayServerUrl,
        creditsPurchaseUrl,
    }),
    loadServerSettings,
    saveServerSettings,
    balance: hostedBalance,
    actions: {
        showToast,
        setBackendLoggedIn: (value) => { backendLoggedIn = !!value; },
        resetBootGuard: () => { pushedOverrideBootId = null; },
        hideSettingsPanel,
        openLogin,
    },
    elements: {
        serverHint: document.getElementById('accountServerHint'),
        identityHint: document.getElementById('accountIdentityHint'),
        purchaseHint: document.getElementById('purchaseCreditsHint'),
        purchaseLink: document.getElementById('purchaseCreditsLink'),
        firstBonusHint: document.getElementById('firstRedeemBonusHint'),
        balanceHint: document.getElementById('accountBalanceHint'),
        freePools: document.getElementById('accountFreePools'),
        redeemButton: document.getElementById('redeemButton'),
        redeemInput: document.getElementById('redeemInput'),
        redeemPasteButton: document.getElementById('redeemPasteButton'),
        reLoginButton: document.getElementById('reLoginButton'),
        logoutButton: document.getElementById('logoutButton'),
        copyInviteButton: document.getElementById('copyInviteButton'),
        openUserWebButton: document.getElementById('openUserWebButton'),
    },
});
hostedAccount.init();
if (balanceOpenSettingsButton) {
    balanceOpenSettingsButton.addEventListener('click', () => openSettings({ forced: false }));
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

function openLogin(options = {}) {
    hostedLogin.open(options);
}

function hideLogin() {
    hostedLogin.hide();
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

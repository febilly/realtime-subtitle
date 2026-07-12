const customTooltip = CustomTooltip.create({ document, window });
customTooltip.init();

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
const escapeHtml = RenderHtml.createEscapeHtml(document);

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
const resetAllController = ResetAllController.create({
    document,
    window,
    fetch,
    localStorage,
    sessionStorage,
    t,
    showConfirm: confirmController.show,
});
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

const controlPorts = {
    applySpeakerLabelVisibility: () => speakerLabelController.applyVisibility(),
    isLlmTranslateMode: () => translationModeController.isTranslateMode(),
    enforceTranslateSegmentMode: () => segmentModeController.enforceTranslateMode(),
    getSegmentModes: () => segmentModeController.getAvailableModes(),
    updatePauseButtonUi: () => { runtimeControls.updatePauseButtonUi(); },
    updateSegmentModeButton: () => segmentModeController.updateButton(),
    updateDisplayModeButton: () => { runtimeControls.updateDisplayModeButton(); },
    updateOscTranslationButton: () => oscTranslationController.updateButton(),
    updateBottomSafeAreaButton: () => mobileSafeAreaController.updateButton(),
    applyBottomSafeArea: () => mobileSafeAreaController.apply(),
    updateAutoRestartButton: () => { recognitionControls.updateAutoRestartButton(); },
    updateAudioSourceButton: () => { runtimeControls.updateAudioSourceButton(); },
    setTranslationUiMode: (mode, options = {}) => translationModeController.setTranslationUiMode(mode, options),
    setSpeakerLabelsHidden: (hidden) => speakerLabelController.setHidden(hidden),
    setSegmentMode: (mode) => segmentModeController.setMode(mode),
    updateFuriganaButton: () => furiganaToggleController.updateButton(),
    restartRecognition: (options = {}) => recognitionControls.restartRecognition(options),
    triggerAutoRestart: () => recognitionControls.triggerAutoRestart(),
    refreshOverlayState: () => runtimeControls.refreshOverlayState(),
    fetchOscTranslationStatus: () => oscTranslationController.fetchStatus(),
};

const settingsPorts = {
    applyBundledCjkFontPreference: (enabled, { persist = false, sync = false } = {}) => (
        settingsRuntime.applyBundledCjkFontPreference(enabled, { persist, sync })
    ),
    loadServerSettings: () => settingsStore.loadServerSettings(),
    saveServerSettings: (settings) => { settingsStore.saveServerSettings(settings); },
    getConnectionMode: () => SettingsPolicy.resolveConnectionMode({
        relayAvailable,
        serverSettings: settingsStore.loadServerSettings(),
    }),
    loadProviderSettings: () => settingsStore.loadProviderSettings(),
    saveProviderSettings: (settings) => { settingsStore.saveProviderSettings(settings); },
    buildCustomSelect: (options, config = {}) => settingsUi.buildCustomSelect(options, config),
    renderMicrophoneDevicePicker: () => settingsRuntime.renderMicrophoneDevicePicker(),
    fetchMicrophoneDevices: () => settingsRuntime.fetchMicrophoneDevices(),
    renderBundledCjkFontPicker: () => settingsRuntime.renderBundledCjkFontPicker(),
    updateTranslationModeHint: () => { settingsRuntime.updateTranslationModeHint(); },
    renderTranslationModePicker: () => settingsRuntime.renderTranslationModePicker(),
    renderSegmentModePicker: () => settingsRuntime.renderSegmentModePicker(),
    renderRuntimeSettingsPickers: () => settingsRuntime.renderRuntimeSettingsPickers(),
    updateSettingsButtonVisibility: () => { settingsPanelController.updateButtonVisibility(); },
    getSelectedProvider: () => settingsPanelController.getSelectedProvider(),
    setModeRadio: (mode) => { settingsPanelController.setMode(mode); },
    applyModeSectionsVisibility: (mode) => { settingsPanelController.applyModeVisibility(mode); },
    pushSetup: (provider, apiKey, options = {}) => settingsSetup.push(provider, apiKey, options),
    syncProviderFromStorage: async () => { await settingsSetup.syncFromStorage(); },
    fetchUiConfig: () => uiConfigController.fetch(),
    fetchLlmRefineStatus: () => translationModeController.fetchLlmRefineStatus(),
};

const settingsRuntime = SettingsRuntime.create({
    document,
    fetch,
    storage: localStorage,
    t,
    localizeBackendMessage,
    buildCustomSelect: settingsPorts.buildCustomSelect,
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
        get selectedProvider() { return settingsPorts.getSelectedProvider(); },
        get providerSettings() { return settingsPorts.loadProviderSettings(); },
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
        get segmentModes() { return controlPorts.getSegmentModes(); },
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
        updateAutoRestartButton: controlPorts.updateAutoRestartButton,
        setSpeakerLabelsHidden: controlPorts.setSpeakerLabelsHidden,
        setSegmentMode: controlPorts.setSegmentMode,
        setTranslationUiMode: controlPorts.setTranslationUiMode,
    },
});

settingsPorts.applyBundledCjkFontPreference(useBundledCjkFont, { sync: true });

// ---- Subtitle-server relay (hosted mode) state ----
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
let hostedAccount = null;

const hostedPorts = {
    updateAccountSection: () => { if (hostedAccount) hostedAccount.updateSection(); },
    updateAccountBalance: () => { if (hostedAccount) hostedAccount.updateBalance(); },
    ensureHostedVersionAllowed: (options = {}) => hostedUpdate.ensure(options),
    returnToModeChooser: () => hostedMode.returnToModeChooser(),
    switchToDirectMode: () => hostedMode.switchToDirectMode(),
    switchToOwnKeyMode: () => hostedMode.switchToOwnKeyMode(),
    maybeRunFirstLaunchFlow: () => hostedMode.maybeRunFirstLaunchFlow(),
    preopenHostedLoginIfNeeded: () => hostedMode.preopenHostedLoginIfNeeded(),
    refreshPreopenedHostedLogin: () => hostedMode.refreshPreopenedHostedLogin(),
    applyLoginI18n: () => hostedLogin.applyI18n(),
    updateLoginSubmitState: () => hostedLogin.updateSubmitState(),
    openLogin: (options = {}) => hostedLogin.open(options),
    hideLogin: () => hostedLogin.hide(),
    updateBalanceBarVisibility: () => hostedBalance.updateBalanceBarVisibility(),
    fetchBalance: (options = {}) => hostedBalance.fetchBalance(options),
    freePoolsSummary: (pools) => hostedBalance.freePoolsSummary(pools),
    sessionCostResume: () => hostedBalance.sessionCostResume(),
    sessionCostPause: () => hostedBalance.sessionCostPause(),
    sessionCostReset: () => hostedBalance.sessionCostReset(),
};
const { safeHttpUrl } = SettingsStore;

let uiTranslationMode = settingsStore.loadUiTranslationMode();

const speakerLabelController = SpeakerLabelController.create({
    fetch,
    container: subtitleContainer,
    console,
    getRuntimeState: () => ({ lockManualControls, translationProvider }),
    getStoredPreference: () => settingsRuntime.getStoredHideSpeakerLabelsSetting(),
    renderPicker: () => settingsRuntime.renderSpeakerLabelsPicker(),
    renderSubtitles: () => subtitleRuntimeController.render(),
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
    setControlIcon: ControlIcon.set,
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
        subtitleRuntimeController.render();
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
        renderSubtitles: () => subtitleRuntimeController.render(),
        updateTranslationModeHint: settingsPorts.updateTranslationModeHint,
        renderTranslationModePicker: settingsPorts.renderTranslationModePicker,
        restartRecognition: controlPorts.restartRecognition,
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
    restartRecognition: controlPorts.restartRecognition,
    renderSubtitles: () => subtitleRuntimeController.render(),
});
languageUi.init();

// 显示模式: 'both', 'original', 'translation'
let displayMode = settingsStore.loadDisplayMode();

// 自动重启识别开关（默认开启；已有保存值优先）
let autoRestartEnabled = settingsStore.loadAutoRestartEnabled();

const furiganaService = Furigana.createService({
    kuromoji: window.kuromoji,
    escapeHtml,
    onReady: () => subtitleRuntimeController.render(),
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
        translateMode: controlPorts.isLlmTranslateMode(),
        translationUiMode: translationModeController.getTranslationUiMode(),
        currentTranslationTargetLang,
        furiganaEnabled: furiganaToggleController.isEnabled(),
        speakerDiarizationEnabled: speakerLabelController.isDiarizationEnabled(),
        hideSpeakerLabels: speakerLabelController.isHidden(),
    }),
});
const subtitleRuntimeController = SubtitleRuntimeController.create({
    session: subtitleSession,
    renderer: subtitleRenderer,
    getState: () => ({
        translateMode: controlPorts.isLlmTranslateMode(),
        translationUiMode: translationModeController.getTranslationUiMode(),
    }),
});
const subtitleFrameController = SubtitleFrameController.create({
    session: subtitleSession,
    renderer: subtitleRenderer,
    console,
    getState: () => ({ translateMode: controlPorts.isLlmTranslateMode() }),
    renderSubtitles: subtitleRuntimeController.render,
    finalizeCurrentNonFinalTokens: subtitleRuntimeController.finalize,
    clearSubtitleState: subtitleRuntimeController.clear,
});

// 控制标志
let shouldReconnect = true;  // 是否应该自动重连
let isRestarting = false;    // 是否正在重启中
let isPaused = false;        // 是否暂停中
let audioSource = 'system';  // 音频输入来源

const webSocketController = WebSocketController.create({
    window,
    wsClient: WsClient,
    logger: console,
    getState: () => ({ autoRestartEnabled, shouldReconnect, isRestarting }),
    onFrame: handleMessageFrame,
    onAutoRestart: controlPorts.triggerAutoRestart,
});

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
    setControlIcon: ControlIcon.set,
    renderSubtitles: subtitleRuntimeController.render,
    sessionCostPause: hostedPorts.sessionCostPause,
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
    closeSocket: webSocketController.close,
    finalizeCurrentNonFinalTokens: subtitleRuntimeController.finalize,
    clearSubtitleState: subtitleRuntimeController.clear,
    sessionCostReset: hostedPorts.sessionCostReset,
    updatePauseButtonUi: () => runtimeControls.updatePauseButtonUi(),
    hasUsableWebSocket: webSocketController.isUsable,
    connect: webSocketController.connect,
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
        openSettings: (options) => settingsFlowController.open(options),
        loadServerSettings: settingsPorts.loadServerSettings,
        saveServerSettings: settingsPorts.saveServerSettings,
        updateBalanceBarVisibility: hostedPorts.updateBalanceBarVisibility,
        openLogin: hostedPorts.openLogin,
        triggerAutoRestart: controlPorts.triggerAutoRestart,
    },
});
const runtimeFrameController = RuntimeFrameController.create({
    t,
    getState: () => ({ lockManualControls }),
    actions: {
        applyBundledCjkFontPreference: settingsPorts.applyBundledCjkFontPreference,
        syncOverlayState: runtimeControls.syncOverlayState,
        syncIpcStatus: appShellController.syncIpcStatus,
        displayErrorMessage,
        openSettings: (options) => settingsFlowController.open(options),
        addLlmCost: (credits) => hostedBalance.addLlmCost(credits),
        setTranslationUiMode: controlPorts.setTranslationUiMode,
        renderTranslationModePicker: settingsPorts.renderTranslationModePicker,
        showToast,
        restartRecognition: controlPorts.restartRecognition,
        handleSegmentModeChanged: segmentModeController.handleBackendChanged,
        handleSpeakerLabelsChanged: speakerLabelController.handleBackendChanged,
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
        providerSettings: settingsPorts.loadProviderSettings(),
        connectionMode: settingsPorts.getConnectionMode(),
        serverSettings: settingsPorts.loadServerSettings(),
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
        sessionCostReset: hostedPorts.sessionCostReset,
        showToast,
        setUiTranslationMode,
        fetchUiConfig: settingsPorts.fetchUiConfig,
    },
});

// 初始化按钮文本
controlPorts.updateSegmentModeButton();
controlPorts.updateDisplayModeButton();
controlPorts.updatePauseButtonUi();
controlPorts.updateAudioSourceButton();
controlPorts.updateFuriganaButton();
controlPorts.updateOscTranslationButton();
controlPorts.updateAutoRestartButton();
controlPorts.updateBottomSafeAreaButton();
controlPorts.enforceTranslateSegmentMode();
controlPorts.applySpeakerLabelVisibility();
controlPorts.applyBottomSafeArea();
appShellController.applyManualControlPolicy();
appShellController.applyStaticText();

const settingsUi = SettingsUI.create({
    document,
    window,
    storage: localStorage,
    actions: {
        openSettings: (options) => settingsFlowController.open(options),
        closeSettings: () => settingsFlowController.close(),
        returnToModeChooser: hostedPorts.returnToModeChooser,
        handleResetAll: resetAllController.handle,
        handleSettingsSave: (event) => settingsFlowController.handleSubmit(event),
    },
});
const themeController = ThemeController.create({
    settingsUi,
    fetch,
    storage: localStorage,
    toggle: themeToggle,
    themeIcon,
    setControlIcon: ControlIcon.set,
});
themeController.init();

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
        sessionCostReset: hostedPorts.sessionCostReset,
        setLanguageListFromCodes: languageUi.setLanguageCodes,
        resetFirstRedeemBonus: (value) => hostedBalance.resetFirstRedeemBonus(value),
        updateBalanceBarVisibility: hostedPorts.updateBalanceBarVisibility,
        updateAccountSection: hostedPorts.updateAccountSection,
        updateSettingsButtonVisibility: settingsPorts.updateSettingsButtonVisibility,
        applyBundledCjkFontPreference: settingsPorts.applyBundledCjkFontPreference,
        renderBundledCjkFontPicker: settingsPorts.renderBundledCjkFontPicker,
        renderRuntimeSettingsPickers: settingsPorts.renderRuntimeSettingsPickers,
        applyLockPauseRestartControlsUI: appShellController.applyManualControlPolicy,
        enforceTranslateSegmentMode: controlPorts.enforceTranslateSegmentMode,
    },
});

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

void controlPorts.refreshOverlayState();


function handleMessageFrame(data) {
    if (subtitleFrameController.handle(data)) return;
    if (sessionFrameController.handle(data)) return;
    runtimeFrameController.handle(data);
}

// ===================== Settings panel (provider + API key) =====================

const settingsPanelController = SettingsPanel.create({
    policy: SettingsPolicy,
    billing: Hosted.Billing,
    document,
    fetch,
    t,
    buildCustomSelect: settingsPorts.buildCustomSelect,
    loadProviderSettings: settingsPorts.loadProviderSettings,
    freePoolsSummary: hostedPorts.freePoolsSummary,
    getState: () => ({
        lockManualControls,
        relayAvailable,
        connectionMode: settingsPorts.getConnectionMode(),
        translationProvider,
        backendSonioxRegion,
        backendSonioxCustomUrl,
        envKeyPresent,
        setupRequired,
        clientVersion,
        canRefreshBalance: backendLoggedIn || !!settingsPorts.loadServerSettings().token,
    }),
    actions: {
        renderMicrophoneDevicePicker: settingsPorts.renderMicrophoneDevicePicker,
        renderRuntimeSettingsPickers: settingsPorts.renderRuntimeSettingsPickers,
        renderBundledCjkFontPicker: settingsPorts.renderBundledCjkFontPicker,
        renderTranslationModePicker: settingsPorts.renderTranslationModePicker,
        fetchMicrophoneDevices: settingsPorts.fetchMicrophoneDevices,
        fetchBalance: hostedPorts.fetchBalance,
        updateAccountSection: hostedPorts.updateAccountSection,
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
const settingsFlowController = SettingsFlowController.create({
    panel: settingsPanelController,
    policy: SettingsPolicy,
    t,
    getState: () => ({
        lockManualControls,
        connectionMode: settingsPorts.getConnectionMode(),
        serverSettings: settingsPorts.loadServerSettings(),
        setupRequired,
    }),
    updateState: (patch) => {
        if (Object.prototype.hasOwnProperty.call(patch, 'setupRequired')) {
            setupRequired = patch.setupRequired;
        }
    },
    submit: (event) => settingsSaveController.handleSubmit(event),
    actions: { openLogin: hostedPorts.openLogin, showToast },
});
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
    loadProviderSettings: settingsPorts.loadProviderSettings,
    saveProviderSettings: settingsPorts.saveProviderSettings,
    loadServerSettings: settingsPorts.loadServerSettings,
    saveServerSettings: settingsPorts.saveServerSettings,
    ensureHostedVersionAllowed: hostedPorts.ensureHostedVersionAllowed,
    actions: {
        setSaving: settingsPanelController.setSaving,
        setError: settingsPanelController.setError,
        refreshProviderFields: settingsPanelController.refreshProviderFields,
        hideSettingsPanel: settingsFlowController.hide,
        openLogin: hostedPorts.openLogin,
        finishHotSettingsSave: settingsFlowController.finishHotSave,
        clearSubtitleState: subtitleRuntimeController.clear,
        populateSettingsForm: () => settingsPanelController.populate(),
    },
});

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
    showConfirm: confirmController.show,
    getState: () => ({
        relayAvailable,
        connectionMode: settingsPorts.getConnectionMode(),
        currentVersion: clientVersion,
        latestVersion: clientLatestVersion,
        minimumVersion: clientMinimumVersion,
        updateUrl: clientUpdateUrl,
        notes: clientUpdateNotes,
    }),
    onSwitchDirect: hostedPorts.switchToDirectMode,
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
    loadServerSettings: settingsPorts.loadServerSettings,
    saveServerSettings: settingsPorts.saveServerSettings,
    getState: () => ({
        lockManualControls,
        relayAvailable,
        relayServerUrl,
        connectionMode: settingsPorts.getConnectionMode(),
    }),
    actions: {
        openLogin: hostedPorts.openLogin,
        hideLogin: hostedPorts.hideLogin,
        applyLoginI18n: hostedPorts.applyLoginI18n,
        updateLoginSubmitState: hostedPorts.updateLoginSubmitState,
        resetBootGuard: () => { pushedOverrideBootId = null; },
        hideSettingsPanel: settingsFlowController.hide,
        ensureHostedVersionAllowed: hostedPorts.ensureHostedVersionAllowed,
        syncProviderFromStorage: settingsPorts.syncProviderFromStorage,
        maybeForceOpenSettings: settingsFlowController.maybeForceOpen,
        updateBalanceBarVisibility: hostedPorts.updateBalanceBarVisibility,
        setModeRadio: settingsPorts.setModeRadio,
        applyModeSectionsVisibility: settingsPorts.applyModeSectionsVisibility,
        updateAccountSection: hostedPorts.updateAccountSection,
        openSettings: settingsFlowController.open,
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
    loadServerSettings: settingsPorts.loadServerSettings,
    saveServerSettings: settingsPorts.saveServerSettings,
    loadProviderSettings: settingsPorts.loadProviderSettings,
    actions: {
        showToast,
        updateBalanceBarVisibility: hostedPorts.updateBalanceBarVisibility,
        fetchBalance: hostedPorts.fetchBalance,
        clearSubtitleState: subtitleRuntimeController.clear,
        setTranslationModeSynced: translationModeController.setTranslationModeSynced,
        pushSetup: settingsPorts.pushSetup,
        switchToOwnKeyMode: hostedPorts.switchToOwnKeyMode,
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
        connectionMode: settingsPorts.getConnectionMode(),
        backendLoggedIn,
        hasToken: !!settingsPorts.loadServerSettings().token,
        translationProvider,
        uiTranslationMode,
        translationUiMode: translationModeController.getTranslationUiMode(),
        sonioxNoTranslationFactor,
    }),
    onAccountSectionChanged: hostedPorts.updateAccountSection,
    onAccountBalanceChanged: hostedPorts.updateAccountBalance,
    onOpenSettings: settingsFlowController.open,
    elements: {
        balanceBar,
        balanceActionItem,
        balanceOpenSettingsButton,
    },
});
hostedBalance.init();
hostedAccount = HostedAccount.create({
    document,
    window,
    navigator,
    fetch,
    t,
    localizeBackendMessage,
    showConfirm: confirmController.show,
    rankLabel: hostedLogin.rankLabel,
    getRuntimeState: () => ({
        backendLoggedIn,
        relayServerUrl,
        creditsPurchaseUrl,
    }),
    loadServerSettings: settingsPorts.loadServerSettings,
    saveServerSettings: settingsPorts.saveServerSettings,
    balance: hostedBalance,
    actions: {
        showToast,
        setBackendLoggedIn: (value) => { backendLoggedIn = !!value; },
        resetBootGuard: () => { pushedOverrideBootId = null; },
        hideSettingsPanel: settingsFlowController.hide,
        openLogin: hostedPorts.openLogin,
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

const hostedController = Hosted.createController({
    preopenHostedLoginIfNeeded: hostedPorts.preopenHostedLoginIfNeeded,
    fetchUiConfig: settingsPorts.fetchUiConfig,
    refreshPreopenedHostedLogin: hostedPorts.refreshPreopenedHostedLogin,
    maybeRunFirstLaunchFlow: hostedPorts.maybeRunFirstLaunchFlow,
    ensureHostedVersionAllowed: hostedPorts.ensureHostedVersionAllowed,
    syncProviderFromStorage: settingsPorts.syncProviderFromStorage,
    fetchLlmRefineStatus: settingsPorts.fetchLlmRefineStatus,
    fetchApiKeyStatus,
    fetchOscTranslationStatus: controlPorts.fetchOscTranslationStatus,
    maybeForceOpenSettings: settingsFlowController.maybeForceOpen,
    updateBalanceBarVisibility: hostedPorts.updateBalanceBarVisibility,
    connect: webSocketController.connect,
    sessionCostResume: hostedPorts.sessionCostResume,
    sessionCostPause: hostedPorts.sessionCostPause,
    isPaused: () => isPaused,
});

document.addEventListener('DOMContentLoaded', () => {
    void hostedController.startup();
});

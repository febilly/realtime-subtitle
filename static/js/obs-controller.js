(function (root) {
    'use strict';

    const obsSettings = root.ObsSettings
        || (typeof require === 'function' ? require('./obs-settings') : null);
    if (!obsSettings) {
        throw new Error('ObsController requires ObsSettings');
    }

    const {
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
        DEFAULT_SETTINGS,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeSettings,
        toBackendPayload,
        areSettingsEqual,
    } = obsSettings;

    const STORAGE_KEY = 'obsSettings.v1';
    const GROUPS_STORAGE_KEY = 'obsDialogGroups.v1';
    const LAST_PORT_STORAGE_KEY = 'obsLastPort';
    const DEFAULT_GROUP_STATES = Object.freeze({
        text: true,
        color: false,
        background: false,
        layout: false,
    });
    const DEFAULT_SOURCE_WIDTH = 1200;
    const DEFAULT_SOURCE_HEIGHT = 224;
    const PREVIEW_VIRTUAL_WIDTH = DEFAULT_SOURCE_WIDTH;
    const PREVIEW_VIRTUAL_HEIGHT = DEFAULT_SOURCE_HEIGHT;
    const COPIED_RESET_DELAY_MS = 1500;
    const MOBILE_UA_PATTERN = /Mobi|Android|iPhone|iPad|iPod/i;

    function isMobileUserAgent(userAgent) {
        if (root.MobileSafeAreaController && typeof root.MobileSafeAreaController.isMobileUserAgent === 'function') {
            return root.MobileSafeAreaController.isMobileUserAgent(userAgent);
        }
        return MOBILE_UA_PATTERN.test(String(userAgent || ''));
    }

    function extractPort(urlStr) {
        if (!urlStr || typeof urlStr !== 'string') return '';
        try {
            const parsed = new URL(urlStr);
            if (parsed.port) return parsed.port;
            if (parsed.protocol === 'https:') return '443';
            if (parsed.protocol === 'http:') return '80';
        } catch (_) {
            const match = /:(\d+)(?:[/?#]|$)/.exec(urlStr);
            if (match) return match[1];
        }
        return '';
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const storage = options.storage || root.localStorage;
        const fetchImpl = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const navigatorRef = options.navigator || root.navigator;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getLang = typeof options.getLang === 'function'
            ? options.getLang
            : () => (root.I18N && root.I18N.lang) || (navigatorRef && navigatorRef.language) || 'en';
        const showToast = typeof options.showToast === 'function' ? options.showToast : () => {};
        const buildCustomSelect = typeof options.buildCustomSelect === 'function'
            ? options.buildCustomSelect
            : null;
        const buildToggleSwitch = typeof options.buildToggleSwitch === 'function'
            ? options.buildToggleSwitch
            : null;
        const controlIcon = options.ControlIcon || root.ControlIcon;
        const isPackEnabledFn = typeof options.isPackEnabled === 'function'
            ? options.isPackEnabled
            : () => true;
        const setTimeoutFn = typeof options.setTimeout === 'function'
            ? options.setTimeout
            : root.setTimeout.bind(root);
        const clearTimeoutFn = typeof options.clearTimeout === 'function'
            ? options.clearTimeout
            : root.clearTimeout.bind(root);
        const logger = options.console || root.console || { warn() {}, error() {} };
        const elements = options.elements || {};
        const mobile = options.isMobile !== undefined
            ? !!options.isMobile
            : isMobileUserAgent(
                options.userAgent !== undefined
                    ? options.userAgent
                    : navigatorRef && navigatorRef.userAgent,
            );

        let hasStoredSettings = false;
        let settings = loadStoredSettings();
        let groupStates = loadGroupStates();
        let packEnabled = !!isPackEnabledFn();
        let desktopAvailable = !!options.desktopAvailable;
        let backendObsUrl = '';
        let dialogOpen = false;
        let previewLoadedUrl = '';
        let copied = false;
        let copiedResetTimer = null;
        let inputDebounceTimer = null;
        let previewObserver = null;
        let initialized = false;
        const selectControls = {};
        const toggleControls = {};
        const boundListeners = [];

        function el(key, id) {
            return elements[key] || (documentRef && documentRef.getElementById(id)) || null;
        }

        function loadStoredSettings() {
            if (!storage || typeof storage.getItem !== 'function') {
                return { ...DEFAULT_SETTINGS };
            }
            try {
                const raw = storage.getItem(STORAGE_KEY);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        hasStoredSettings = true;
                        return normalizeSettings(parsed);
                    }
                }
            } catch (_) {
                // ignore malformed storage
            }
            return { ...DEFAULT_SETTINGS };
        }

        function saveSettings(next) {
            settings = normalizeSettings(next);
            hasStoredSettings = true;
            if (storage && typeof storage.setItem === 'function') {
                try {
                    storage.setItem(STORAGE_KEY, JSON.stringify(settings));
                } catch (error) {
                    logger.warn('Failed to persist OBS settings:', error);
                }
            }
            return { ...settings };
        }

        function loadGroupStates() {
            const defaults = { ...DEFAULT_GROUP_STATES };
            if (!storage || typeof storage.getItem !== 'function') return defaults;
            try {
                const raw = storage.getItem(GROUPS_STORAGE_KEY);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        for (const key of Object.keys(defaults)) {
                            if (typeof parsed[key] === 'boolean') {
                                defaults[key] = parsed[key];
                            }
                        }
                    }
                }
            } catch (_) {
                // ignore malformed group state
            }
            return defaults;
        }

        function saveGroupStates() {
            if (!storage || typeof storage.setItem !== 'function') return;
            try {
                storage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(groupStates));
            } catch (_) {
                // ignore storage write failures
            }
        }

        function isDesktopMainWindow() {
            return desktopAvailable && !mobile;
        }

        function getObsUrl() {
            if (backendObsUrl) return backendObsUrl;
            const loc = windowRef && windowRef.location ? windowRef.location : {};
            const port = loc.port ? `:${loc.port}` : '';
            return `http://127.0.0.1${port}/obs`;
        }

        function getObsDragUrl() {
            const baseUrl = getObsUrl();
            const layerName = encodeURIComponent(t('obs_layer_name'));
            const sep = baseUrl.includes('?') ? '&' : '?';
            return `${baseUrl}${sep}layer-name=${layerName}&layer-width=${DEFAULT_SOURCE_WIDTH}&layer-height=${DEFAULT_SOURCE_HEIGHT}`;
        }

        function updatePortWarning() {
            const warnEl = el('portWarning', 'obsPortWarning');
            if (!warnEl) return;
            let storedPort = '';
            if (storage && typeof storage.getItem === 'function') {
                try {
                    storedPort = String(storage.getItem(LAST_PORT_STORAGE_KEY) || '').trim();
                } catch (_) {
                    storedPort = '';
                }
            }
            const currentPort = extractPort(getObsUrl());
            const mismatch = !!(storedPort && currentPort && storedPort !== currentPort);
            warnEl.hidden = !mismatch;
            warnEl.textContent = mismatch ? t('obs_port_changed_warning') : '';
        }

        function recordHandedOutPort() {
            const currentPort = extractPort(getObsUrl());
            if (!currentPort || !storage || typeof storage.setItem !== 'function') return;
            try {
                storage.setItem(LAST_PORT_STORAGE_KEY, currentPort);
            } catch (_) {
                // ignore storage errors
            }
            updatePortWarning();
        }

        function setIconHref(iconEl, iconName) {
            if (!iconEl) return false;
            if (controlIcon && typeof controlIcon.set === 'function') {
                return controlIcon.set(iconEl, iconName);
            }
            const useEl = iconEl.querySelector && iconEl.querySelector('use');
            if (!useEl) return false;
            useEl.setAttribute('href', `icons/lucide-sprite.svg#${iconName}`);
            return true;
        }

        function updateToolbarButton() {
            const btn = el('copyLinkButton', 'obsCopyLinkButton');
            if (!btn) return;
            btn.hidden = !(packEnabled && isDesktopMainWindow());
            const title = t('obs_dialog_title');
            btn.title = title;
            btn.setAttribute('aria-label', title);
        }

        function updateCopyUrlButton() {
            const btn = el('copyUrlButton', 'obsCopyUrlButton');
            const textEl = el('copyUrlText', 'obsCopyUrlText');
            const iconEl = el('copyUrlIcon', 'obsCopyUrlIcon');
            if (iconEl) {
                setIconHref(iconEl, copied ? 'check' : 'link');
            }
            if (textEl) {
                textEl.textContent = t(copied ? 'obs_copied_short' : 'obs_copy_link_btn');
            }
            if (btn) {
                if (btn.classList) btn.classList.toggle('copied', copied);
                btn.title = t(copied ? 'obs_link_copied' : 'obs_copy_link');
                btn.setAttribute('aria-label', btn.title);
            }
        }

        function updateDragCard() {
            const card = el('dragCard', 'obsDragCard');
            const textEl = el('dragCardText', 'obsDragCardText');
            if (card) {
                card.setAttribute('href', getObsDragUrl());
            }
            if (textEl) {
                textEl.textContent = t('obs_drag_to_obs');
            }
        }

        const SLIDER_SPECS = [
            {
                key: 'fontSize',
                sliderId: 'obsFontSizeSlider',
                valueId: 'obsFontSizeValue',
                markId: 'obsFontSizeDefaultMark',
                min: FONT_SIZE_MIN,
                max: FONT_SIZE_MAX,
                def: FONT_SIZE_DEFAULT,
                format: (v) => t('obs_font_size_value', { value: v }),
            },
            {
                key: 'translationFontScale',
                sliderId: 'obsTranslationScaleSlider',
                valueId: 'obsTranslationScaleValue',
                markId: 'obsTranslationScaleDefaultMark',
                min: TRANSLATION_FONT_SCALE_MIN,
                max: TRANSLATION_FONT_SCALE_MAX,
                def: TRANSLATION_FONT_SCALE_DEFAULT,
                format: (v) => `${v}%`,
            },
            {
                key: 'outlineWidth',
                sliderId: 'obsOutlineWidthSlider',
                valueId: 'obsOutlineWidthValue',
                markId: 'obsOutlineWidthDefaultMark',
                min: OUTLINE_WIDTH_MIN,
                max: OUTLINE_WIDTH_MAX,
                def: OUTLINE_WIDTH_DEFAULT,
                format: (v) => t('obs_font_size_value', { value: v }),
            },
            {
                key: 'bgOpacity',
                sliderId: 'obsBgOpacitySlider',
                valueId: 'obsBgOpacityValue',
                markId: 'obsBgOpacityDefaultMark',
                min: BG_OPACITY_MIN,
                max: BG_OPACITY_MAX,
                def: BG_OPACITY_DEFAULT,
                format: (v) => `${v}%`,
            },
            {
                key: 'maxSentences',
                sliderId: 'obsMaxSentencesSlider',
                valueId: 'obsMaxSentencesValue',
                markId: 'obsMaxSentencesDefaultMark',
                min: MAX_SENTENCES_MIN,
                max: MAX_SENTENCES_MAX,
                def: MAX_SENTENCES_DEFAULT,
                format: (v) => `${v}`,
            },
            {
                key: 'autoHideSeconds',
                sliderId: 'obsAutoHideSlider',
                valueId: 'obsAutoHideValue',
                markId: 'obsAutoHideDefaultMark',
                min: AUTO_HIDE_SECONDS_MIN,
                max: AUTO_HIDE_SECONDS_MAX,
                def: AUTO_HIDE_SECONDS_DEFAULT,
                format: (v) => (v === 0 ? t('obs_auto_hide_off') : t('local_seconds_value', { value: v })),
            },
        ];

        function renderSlider(spec) {
            const slider = el(spec.key + 'Slider', spec.sliderId);
            const output = el(spec.key + 'Value', spec.valueId);
            const mark = el(spec.key + 'Mark', spec.markId);
            const val = Number(settings[spec.key]);
            if (output) {
                output.textContent = spec.format(val);
            }
            if (!slider) return;
            slider.min = String(spec.min);
            slider.max = String(spec.max);
            slider.value = String(val);
            const span = spec.max - spec.min;
            const fill = span > 0 ? ((val - spec.min) / span) * 100 : 0;
            if (slider.parentElement && slider.parentElement.style) {
                slider.parentElement.style.setProperty('--fill', `${fill}%`);
            }
            if (mark && mark.classList) {
                mark.classList.toggle('is-current', val === spec.def);
            }
        }

        function renderAllSliders() {
            for (const spec of SLIDER_SPECS) {
                renderSlider(spec);
            }
        }

        const COLOR_SPECS = [
            { key: 'originalColor', inputId: 'obsOriginalColorInput', valueId: 'obsOriginalColorValue' },
            { key: 'translationColor', inputId: 'obsTranslationColorInput', valueId: 'obsTranslationColorValue' },
            { key: 'outlineColor', inputId: 'obsOutlineColorInput', valueId: 'obsOutlineColorValue' },
            { key: 'bgColor', inputId: 'obsBgColorInput', valueId: 'obsBgColorValue' },
        ];

        function renderAllColors() {
            for (const spec of COLOR_SPECS) {
                const input = el(spec.key + 'Input', spec.inputId);
                const output = el(spec.key + 'Value', spec.valueId);
                const val = settings[spec.key];
                if (input) input.value = val;
                if (output) output.textContent = val;
            }
        }

        function getSelectSpecs() {
            return [
                {
                    key: 'displayContent',
                    hostId: 'obsDisplayContentPicker',
                    options: [
                        { value: 'both', label: t('obs_display_both') },
                        { value: 'original', label: t('obs_display_original') },
                        { value: 'translation', label: t('obs_display_translation') },
                    ],
                },
                {
                    key: 'fontFamily',
                    hostId: 'obsFontFamilyPicker',
                    options: [
                        { value: 'system', label: t('obs_font_system') },
                        { value: 'microsoft_yahei', label: t('obs_font_microsoft_yahei') },
                        { value: 'simhei', label: t('obs_font_simhei') },
                        { value: 'simsun', label: t('obs_font_simsun') },
                        { value: 'kaiti', label: t('obs_font_kaiti') },
                        { value: 'noto_sans_cjk', label: t('obs_font_noto_sans_cjk') },
                    ],
                },
                {
                    key: 'bgMode',
                    hostId: 'obsBgModePicker',
                    options: [
                        { value: 'none', label: t('obs_bg_mode_none') },
                        { value: 'strip', label: t('obs_bg_mode_strip') },
                        { value: 'block', label: t('obs_bg_mode_block') },
                    ],
                },
                {
                    key: 'flowDirection',
                    hostId: 'obsFlowDirectionPicker',
                    options: [
                        { value: 'down', label: t('subtitle_flow_option_down') },
                        { value: 'up', label: t('subtitle_flow_option_up') },
                    ],
                },
                {
                    key: 'alignment',
                    hostId: 'obsAlignmentPicker',
                    options: [
                        { value: 'left', label: t('obs_alignment_left') },
                        { value: 'center', label: t('obs_alignment_center') },
                        { value: 'right', label: t('obs_alignment_right') },
                    ],
                },
            ];
        }

        function renderSelectControls() {
            if (!documentRef) return;
            for (const spec of getSelectSpecs()) {
                const host = el(spec.key + 'Picker', spec.hostId);
                if (!host) continue;
                if (selectControls[spec.key] && typeof selectControls[spec.key].close === 'function') {
                    selectControls[spec.key].close();
                }
                host.innerHTML = '';
                const onSelect = (nextValue) => {
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: nextValue,
                    });
                    postPreviewUpdate();
                    void syncSettingsToBackend(updated);
                };
                if (buildCustomSelect) {
                    const picker = buildCustomSelect(spec.options, {
                        value: settings[spec.key],
                        onChange: onSelect,
                    });
                    host.appendChild(picker);
                    selectControls[spec.key] = picker;
                } else {
                    const select = documentRef.createElement('select');
                    for (const entry of spec.options) {
                        const opt = documentRef.createElement('option');
                        opt.value = entry.value;
                        opt.textContent = entry.label;
                        select.appendChild(opt);
                    }
                    select.value = settings[spec.key];
                    select.addEventListener('change', () => onSelect(select.value));
                    host.appendChild(select);
                    selectControls[spec.key] = select;
                }
            }
        }

        const TOGGLE_SPECS = [
            { key: 'bold', hostId: 'obsBoldPicker' },
            { key: 'showLangTag', hostId: 'obsShowLangTagPicker' },
            { key: 'dropShadow', hostId: 'obsDropShadowPicker' },
        ];

        function renderToggleControls() {
            if (!documentRef) return;
            for (const spec of TOGGLE_SPECS) {
                const host = el(spec.key + 'Picker', spec.hostId);
                if (!host) continue;
                host.innerHTML = '';
                const currentVal = settings[spec.key] ? 'true' : 'false';
                const onToggle = (nextStr) => {
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: nextStr === 'true',
                    });
                    postPreviewUpdate();
                    void syncSettingsToBackend(updated);
                };
                if (buildToggleSwitch) {
                    const toggle = buildToggleSwitch({
                        value: currentVal,
                        onValue: 'true',
                        offValue: 'false',
                        onChange: onToggle,
                    });
                    host.appendChild(toggle);
                    toggleControls[spec.key] = toggle;
                } else {
                    const toggle = documentRef.createElement('button');
                    toggle.type = 'button';
                    toggle.className = 'settings-switch';
                    toggle.setAttribute('role', 'switch');
                    toggle.setAttribute('aria-checked', currentVal);
                    let val = currentVal;
                    Object.defineProperty(toggle, 'value', {
                        get: () => val,
                        set: (next) => {
                            val = next === 'true' ? 'true' : 'false';
                            toggle.setAttribute('aria-checked', val);
                        },
                    });
                    toggle.addEventListener('click', () => {
                        val = val === 'true' ? 'false' : 'true';
                        toggle.setAttribute('aria-checked', val);
                        onToggle(val);
                    });
                    host.appendChild(toggle);
                    toggleControls[spec.key] = toggle;
                }
            }
        }

        function syncControlValuesFromSettings() {
            renderAllSliders();
            renderAllColors();
            for (const key of Object.keys(selectControls)) {
                const ctrl = selectControls[key];
                if (ctrl) ctrl.value = settings[key];
            }
            for (const key of Object.keys(toggleControls)) {
                const ctrl = toggleControls[key];
                if (ctrl) ctrl.value = settings[key] ? 'true' : 'false';
            }
        }

        function applyGroupOpenStates() {
            if (!documentRef || typeof documentRef.querySelectorAll !== 'function') return;
            const groups = documentRef.querySelectorAll('details[data-obs-group]');
            groups.forEach((details) => {
                const key = details.getAttribute('data-obs-group');
                if (key && Object.prototype.hasOwnProperty.call(groupStates, key)) {
                    details.open = !!groupStates[key];
                }
            });
        }

        function applyStaticText() {
            const textMap = [
                ['obsDialogTitle', 'obs_dialog_title'],
                ['obsAddSectionLabel', 'obs_add_section'],
                ['obsHowToSummary', 'obs_howto_title'],
                ['obsHowToDrag', 'obs_howto_drag'],
                ['obsHowToCopy', 'obs_howto_copy'],
                ['obsHowToScaleTip', 'obs_howto_scale_tip'],
                ['obsHowToKeepRunning', 'obs_howto_keep_running'],
                ['obsPreviewLabel', 'obs_preview_label'],
                ['obsGroupTextTitle', 'obs_group_text'],
                ['obsDisplayContentLabel', 'obs_display_content'],
                ['obsFontSizeLabel', 'obs_font_size'],
                ['obsTranslationScaleLabel', 'obs_translation_scale'],
                ['obsFontFamilyLabel', 'obs_font_family'],
                ['obsBoldLabel', 'obs_bold'],
                ['obsShowLangTagLabel', 'obs_show_lang_tag'],
                ['obsGroupColorTitle', 'obs_group_color'],
                ['obsOriginalColorLabel', 'obs_original_color'],
                ['obsTranslationColorLabel', 'obs_translation_color'],
                ['obsOutlineWidthLabel', 'obs_outline_width'],
                ['obsOutlineColorLabel', 'obs_outline_color'],
                ['obsDropShadowLabel', 'obs_drop_shadow'],
                ['obsGroupBackgroundTitle', 'obs_group_background'],
                ['obsBgModeLabel', 'obs_bg_mode'],
                ['obsBgColorLabel', 'obs_bg_color'],
                ['obsBgOpacityLabel', 'obs_bg_opacity'],
                ['obsGroupLayoutTitle', 'obs_group_layout'],
                ['obsFlowDirectionLabel', 'subtitle_flow_setting'],
                ['obsAlignmentLabel', 'obs_alignment'],
                ['obsMaxSentencesLabel', 'obs_max_sentences'],
                ['obsAutoHideLabel', 'obs_auto_hide'],
                ['obsResetDefaultsButton', 'obs_reset_defaults'],
                ['obsDialogDoneButton', 'obs_done'],
            ];
            for (const [id, i18nKey] of textMap) {
                const node = documentRef && documentRef.getElementById(id);
                if (node) node.textContent = t(i18nKey);
            }
            const closeBtn = el('dialogCloseButton', 'obsDialogCloseButton');
            if (closeBtn) {
                const closeText = t('close');
                closeBtn.title = closeText;
                closeBtn.setAttribute('aria-label', closeText);
            }
            updateDragCard();
            updateCopyUrlButton();
            updatePortWarning();
        }

        function renderDialog() {
            applyStaticText();
            renderAllSliders();
            renderAllColors();
            renderSelectControls();
            renderToggleControls();
            applyGroupOpenStates();
            updateToolbarButton();
        }

        function updatePreviewScale() {
            const box = el('previewBox', 'obsPreviewBox');
            const frame = el('previewFrame', 'obsPreviewFrame');
            if (!box || !frame) return;
            const width = box.clientWidth || (box.getBoundingClientRect && box.getBoundingClientRect().width) || 434;
            const scale = width > 0 ? width / PREVIEW_VIRTUAL_WIDTH : (434 / PREVIEW_VIRTUAL_WIDTH);
            if (frame.style) {
                frame.style.width = `${PREVIEW_VIRTUAL_WIDTH}px`;
                frame.style.height = `${PREVIEW_VIRTUAL_HEIGHT}px`;
                frame.style.transform = `scale(${scale})`;
            }
        }

        function postPreviewUpdate() {
            const frame = el('previewFrame', 'obsPreviewFrame');
            if (!frame || !frame.contentWindow || typeof frame.contentWindow.postMessage !== 'function') {
                return false;
            }
            try {
                frame.contentWindow.postMessage({
                    type: 'obs_preview_update',
                    settings: { ...settings },
                    lang: String(getLang() || 'en'),
                }, '*');
                return true;
            } catch (_) {
                return false;
            }
        }

        function ensurePreviewFrameLoaded() {
            const frame = el('previewFrame', 'obsPreviewFrame');
            if (!frame) return;
            const lang = encodeURIComponent(String(getLang() || 'en'));
            const targetSrc = `/obs?preview=1&lang=${lang}`;
            if (previewLoadedUrl !== targetSrc) {
                previewLoadedUrl = targetSrc;
                frame.setAttribute('src', targetSrc);
            }
            updatePreviewScale();
            postPreviewUpdate();
        }

        function openDialog() {
            const overlay = el('dialogOverlay', 'obsDialogOverlay');
            const dialog = el('dialog', 'obsDialog');
            if (!overlay || !dialog) return false;
            dialogOpen = true;
            overlay.hidden = false;
            dialog.hidden = false;
            updateDragCard();
            updateCopyUrlButton();
            updatePortWarning();
            applyGroupOpenStates();
            ensurePreviewFrameLoaded();
            return true;
        }

        function closeDialog() {
            const overlay = el('dialogOverlay', 'obsDialogOverlay');
            const dialog = el('dialog', 'obsDialog');
            for (const ctrl of Object.values(selectControls)) {
                if (ctrl && typeof ctrl.close === 'function') ctrl.close();
            }
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
            dialogOpen = false;
            return true;
        }

        async function syncSettingsToBackend(target = settings) {
            if (!fetchImpl) return false;
            const payload = toBackendPayload(target);
            try {
                const response = await fetchImpl('/obs-settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                return !!(response && response.ok);
            } catch (error) {
                logger.warn('Failed to sync OBS settings to backend:', error);
                return false;
            }
        }

        async function syncToBackend() {
            if (!hasStoredSettings) return false;
            return syncSettingsToBackend(settings);
        }

        async function copyTextWithFallback(text) {
            if (
                navigatorRef
                && navigatorRef.clipboard
                && typeof navigatorRef.clipboard.writeText === 'function'
            ) {
                try {
                    await navigatorRef.clipboard.writeText(text);
                    return true;
                } catch (_) {
                    // Fall through to backend clipboard endpoint.
                }
            }
            if (!fetchImpl) return false;
            try {
                const response = await fetchImpl('/clipboard', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                return !!(response && response.ok);
            } catch (error) {
                logger.warn('Failed to copy OBS URL via backend:', error);
                return false;
            }
        }

        function markCopiedVisual() {
            copied = true;
            updateCopyUrlButton();
            if (copiedResetTimer !== null) {
                clearTimeoutFn(copiedResetTimer);
            }
            copiedResetTimer = setTimeoutFn(() => {
                copiedResetTimer = null;
                copied = false;
                updateCopyUrlButton();
            }, COPIED_RESET_DELAY_MS);
        }

        async function copyObsLink() {
            const url = getObsUrl();
            const ok = await copyTextWithFallback(url);
            if (ok) {
                recordHandedOutPort();
                markCopiedVisual();
            } else {
                showToast(t('obs_copy_failed'));
            }
            return ok;
        }

        function resetToDefaults() {
            if (inputDebounceTimer !== null) {
                clearTimeoutFn(inputDebounceTimer);
                inputDebounceTimer = null;
            }
            const updated = saveSettings({ ...DEFAULT_SETTINGS });
            syncControlValuesFromSettings();
            postPreviewUpdate();
            void syncSettingsToBackend(updated);
            return updated;
        }

        function applyConfig(data = {}) {
            if (!data || typeof data !== 'object') return false;
            if (typeof data.obs_desktop_available === 'boolean') {
                desktopAvailable = data.obs_desktop_available;
            }
            if (typeof data.obs_url === 'string' && data.obs_url.trim()) {
                backendObsUrl = data.obs_url.trim();
            }
            let dirty = false;
            if (data.obs_settings && typeof data.obs_settings === 'object' && !hasStoredSettings) {
                const incoming = normalizeSettings(data.obs_settings);
                if (!areSettingsEqual(incoming, settings)) {
                    settings = incoming;
                    dirty = true;
                }
            }
            if (dirty || !selectControls.displayContent) {
                renderDialog();
            } else {
                syncControlValuesFromSettings();
                updateToolbarButton();
                updateDragCard();
                updatePortWarning();
            }
            if (dialogOpen) {
                postPreviewUpdate();
            }
            return true;
        }

        function setPackEnabled(enabled) {
            packEnabled = !!enabled;
            updateToolbarButton();
            if (!packEnabled && dialogOpen) {
                closeDialog();
            }
            return packEnabled;
        }

        function applyI18n() {
            renderDialog();
            if (dialogOpen) {
                postPreviewUpdate();
            }
        }

        function bindEvent(target, type, handler) {
            if (!target || typeof target.addEventListener !== 'function') return;
            target.addEventListener(type, handler);
            boundListeners.push(() => target.removeEventListener(type, handler));
        }

        function scheduleSettingsSync(nextSettings) {
            if (inputDebounceTimer !== null) {
                clearTimeoutFn(inputDebounceTimer);
            }
            inputDebounceTimer = setTimeoutFn(() => {
                inputDebounceTimer = null;
                void syncSettingsToBackend(nextSettings);
            }, 60);
        }

        function bindSliderEvents() {
            for (const spec of SLIDER_SPECS) {
                const slider = el(spec.key + 'Slider', spec.sliderId);
                if (!slider) continue;
                bindEvent(slider, 'input', () => {
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: slider.value,
                    });
                    renderSlider(spec);
                    postPreviewUpdate();
                    scheduleSettingsSync(updated);
                });
                bindEvent(slider, 'change', () => {
                    if (inputDebounceTimer !== null) {
                        clearTimeoutFn(inputDebounceTimer);
                        inputDebounceTimer = null;
                    }
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: slider.value,
                    });
                    renderSlider(spec);
                    postPreviewUpdate();
                    void syncSettingsToBackend(updated);
                });
            }
        }

        function bindColorEvents() {
            for (const spec of COLOR_SPECS) {
                const input = el(spec.key + 'Input', spec.inputId);
                if (!input) continue;
                bindEvent(input, 'input', () => {
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: input.value,
                    });
                    renderAllColors();
                    postPreviewUpdate();
                    scheduleSettingsSync(updated);
                });
                bindEvent(input, 'change', () => {
                    if (inputDebounceTimer !== null) {
                        clearTimeoutFn(inputDebounceTimer);
                        inputDebounceTimer = null;
                    }
                    const updated = saveSettings({
                        ...settings,
                        [spec.key]: input.value,
                    });
                    renderAllColors();
                    postPreviewUpdate();
                    void syncSettingsToBackend(updated);
                });
            }
        }

        function bindGroupToggleEvents() {
            if (!documentRef || typeof documentRef.querySelectorAll !== 'function') return;
            const groups = documentRef.querySelectorAll('details[data-obs-group]');
            groups.forEach((details) => {
                bindEvent(details, 'toggle', () => {
                    const key = details.getAttribute('data-obs-group');
                    if (!key || !Object.prototype.hasOwnProperty.call(DEFAULT_GROUP_STATES, key)) return;
                    groupStates[key] = !!details.open;
                    saveGroupStates();
                });
            });
        }

        function init() {
            if (initialized) {
                renderDialog();
                return;
            }
            initialized = true;
            setIconHref(el('copyLinkIcon', 'obsCopyLinkIcon'), 'radio');
            renderDialog();

            bindEvent(el('copyLinkButton', 'obsCopyLinkButton'), 'click', () => {
                openDialog();
            });
            bindEvent(el('dialogCloseButton', 'obsDialogCloseButton'), 'click', () => {
                closeDialog();
            });
            bindEvent(el('dialogDoneButton', 'obsDialogDoneButton'), 'click', () => {
                closeDialog();
            });
            bindEvent(el('dialogOverlay', 'obsDialogOverlay'), 'click', () => {
                closeDialog();
            });
            bindEvent(documentRef, 'keydown', (event) => {
                if (dialogOpen && event && event.key === 'Escape') {
                    closeDialog();
                }
            });

            const dragCard = el('dragCard', 'obsDragCard');
            bindEvent(dragCard, 'dragstart', (event) => {
                // pywebview (draggable=False) adds a document-level dragstart
                // listener that cancels every <a>/<img> drag; keep this one
                // card draggable without enabling drag app-wide.
                if (event && typeof event.stopPropagation === 'function') {
                    event.stopPropagation();
                }
                const dragUrl = getObsDragUrl();
                if (event && event.dataTransfer) {
                    try {
                        event.dataTransfer.effectAllowed = 'copyLink';
                    } catch (_) {
                        // ignore read-only effectAllowed in test mocks
                    }
                    if (typeof event.dataTransfer.setData === 'function') {
                        event.dataTransfer.setData('text/uri-list', dragUrl);
                        event.dataTransfer.setData('text/plain', dragUrl);
                    }
                }
                recordHandedOutPort();
            });
            bindEvent(dragCard, 'click', (event) => {
                if (event && typeof event.preventDefault === 'function') {
                    event.preventDefault();
                }
                void copyObsLink();
            });

            bindEvent(el('copyUrlButton', 'obsCopyUrlButton'), 'click', () => {
                void copyObsLink();
            });

            bindEvent(el('resetDefaultsButton', 'obsResetDefaultsButton'), 'click', () => {
                resetToDefaults();
            });

            const previewFrame = el('previewFrame', 'obsPreviewFrame');
            bindEvent(previewFrame, 'load', () => {
                updatePreviewScale();
                postPreviewUpdate();
            });

            const previewBox = el('previewBox', 'obsPreviewBox');
            if (previewBox && typeof windowRef.ResizeObserver === 'function') {
                previewObserver = new windowRef.ResizeObserver(() => updatePreviewScale());
                previewObserver.observe(previewBox);
            }
            bindEvent(windowRef, 'resize', () => {
                if (dialogOpen) updatePreviewScale();
            });

            bindSliderEvents();
            bindColorEvents();
            bindGroupToggleEvents();
        }

        function dispose() {
            if (copiedResetTimer !== null) {
                clearTimeoutFn(copiedResetTimer);
                copiedResetTimer = null;
            }
            if (inputDebounceTimer !== null) {
                clearTimeoutFn(inputDebounceTimer);
                inputDebounceTimer = null;
            }
            if (previewObserver && typeof previewObserver.disconnect === 'function') {
                previewObserver.disconnect();
                previewObserver = null;
            }
            for (const ctrl of Object.values(selectControls)) {
                if (ctrl && typeof ctrl.close === 'function') ctrl.close();
            }
            while (boundListeners.length) {
                const unbind = boundListeners.pop();
                unbind();
            }
            initialized = false;
        }

        return {
            init,
            dispose,
            openDialog,
            closeDialog,
            isDialogOpen: () => dialogOpen,
            applyConfig,
            applyI18n,
            setPackEnabled,
            syncToBackend,
            copyObsLink,
            resetToDefaults,
            getObsUrl,
            getObsDragUrl,
            getSettings: () => ({ ...settings }),
            getGroupStates: () => ({ ...groupStates }),
            isDesktopMainWindow,
        };
    }

    const api = {
        STORAGE_KEY,
        GROUPS_STORAGE_KEY,
        LAST_PORT_STORAGE_KEY,
        DEFAULT_GROUP_STATES,
        FONT_SIZE_DEFAULT,
        FONT_SIZE_MIN,
        FONT_SIZE_MAX,
        VALID_DISPLAY_CONTENTS,
        DEFAULT_SETTINGS,
        COPIED_RESET_DELAY_MS,
        PREVIEW_VIRTUAL_WIDTH,
        PREVIEW_VIRTUAL_HEIGHT,
        extractPort,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeSettings,
        create,
    };
    root.ObsController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

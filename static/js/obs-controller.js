(function (root) {
    'use strict';

    const STORAGE_KEY = 'obsSettings.v1';
    const FONT_SIZE_DEFAULT = 42;
    const FONT_SIZE_MIN = 16;
    const FONT_SIZE_MAX = 120;
    const VALID_DISPLAY_CONTENTS = Object.freeze(['both', 'original', 'translation']);
    const DEFAULT_SETTINGS = Object.freeze({
        fontSize: FONT_SIZE_DEFAULT,
        displayContent: 'both',
    });
    const COPIED_RESET_DELAY_MS = 1500;
    const MOBILE_UA_PATTERN = /Mobi|Android|iPhone|iPad|iPod/i;

    function isMobileUserAgent(userAgent) {
        if (root.MobileSafeAreaController && typeof root.MobileSafeAreaController.isMobileUserAgent === 'function') {
            return root.MobileSafeAreaController.isMobileUserAgent(userAgent);
        }
        return MOBILE_UA_PATTERN.test(String(userAgent || ''));
    }

    function normalizeFontSize(value) {
        if (typeof value === 'boolean') return FONT_SIZE_DEFAULT;
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return FONT_SIZE_DEFAULT;
        return Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, Math.round(parsed)));
    }

    function normalizeDisplayContent(value) {
        if (typeof value !== 'string') return 'both';
        const normalized = value.trim().toLowerCase();
        return VALID_DISPLAY_CONTENTS.includes(normalized) ? normalized : 'both';
    }

    function normalizeSettings(raw) {
        const source = raw && typeof raw === 'object' ? raw : {};
        return {
            fontSize: normalizeFontSize(
                source.fontSize !== undefined ? source.fontSize : source.font_size,
            ),
            displayContent: normalizeDisplayContent(
                source.displayContent !== undefined ? source.displayContent : source.display_content,
            ),
        };
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
        const showToast = typeof options.showToast === 'function' ? options.showToast : () => {};
        const buildCustomSelect = typeof options.buildCustomSelect === 'function'
            ? options.buildCustomSelect
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
        let packEnabled = !!isPackEnabledFn();
        let desktopAvailable = !!options.desktopAvailable;
        let backendObsUrl = '';
        let copied = false;
        let copiedResetTimer = null;
        let inputDebounceTimer = null;
        let displayContentControl = null;
        let initialized = false;
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

        function isDesktopMainWindow() {
            return desktopAvailable && !mobile;
        }

        function getObsUrl() {
            if (backendObsUrl) return backendObsUrl;
            const loc = windowRef && windowRef.location ? windowRef.location : {};
            const port = loc.port ? `:${loc.port}` : '';
            return `http://127.0.0.1${port}/obs`;
        }

        function setCopyButtonIcon(iconName) {
            const iconEl = el('copyLinkIcon', 'obsCopyLinkIcon');
            if (!iconEl) return false;
            if (controlIcon && typeof controlIcon.set === 'function') {
                return controlIcon.set(iconEl, iconName);
            }
            const useEl = iconEl.querySelector && iconEl.querySelector('use');
            if (!useEl) return false;
            const href = `icons/lucide-sprite.svg#${iconName}`;
            useEl.setAttribute('href', href);
            return true;
        }

        function updateCopyButton() {
            const btn = el('copyLinkButton', 'obsCopyLinkButton');
            if (!btn) return;
            btn.hidden = !(packEnabled && isDesktopMainWindow());
            const title = t(copied ? 'obs_link_copied' : 'obs_copy_link');
            btn.title = title;
            btn.setAttribute('aria-label', title);
            if (btn.classList) btn.classList.toggle('copied', copied);
        }

        function renderFontSizeSlider() {
            const slider = el('fontSizeSlider', 'obsFontSizeSlider');
            const output = el('fontSizeValue', 'obsFontSizeValue');
            const mark = el('fontSizeDefaultMark', 'obsFontSizeDefaultMark');
            const fs = normalizeFontSize(settings.fontSize);
            if (output) {
                output.textContent = t('obs_font_size_value', { value: fs });
            }
            if (!slider) return;
            slider.min = String(FONT_SIZE_MIN);
            slider.max = String(FONT_SIZE_MAX);
            slider.value = String(fs);
            const span = FONT_SIZE_MAX - FONT_SIZE_MIN;
            const fill = span > 0 ? ((fs - FONT_SIZE_MIN) / span) * 100 : 0;
            if (slider.parentElement && slider.parentElement.style) {
                slider.parentElement.style.setProperty('--fill', `${fill}%`);
            }
            if (mark && mark.classList) {
                mark.classList.toggle('is-current', fs === FONT_SIZE_DEFAULT);
            }
        }

        function renderDisplayContentControl() {
            const host = el('displayContentPickerHost', 'obsDisplayContentPicker');
            if (!host || !documentRef) return null;
            host.innerHTML = '';
            const selectOptions = [
                { value: 'both', label: t('obs_display_both') },
                { value: 'original', label: t('obs_display_original') },
                { value: 'translation', label: t('obs_display_translation') },
            ];
            const onSelect = (nextValue) => {
                const updated = saveSettings({
                    ...settings,
                    displayContent: nextValue,
                });
                void syncSettingsToBackend(updated);
            };
            if (buildCustomSelect) {
                const picker = buildCustomSelect(selectOptions, {
                    value: settings.displayContent,
                    onChange: onSelect,
                });
                host.appendChild(picker);
                displayContentControl = picker;
                return picker;
            }
            const select = documentRef.createElement('select');
            for (const entry of selectOptions) {
                const opt = documentRef.createElement('option');
                opt.value = entry.value;
                opt.textContent = entry.label;
                select.appendChild(opt);
            }
            select.value = settings.displayContent;
            select.addEventListener('change', () => onSelect(select.value));
            host.appendChild(select);
            displayContentControl = select;
            return select;
        }

        function applyStaticText() {
            const titleEl = el('settingsTitle', 'obsSettingsLabel');
            if (titleEl) titleEl.textContent = t('obs_settings_section');
            const fontLabel = el('fontSizeLabel', 'obsFontSizeLabel');
            if (fontLabel) fontLabel.textContent = t('obs_font_size');
            const displayLabel = el('displayContentLabel', 'obsDisplayContentLabel');
            if (displayLabel) displayLabel.textContent = t('obs_display_content');
        }

        function applyVisibility() {
            const section = el('settingsSection', 'obsSettingsSection');
            if (section) {
                section.hidden = !packEnabled;
            }
            updateCopyButton();
        }

        function renderSettingsSection() {
            applyStaticText();
            renderFontSizeSlider();
            renderDisplayContentControl();
            applyVisibility();
        }

        async function syncSettingsToBackend(target = settings) {
            if (!fetchImpl) return false;
            const normalized = normalizeSettings(target);
            try {
                const response = await fetchImpl('/obs-settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        font_size: normalized.fontSize,
                        display_content: normalized.displayContent,
                    }),
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
            setCopyButtonIcon('check');
            updateCopyButton();
            if (copiedResetTimer !== null) {
                clearTimeoutFn(copiedResetTimer);
            }
            copiedResetTimer = setTimeoutFn(() => {
                copiedResetTimer = null;
                copied = false;
                setCopyButtonIcon('radio');
                updateCopyButton();
            }, COPIED_RESET_DELAY_MS);
        }

        async function copyObsLink() {
            const url = getObsUrl();
            const ok = await copyTextWithFallback(url);
            if (ok) {
                markCopiedVisual();
            } else {
                showToast(t('obs_copy_failed'));
            }
            return ok;
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
                if (
                    incoming.fontSize !== settings.fontSize
                    || incoming.displayContent !== settings.displayContent
                ) {
                    settings = incoming;
                    dirty = true;
                }
            }
            if (dirty || !displayContentControl) {
                renderSettingsSection();
            } else {
                renderFontSizeSlider();
                applyVisibility();
            }
            return true;
        }

        function setPackEnabled(enabled) {
            packEnabled = !!enabled;
            applyVisibility();
            return packEnabled;
        }

        function applyI18n() {
            renderSettingsSection();
        }

        function bindEvent(target, type, handler) {
            if (!target || typeof target.addEventListener !== 'function') return;
            target.addEventListener(type, handler);
            boundListeners.push(() => target.removeEventListener(type, handler));
        }

        function scheduleFontSizeSync(nextSettings) {
            if (inputDebounceTimer !== null) {
                clearTimeoutFn(inputDebounceTimer);
            }
            inputDebounceTimer = setTimeoutFn(() => {
                inputDebounceTimer = null;
                void syncSettingsToBackend(nextSettings);
            }, 60);
        }

        function init() {
            if (initialized) {
                renderSettingsSection();
                return;
            }
            initialized = true;
            setCopyButtonIcon('radio');
            renderSettingsSection();

            bindEvent(el('copyLinkButton', 'obsCopyLinkButton'), 'click', () => {
                void copyObsLink();
            });

            const slider = el('fontSizeSlider', 'obsFontSizeSlider');
            bindEvent(slider, 'input', () => {
                const updated = saveSettings({
                    ...settings,
                    fontSize: slider.value,
                });
                renderFontSizeSlider();
                scheduleFontSizeSync(updated);
            });
            bindEvent(slider, 'change', () => {
                if (inputDebounceTimer !== null) {
                    clearTimeoutFn(inputDebounceTimer);
                    inputDebounceTimer = null;
                }
                const updated = saveSettings({
                    ...settings,
                    fontSize: slider.value,
                });
                renderFontSizeSlider();
                void syncSettingsToBackend(updated);
            });
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
            while (boundListeners.length) {
                const unbind = boundListeners.pop();
                unbind();
            }
            initialized = false;
        }

        return {
            init,
            dispose,
            applyConfig,
            applyI18n,
            setPackEnabled,
            syncToBackend,
            copyObsLink,
            getObsUrl,
            getSettings: () => ({ ...settings }),
            isDesktopMainWindow,
        };
    }

    const api = {
        STORAGE_KEY,
        FONT_SIZE_DEFAULT,
        FONT_SIZE_MIN,
        FONT_SIZE_MAX,
        VALID_DISPLAY_CONTENTS,
        DEFAULT_SETTINGS,
        COPIED_RESET_DELAY_MS,
        normalizeFontSize,
        normalizeDisplayContent,
        normalizeSettings,
        create,
    };
    root.ObsController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

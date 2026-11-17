(function (root) {
    'use strict';

    const STORAGE_KEY = 'transcriptSettings.v1';
    const VALID_FORMATS = Object.freeze(['srt', 'txt']);
    const DEFAULT_SETTINGS = Object.freeze({
        autoSave: false,
        format: 'srt',
        directory: '',
    });
    const MOBILE_UA_PATTERN = /Mobi|Android|iPhone|iPad|iPod/i;

    function isMobileUserAgent(userAgent) {
        if (root.MobileSafeAreaController && typeof root.MobileSafeAreaController.isMobileUserAgent === 'function') {
            return root.MobileSafeAreaController.isMobileUserAgent(userAgent);
        }
        return MOBILE_UA_PATTERN.test(String(userAgent || ''));
    }

    function normalizeFormat(value) {
        const normalized = String(value || 'srt').trim().toLowerCase();
        return VALID_FORMATS.includes(normalized) ? normalized : 'srt';
    }

    function normalizeSettings(raw) {
        const source = raw && typeof raw === 'object' ? raw : {};
        return {
            autoSave: typeof source.autoSave === 'boolean'
                ? source.autoSave
                : (typeof source.auto_save === 'boolean' ? source.auto_save : false),
            format: normalizeFormat(source.format),
            directory: typeof source.directory === 'string' ? source.directory.trim() : '',
        };
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const storage = options.storage || root.localStorage;
        const fetchImpl = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const showToast = typeof options.showToast === 'function' ? options.showToast : () => {};
        const buildToggleSwitch = typeof options.buildToggleSwitch === 'function'
            ? options.buildToggleSwitch
            : null;
        const buildCustomSelect = typeof options.buildCustomSelect === 'function'
            ? options.buildCustomSelect
            : null;
        const isPackEnabledFn = typeof options.isPackEnabled === 'function'
            ? options.isPackEnabled
            : () => true;
        const logger = options.console || root.console || { warn() {}, error() {} };
        const elements = options.elements || {};
        const mobile = options.isMobile !== undefined
            ? !!options.isMobile
            : isMobileUserAgent(
                options.userAgent !== undefined
                    ? options.userAgent
                    : root.navigator && root.navigator.userAgent,
            );

        let hasStoredSettings = false;
        let settings = loadStoredSettings();
        let backendDefaultDirectory = '';
        let packEnabled = !!isPackEnabledFn();
        let desktopAvailable = !!options.desktopAvailable;
        let status = {
            has_entries: false,
            count: 0,
            pack_enabled: packEnabled,
            run_active: false,
        };
        let exporting = false;
        let choosingFolder = false;
        let openingFolder = false;
        let autoSaveControl = null;
        let formatControl = null;
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
                    logger.warn('Failed to persist transcript settings:', error);
                }
            }
            return { ...settings };
        }

        function isDesktopMainWindow() {
            return desktopAvailable && !mobile;
        }

        function effectiveDirectory() {
            return settings.directory || backendDefaultDirectory || '';
        }

        function updateExportButton() {
            const btn = el('exportButton', 'exportTranscriptButton');
            if (!btn) return;
            const visible = packEnabled && isDesktopMainWindow();
            btn.hidden = !visible;
            btn.disabled = !status.has_entries || exporting;
            const title = t(status.has_entries ? 'transcript_export' : 'transcript_export_empty');
            btn.title = title;
            btn.setAttribute('aria-label', title);
        }

        function updateFolderDisplay() {
            const pathEl = el('folderPath', 'transcriptFolderPath');
            if (pathEl) {
                pathEl.textContent = effectiveDirectory();
            }
            const actionsEl = el('folderActionsRow', 'transcriptFolderActions');
            const changeBtn = el('changeFolderButton', 'transcriptChangeFolderButton');
            const openBtn = el('openFolderButton', 'transcriptOpenFolderButton');
            const hintEl = el('desktopHint', 'transcriptDesktopHint');
            const desktopMode = isDesktopMainWindow();

            if (actionsEl) actionsEl.hidden = !desktopMode;
            if (changeBtn) {
                changeBtn.hidden = !desktopMode;
                changeBtn.disabled = !desktopMode || choosingFolder;
            }
            if (openBtn) {
                openBtn.hidden = !desktopMode;
                openBtn.disabled = !desktopMode || openingFolder;
            }
            if (hintEl) {
                hintEl.hidden = desktopMode;
                hintEl.textContent = desktopMode ? '' : t('transcript_desktop_only_hint');
            }
        }

        function applyVisibility() {
            const section = el('settingsSection', 'transcriptSettingsSection');
            if (section) {
                section.hidden = !packEnabled;
            }
            updateExportButton();
            updateFolderDisplay();
        }

        function renderAutoSaveControl() {
            const host = el('autoSavePickerHost', 'transcriptAutoSavePicker');
            if (!host || !documentRef) return null;
            host.innerHTML = '';
            const currentVal = settings.autoSave ? 'true' : 'false';
            const onToggle = (nextValue) => {
                const updated = saveSettings({
                    ...settings,
                    autoSave: nextValue === 'true',
                });
                void syncSettingsToBackend(updated);
            };
            if (buildToggleSwitch) {
                const toggle = buildToggleSwitch({
                    value: currentVal,
                    onValue: 'true',
                    offValue: 'false',
                    onChange: onToggle,
                });
                toggle.setAttribute('aria-label', t('transcript_auto_save'));
                host.appendChild(toggle);
                autoSaveControl = toggle;
                return toggle;
            }
            const checkbox = documentRef.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = settings.autoSave;
            checkbox.setAttribute('aria-label', t('transcript_auto_save'));
            checkbox.addEventListener('change', () => {
                onToggle(checkbox.checked ? 'true' : 'false');
            });
            host.appendChild(checkbox);
            autoSaveControl = checkbox;
            return checkbox;
        }

        function renderFormatControl() {
            const host = el('formatPickerHost', 'transcriptFormatPicker');
            if (!host || !documentRef) return null;
            host.innerHTML = '';
            const selectOptions = [
                { value: 'srt', label: t('transcript_format_srt') },
                { value: 'txt', label: t('transcript_format_txt') },
            ];
            const onSelect = (nextFormat) => {
                const updated = saveSettings({
                    ...settings,
                    format: nextFormat,
                });
                void syncSettingsToBackend(updated);
            };
            if (buildCustomSelect) {
                const picker = buildCustomSelect(selectOptions, {
                    value: settings.format,
                    onChange: onSelect,
                });
                host.appendChild(picker);
                formatControl = picker;
                return picker;
            }
            const select = documentRef.createElement('select');
            for (const entry of selectOptions) {
                const opt = documentRef.createElement('option');
                opt.value = entry.value;
                opt.textContent = entry.label;
                select.appendChild(opt);
            }
            select.value = settings.format;
            select.addEventListener('change', () => onSelect(select.value));
            host.appendChild(select);
            formatControl = select;
            return select;
        }

        function applyStaticText() {
            const titleEl = el('settingsTitle', 'transcriptSettingsLabel');
            if (titleEl) titleEl.textContent = t('transcript_settings_section');
            const autoSaveLabel = el('autoSaveLabel', 'transcriptAutoSaveLabel');
            if (autoSaveLabel) autoSaveLabel.textContent = t('transcript_auto_save');
            const formatLabel = el('formatLabel', 'transcriptFormatLabel');
            if (formatLabel) formatLabel.textContent = t('transcript_format');
            const folderLabel = el('folderLabel', 'transcriptFolderLabel');
            if (folderLabel) folderLabel.textContent = t('transcript_folder');
            const changeBtn = el('changeFolderButton', 'transcriptChangeFolderButton');
            if (changeBtn) changeBtn.textContent = t('transcript_change_folder');
            const openBtn = el('openFolderButton', 'transcriptOpenFolderButton');
            if (openBtn) openBtn.textContent = t('transcript_open_folder');
        }

        function renderSettingsSection() {
            applyStaticText();
            renderAutoSaveControl();
            renderFormatControl();
            applyVisibility();
        }

        async function syncSettingsToBackend(target = settings) {
            if (!fetchImpl) return false;
            const payload = {
                auto_save: !!target.autoSave,
                format: normalizeFormat(target.format),
            };
            if (target.directory) {
                payload.directory = target.directory;
            }
            try {
                const response = await fetchImpl('/transcript/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    return false;
                }
                if (data && data.settings && typeof data.settings === 'object') {
                    if (typeof data.settings.directory === 'string' && data.settings.directory.trim()) {
                        backendDefaultDirectory = data.settings.directory.trim();
                    }
                    updateFolderDisplay();
                }
                return true;
            } catch (error) {
                logger.warn('Failed to sync transcript settings to backend:', error);
                return false;
            }
        }

        async function syncToBackend() {
            if (!hasStoredSettings) return false;
            return syncSettingsToBackend(settings);
        }

        function applyStatus(nextStatus, { render = true } = {}) {
            if (!nextStatus || typeof nextStatus !== 'object') return false;
            status = {
                has_entries: !!nextStatus.has_entries,
                count: Number.isFinite(Number(nextStatus.count)) ? Math.max(0, Number(nextStatus.count)) : 0,
                pack_enabled: typeof nextStatus.pack_enabled === 'boolean' ? nextStatus.pack_enabled : packEnabled,
                run_active: !!nextStatus.run_active,
            };
            if (render) {
                updateExportButton();
            }
            return true;
        }

        function applyConfig(data = {}) {
            if (!data || typeof data !== 'object') return false;
            if (typeof data.transcript_desktop_available === 'boolean') {
                desktopAvailable = data.transcript_desktop_available;
            }
            if (data.transcript_status && typeof data.transcript_status === 'object') {
                applyStatus(data.transcript_status, { render: false });
            }
            let controlsDirty = false;
            if (data.transcript_settings && typeof data.transcript_settings === 'object') {
                const incoming = data.transcript_settings;
                if (typeof incoming.directory === 'string' && incoming.directory.trim()) {
                    backendDefaultDirectory = incoming.directory.trim();
                }
                if (!hasStoredSettings) {
                    const nextAutoSave = typeof incoming.auto_save === 'boolean'
                        ? incoming.auto_save
                        : settings.autoSave;
                    const nextFormat = normalizeFormat(incoming.format || settings.format);
                    if (nextAutoSave !== settings.autoSave || nextFormat !== settings.format) {
                        settings = {
                            ...settings,
                            autoSave: nextAutoSave,
                            format: nextFormat,
                        };
                        controlsDirty = true;
                    }
                }
            }
            if (controlsDirty || !autoSaveControl || !formatControl) {
                renderSettingsSection();
            } else {
                applyVisibility();
            }
            return true;
        }

        function setPackEnabled(enabled) {
            packEnabled = !!enabled;
            applyVisibility();
            return packEnabled;
        }

        async function handleExportClick() {
            if (!packEnabled || !isDesktopMainWindow() || exporting || !status.has_entries || !fetchImpl) {
                return false;
            }
            exporting = true;
            updateExportButton();
            try {
                const response = await fetchImpl('/transcript/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ format: settings.format }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok && data.status === 'ok') {
                    showToast(t('transcript_export_success', { path: data.path || '' }), false);
                    return true;
                }
                if (response.ok && data.status === 'cancelled') {
                    return false;
                }
                showToast((data && data.message) || t('transcript_export_failed'), true);
                return false;
            } catch (error) {
                logger.error('Failed to export transcript:', error);
                showToast(t('transcript_export_failed'), true);
                return false;
            } finally {
                exporting = false;
                updateExportButton();
            }
        }

        async function handleChangeFolderClick() {
            if (!isDesktopMainWindow() || choosingFolder || !fetchImpl) {
                return false;
            }
            choosingFolder = true;
            updateFolderDisplay();
            try {
                const response = await fetchImpl('/transcript/folder', {
                    method: 'POST',
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    showToast((data && data.message) || t('transcript_folder_failed'), true);
                    return false;
                }
                if (data && !data.cancelled && typeof data.directory === 'string' && data.directory.trim()) {
                    const nextDir = data.directory.trim();
                    backendDefaultDirectory = nextDir;
                    saveSettings({
                        ...settings,
                        directory: nextDir,
                    });
                    updateFolderDisplay();
                    return true;
                }
                return false;
            } catch (error) {
                logger.error('Failed to choose transcript folder:', error);
                showToast(t('transcript_folder_failed'), true);
                return false;
            } finally {
                choosingFolder = false;
                updateFolderDisplay();
            }
        }

        async function handleOpenFolderClick() {
            if (!isDesktopMainWindow() || openingFolder || !fetchImpl) {
                return false;
            }
            openingFolder = true;
            updateFolderDisplay();
            try {
                const response = await fetchImpl('/transcript/open-folder', {
                    method: 'POST',
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    showToast((data && data.message) || t('transcript_open_folder_failed'), true);
                    return false;
                }
                return true;
            } catch (error) {
                logger.error('Failed to open transcript folder:', error);
                showToast(t('transcript_open_folder_failed'), true);
                return false;
            } finally {
                openingFolder = false;
                updateFolderDisplay();
            }
        }

        function bind(node, event, handler) {
            if (!node) return;
            node.addEventListener(event, handler);
            boundListeners.push([node, event, handler]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            bind(el('exportButton', 'exportTranscriptButton'), 'click', () => {
                void handleExportClick();
            });
            bind(el('changeFolderButton', 'transcriptChangeFolderButton'), 'click', () => {
                void handleChangeFolderClick();
            });
            bind(el('openFolderButton', 'transcriptOpenFolderButton'), 'click', () => {
                void handleOpenFolderClick();
            });
            renderSettingsSection();
            return true;
        }

        function destroy() {
            while (boundListeners.length) {
                const [node, event, handler] = boundListeners.pop();
                node.removeEventListener(event, handler);
            }
            initialized = false;
        }

        function applyI18n() {
            renderSettingsSection();
        }

        return {
            STORAGE_KEY,
            applyConfig,
            applyI18n,
            applyStatus,
            applyVisibility,
            destroy,
            getSettings: () => ({ ...settings, effectiveDirectory: effectiveDirectory() }),
            getStatus: () => ({ ...status }),
            handleChangeFolderClick,
            handleExportClick,
            handleOpenFolderClick,
            init,
            isDesktopMainWindow,
            isPackEnabled: () => packEnabled,
            renderSettingsSection,
            setPackEnabled,
            syncToBackend,
        };
    }

    const api = {
        DEFAULT_SETTINGS,
        STORAGE_KEY,
        VALID_FORMATS,
        create,
        isMobileUserAgent,
    };
    root.TranscriptController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

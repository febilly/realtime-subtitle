(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const showToast = typeof options.showToast === 'function' ? options.showToast : () => {};
        const elements = options.elements || {};
        const overlay = elements.overlay || null;
        const dialog = elements.dialog || null;
        const createButton = elements.createButton || null;
        const skipButton = elements.skipButton || null;
        let initialized = false;
        let busy = false;

        function setText(id, value) {
            const element = documentRef && documentRef.getElementById(id);
            if (element) element.textContent = value;
        }

        function applyI18n() {
            setText('desktopShortcutTitle', t('desktop_shortcut_title'));
            setText('desktopShortcutHint', t('desktop_shortcut_hint'));
            setText('desktopShortcutCreateTitle', t('desktop_shortcut_create'));
            setText('desktopShortcutCreateDesc', t('desktop_shortcut_create_desc'));
            setText('desktopShortcutSkipTitle', t('desktop_shortcut_skip'));
            setText('desktopShortcutSkipDesc', t('desktop_shortcut_skip_desc'));
        }

        function show() {
            applyI18n();
            if (overlay) overlay.hidden = false;
            if (dialog) dialog.hidden = false;
        }

        function hide() {
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
        }

        function setBusy(value) {
            busy = !!value;
            if (createButton) createButton.disabled = busy;
            if (skipButton) skipButton.disabled = busy;
        }

        async function offer() {
            if (!fetchRef || busy) return false;
            try {
                const response = await fetchRef('/desktop-shortcut');
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.available || data.exists) return false;
                show();
                return true;
            } catch (error) {
                return false;
            }
        }

        async function createShortcut() {
            if (!fetchRef || busy) return false;
            setBusy(true);
            try {
                const response = await fetchRef('/desktop-shortcut', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'create' }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.created) {
                    showToast(t('desktop_shortcut_failed'), true);
                    return false;
                }
                hide();
                showToast(t('desktop_shortcut_created'));
                return true;
            } catch (error) {
                showToast(t('desktop_shortcut_failed'), true);
                return false;
            } finally {
                setBusy(false);
            }
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (createButton) createButton.addEventListener('click', () => { void createShortcut(); });
            if (skipButton) skipButton.addEventListener('click', hide);
            applyI18n();
            return true;
        }

        return { applyI18n, createShortcut, hide, init, offer, show };
    }

    const api = { create };
    root.DesktopShortcut = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

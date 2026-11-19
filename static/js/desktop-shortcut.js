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
        const field = elements.field
            || (documentRef && documentRef.getElementById('loginDesktopShortcutField'));
        const checkbox = elements.checkbox
            || (documentRef && documentRef.getElementById('loginDesktopShortcutCheckbox'));
        const label = elements.label
            || (documentRef && documentRef.getElementById('loginDesktopShortcutLabel'));
        let initialized = false;
        let busy = false;
        let eligible = false;
        let syncSequence = 0;

        function applyI18n() {
            const text = t('desktop_shortcut_create');
            if (label) {
                label.textContent = text;
                return;
            }
            const element = documentRef && documentRef.getElementById('loginDesktopShortcutLabel');
            if (element) element.textContent = text;
        }

        function setVisible(visible) {
            eligible = !!visible;
            if (field) field.hidden = !eligible;
        }

        async function syncOption({ resetChecked = false } = {}) {
            const token = ++syncSequence;
            if (resetChecked && checkbox) checkbox.checked = true;
            if (!fetchRef || !field || !checkbox) {
                setVisible(false);
                return false;
            }
            try {
                const response = await fetchRef('/desktop-shortcut');
                const data = await response.json().catch(() => ({}));
                if (token !== syncSequence) return false;
                if (!response.ok || !data.available || data.exists) {
                    setVisible(false);
                    return false;
                }
                applyI18n();
                setVisible(true);
                return true;
            } catch (error) {
                if (token === syncSequence) setVisible(false);
                return false;
            }
        }

        async function createIfRequested() {
            if (!fetchRef || busy || !eligible || !field || field.hidden || !checkbox || !checkbox.checked) {
                return false;
            }
            busy = true;
            try {
                const statusResponse = await fetchRef('/desktop-shortcut');
                const statusData = await statusResponse.json().catch(() => ({}));
                if (!statusResponse.ok || !statusData.available || statusData.exists) {
                    setVisible(false);
                    return false;
                }
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
                setVisible(false);
                showToast(t('desktop_shortcut_created'));
                return true;
            } catch (error) {
                showToast(t('desktop_shortcut_failed'), true);
                return false;
            } finally {
                busy = false;
            }
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (checkbox) checkbox.checked = true;
            setVisible(false);
            applyI18n();
            return true;
        }

        return { applyI18n, createIfRequested, init, syncOption };
    }

    const api = { create };
    root.DesktopShortcut = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

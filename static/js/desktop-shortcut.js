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

        function resolveTargets() {
            if (elements.targets && typeof elements.targets === 'object') {
                return Object.entries(elements.targets).map(([name, target]) => ({
                    name,
                    field: (target && target.field) || null,
                    checkbox: (target && target.checkbox) || null,
                    label: (target && target.label) || null,
                }));
            }
            return [{
                name: 'default',
                field: elements.field
                    || (documentRef && documentRef.getElementById('loginDesktopShortcutField')),
                checkbox: elements.checkbox
                    || (documentRef && documentRef.getElementById('loginDesktopShortcutCheckbox')),
                label: elements.label
                    || (documentRef && documentRef.getElementById('loginDesktopShortcutLabel')),
            }];
        }

        const targets = resolveTargets();
        let initialized = false;
        let busy = false;
        let eligible = false;
        let syncSequence = 0;

        function getTarget(targetName = null) {
            if (!targetName) return targets[0] || null;
            return targets.find((entry) => entry.name === targetName) || null;
        }

        function applyI18n() {
            const text = t('desktop_shortcut_create');
            for (const target of targets) {
                if (target.label) target.label.textContent = text;
            }
        }

        function hideAllFields() {
            eligible = false;
            for (const target of targets) {
                if (target.field) target.field.hidden = true;
            }
        }

        function setTargetVisible(target, visible) {
            if (!visible) {
                if (target && target.field) target.field.hidden = true;
                eligible = false;
                return;
            }
            eligible = true;
            if (target && target.field) target.field.hidden = false;
        }

        async function syncOption({ resetChecked = false, target: targetName = null } = {}) {
            const token = ++syncSequence;
            const active = getTarget(targetName);
            if (resetChecked && active && active.checkbox) active.checkbox.checked = true;
            if (!fetchRef || !active || !active.field || !active.checkbox) {
                setTargetVisible(active, false);
                return false;
            }
            try {
                const response = await fetchRef('/desktop-shortcut');
                const data = await response.json().catch(() => ({}));
                if (token !== syncSequence) return false;
                if (!response.ok || !data.available || data.exists) {
                    hideAllFields();
                    return false;
                }
                applyI18n();
                setTargetVisible(active, true);
                return true;
            } catch (error) {
                if (token === syncSequence) hideAllFields();
                return false;
            }
        }

        async function createIfRequested({ target: targetName = null } = {}) {
            const active = getTarget(targetName);
            if (!fetchRef || busy || !eligible || !active || !active.field || active.field.hidden
                || !active.checkbox || !active.checkbox.checked) {
                return false;
            }
            busy = true;
            try {
                const statusResponse = await fetchRef('/desktop-shortcut');
                const statusData = await statusResponse.json().catch(() => ({}));
                if (!statusResponse.ok || !statusData.available || statusData.exists) {
                    hideAllFields();
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
                hideAllFields();
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
            for (const target of targets) {
                if (target.checkbox) target.checkbox.checked = true;
            }
            hideAllFields();
            applyI18n();
            return true;
        }

        return { applyI18n, createIfRequested, init, syncOption };
    }

    const api = { create };
    root.DesktopShortcut = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

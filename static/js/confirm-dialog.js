(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const elements = options.elements || {};
        const overlay = elements.overlay || null;
        const dialog = elements.dialog || null;
        const messageElement = elements.message || null;
        const okButton = elements.okButton || null;
        const cancelButton = elements.cancelButton || null;
        const listeners = [];
        let resolvePending = null;
        let initialized = false;

        function close(result) {
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
            if (documentRef) documentRef.removeEventListener('keydown', handleKeydown);
            if (resolvePending) {
                const resolve = resolvePending;
                resolvePending = null;
                resolve(!!result);
            }
        }

        function handleKeydown(event) {
            if (event.key === 'Escape') {
                close(false);
            } else if (event.key === 'Enter') {
                close(true);
            }
        }

        function show(message, { okLabel, cancelLabel, danger = false } = {}) {
            if (!dialog || !overlay) {
                return Promise.resolve(windowRef.confirm(message));
            }
            if (resolvePending) close(false);
            if (messageElement) messageElement.textContent = message;
            if (okButton) {
                okButton.textContent = okLabel || t('confirm');
                okButton.className = danger ? 'danger-button' : 'primary-button';
            }
            if (cancelButton) cancelButton.textContent = cancelLabel || t('cancel');
            overlay.hidden = false;
            dialog.hidden = false;
            documentRef.addEventListener('keydown', handleKeydown);
            if (cancelButton) cancelButton.focus();
            return new Promise((resolve) => {
                resolvePending = resolve;
            });
        }

        function bind(target, event, listener) {
            if (!target) return;
            target.addEventListener(event, listener);
            listeners.push([target, event, listener]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            bind(okButton, 'click', () => close(true));
            bind(cancelButton, 'click', () => close(false));
            bind(overlay, 'click', () => close(false));
            return true;
        }

        function destroy() {
            close(false);
            for (const [target, event, listener] of listeners.splice(0)) {
                target.removeEventListener(event, listener);
            }
            initialized = false;
        }

        function getDebugState() {
            return {
                initialized,
                pending: !!resolvePending,
            };
        }

        return {
            close,
            destroy,
            getDebugState,
            handleKeydown,
            init,
            show,
        };
    }

    const api = { create };
    root.ConfirmDialog = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

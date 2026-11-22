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
        let altButton = elements.altButton || (dialog && dialog.querySelector ? dialog.querySelector('#confirmAltButton') : null);
        const listeners = [];
        let resolvePending = null;
        let pendingMode = 'confirm';
        let initialized = false;
        let dismissible = true;

        function ensureAltButton() {
            if (altButton || !dialog || !documentRef) return altButton;
            const actions = (okButton && okButton.parentNode)
                || (cancelButton && cancelButton.parentNode)
                || (dialog.querySelector && dialog.querySelector('.confirm-actions'))
                || dialog;
            const button = documentRef.createElement('button');
            button.type = 'button';
            button.className = 'secondary-button';
            button.hidden = true;
            if (okButton && okButton.parentNode === actions) {
                actions.insertBefore(button, okButton);
            } else {
                actions.appendChild(button);
            }
            altButton = button;
            if (initialized) {
                bind(altButton, 'click', () => finish('alt'));
            }
            return altButton;
        }

        function finish(outcome) {
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
            if (altButton) altButton.hidden = true;
            if (documentRef) documentRef.removeEventListener('keydown', handleKeydown);
            if (resolvePending) {
                const resolve = resolvePending;
                const mode = pendingMode;
                resolvePending = null;
                pendingMode = 'confirm';
                if (mode === 'choice') {
                    resolve(outcome);
                } else {
                    resolve(outcome === 'ok' || outcome === true);
                }
            }
        }

        function close(result) {
            if (pendingMode === 'choice') {
                if (result === 'ok' || result === 'alt' || result === 'cancel') {
                    finish(result);
                } else {
                    finish(result ? 'ok' : 'cancel');
                }
                return;
            }
            finish(!!result);
        }

        function handleKeydown(event) {
            if (event.key === 'Escape' && dismissible) {
                finish(pendingMode === 'choice' ? 'cancel' : false);
            } else if (event.key === 'Enter') {
                event.preventDefault();
                if (pendingMode === 'choice') {
                    if (event.target === cancelButton) finish('cancel');
                    else if (altButton && !altButton.hidden && event.target === altButton) finish('alt');
                    else finish('ok');
                } else {
                    finish(event.target !== cancelButton);
                }
            }
        }

        function show(message, { okLabel, cancelLabel, danger = false, allowCancel = true, dismissible: canDismiss = true } = {}) {
            if (!dialog || !overlay) {
                return Promise.resolve(windowRef.confirm(message));
            }
            if (resolvePending) close(false);
            pendingMode = 'confirm';
            dismissible = canDismiss;
            if (messageElement) messageElement.textContent = message;
            if (altButton) altButton.hidden = true;
            if (okButton) {
                okButton.textContent = okLabel || t('confirm');
                okButton.className = danger ? 'danger-button' : 'primary-button';
            }
            if (cancelButton) {
                cancelButton.textContent = cancelLabel || t('cancel');
                cancelButton.hidden = !allowCancel;
            }
            overlay.hidden = false;
            dialog.hidden = false;
            documentRef.addEventListener('keydown', handleKeydown);
            const focusButton = allowCancel ? cancelButton : okButton;
            if (focusButton) focusButton.focus();
            return new Promise((resolve) => {
                resolvePending = resolve;
            });
        }

        function choose(message, {
            okLabel,
            altLabel = '',
            cancelLabel,
            danger = false,
            altDanger = false,
            allowCancel = true,
            dismissible: canDismiss = true,
        } = {}) {
            if (!dialog || !overlay) {
                return Promise.resolve(windowRef.confirm(message) ? 'ok' : 'alt');
            }
            if (resolvePending) close(false);
            pendingMode = 'choice';
            dismissible = canDismiss;
            if (messageElement) messageElement.textContent = message;
            if (okButton) {
                okButton.textContent = okLabel || t('confirm');
                okButton.className = danger ? 'danger-button' : 'primary-button';
            }
            const middle = altLabel ? ensureAltButton() : altButton;
            if (middle) {
                if (altLabel) {
                    middle.textContent = altLabel;
                    middle.className = altDanger ? 'danger-button' : 'secondary-button';
                    middle.hidden = false;
                } else {
                    middle.hidden = true;
                }
            }
            if (cancelButton) {
                cancelButton.textContent = cancelLabel || t('cancel');
                cancelButton.hidden = !allowCancel;
            }
            overlay.hidden = false;
            dialog.hidden = false;
            documentRef.addEventListener('keydown', handleKeydown);
            const focusButton = allowCancel ? cancelButton : okButton;
            if (focusButton) focusButton.focus();
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
            bind(okButton, 'click', () => finish(pendingMode === 'choice' ? 'ok' : true));
            bind(altButton, 'click', () => finish('alt'));
            bind(cancelButton, 'click', () => finish(pendingMode === 'choice' ? 'cancel' : false));
            bind(overlay, 'click', () => {
                if (dismissible) finish(pendingMode === 'choice' ? 'cancel' : false);
            });
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
            choose,
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

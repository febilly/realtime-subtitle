(function (root) {
    'use strict';

    const DEFAULT_TOAST_TIMEOUT_MS = 10000;

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchImpl = options.fetch || root.fetch;
        const subtitleContainer = options.subtitleContainer || null;
        const toastContainer = options.toast || null;
        const bottomToastContainer = options.bottomToast || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const localizeBackendMessage = typeof options.localizeBackendMessage === 'function'
            ? options.localizeBackendMessage
            : (message) => message;
        const escapeHtml = typeof options.escapeHtml === 'function'
            ? options.escapeHtml
            : (value) => String(value ?? '');
        const logger = options.console || root.console || { error() {} };
        const schedule = options.setTimeout || ((...args) => root.setTimeout(...args));
        const cancel = options.clearTimeout || ((...args) => root.clearTimeout(...args));
        const activeToasts = new Map();

        function updateContainerVisibility(container) {
            if (!container) return;
            container.hidden = ![...activeToasts.values()]
                .some((entry) => entry.container === container);
        }

        function hideToast() {
            if (!toastContainer && !bottomToastContainer) return;
            for (const entry of activeToasts.values()) {
                if (entry.timer) cancel(entry.timer);
                entry.node.remove();
            }
            activeToasts.clear();
            if (toastContainer) toastContainer.hidden = true;
            if (bottomToastContainer) bottomToastContainer.hidden = true;
        }

        function dismissToast(messageKey) {
            const entry = activeToasts.get(messageKey);
            if (!entry) return;
            if (entry.timer) cancel(entry.timer);
            entry.node.remove();
            activeToasts.delete(messageKey);
            updateContainerVisibility(entry.container);
        }

        function showToast(message, isError = false, toastOptions = {}) {
            const targetContainer = toastOptions.position === 'bottom' && bottomToastContainer
                ? bottomToastContainer
                : toastContainer;
            if (!targetContainer) return;
            const messageText = String(message ?? '');
            dismissToast(messageText);

            const toast = documentRef.createElement('div');
            toast.className = 'toast';
            toast.setAttribute('role', isError ? 'alert' : 'status');
            const text = documentRef.createElement('span');
            text.textContent = messageText;
            toast.appendChild(text);
            const configuredActions = Array.isArray(toastOptions.actions)
                ? toastOptions.actions.filter((item) => (
                    item && item.label && typeof item.onAction === 'function'
                ))
                : [];
            const toastActions = configuredActions.length
                ? configuredActions
                : (toastOptions.actionLabel && typeof toastOptions.onAction === 'function'
                    ? [{ label: toastOptions.actionLabel, onAction: toastOptions.onAction }]
                    : []);
            toastActions.forEach((item, index) => {
                if (index > 0 && toastOptions.actionSeparator) {
                    const separator = documentRef.createElement('span');
                    separator.className = 'toast-action-separator';
                    separator.textContent = toastOptions.actionSeparator;
                    toast.appendChild(separator);
                }
                const action = documentRef.createElement('button');
                action.type = 'button';
                action.className = 'toast-action';
                action.textContent = item.label;
                action.addEventListener('click', () => {
                    dismissToast(messageText);
                    item.onAction();
                });
                toast.appendChild(action);
            });
            if (toastActions.length && toastOptions.actionSuffix) {
                const suffix = documentRef.createElement('span');
                suffix.textContent = toastOptions.actionSuffix;
                toast.appendChild(suffix);
            }
            const close = documentRef.createElement('button');
            close.type = 'button';
            close.className = 'toast-close';
            close.setAttribute('aria-label', t('close'));
            close.setAttribute('title', t('close'));
            close.addEventListener('click', () => dismissToast(messageText));
            toast.appendChild(close);
            toast.classList.toggle('error', !!isError);
            targetContainer.appendChild(toast);
            const entry = { node: toast, timer: null, container: targetContainer };
            activeToasts.set(messageText, entry);
            targetContainer.hidden = false;
            if (isError) return;
            const requestedTimeoutMs = Number(toastOptions.timeoutMs);
            const timeoutMs = Number.isFinite(requestedTimeoutMs) && requestedTimeoutMs > 0
                ? requestedTimeoutMs
                : DEFAULT_TOAST_TIMEOUT_MS;
            entry.timer = schedule(() => {
                if (activeToasts.get(messageText) !== entry) return;
                entry.timer = null;
                dismissToast(messageText);
            }, timeoutMs);
        }

        function displayErrorMessage(message) {
            const localizedMessage = localizeBackendMessage(message);
            subtitleContainer.innerHTML = `
        <div class="error-message-overlay">
            <h2 class="error-title">${escapeHtml(t('error_title'))}</h2>
            <p class="error-text">${escapeHtml(localizedMessage)}</p>
            <p class="error-suggestion">${escapeHtml(t('error_suggestion_api'))}</p>
        </div>
    `;
            subtitleContainer.scrollTop = 0;
        }

        async function fetchApiKeyStatus() {
            try {
                const response = await fetchImpl('/api-key-status');
                if (!response.ok) {
                    logger.error('Failed to fetch API key status:', response.statusText);
                    return;
                }
                const data = await response.json();
                if (data.status === 'error' && data.message) {
                    displayErrorMessage(data.message);
                }
            } catch (error) {
                logger.error('Error fetching API key status:', error);
                // A temporary startup failure is handled later by the WebSocket path.
            }
        }

        return {
            displayErrorMessage,
            fetchApiKeyStatus,
            hideToast,
            showToast,
        };
    }

    const api = { DEFAULT_TOAST_TIMEOUT_MS, create };
    root.UiFeedbackController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

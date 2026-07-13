(function (root) {
    'use strict';

    const DEFAULT_TOAST_TIMEOUT_MS = 10000;

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchImpl = options.fetch || root.fetch;
        const subtitleContainer = options.subtitleContainer || null;
        const toastContainer = options.toast || null;
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

        function hideToast() {
            if (!toastContainer) return;
            for (const entry of activeToasts.values()) {
                if (entry.timer) cancel(entry.timer);
                entry.node.remove();
            }
            activeToasts.clear();
            toastContainer.hidden = true;
        }

        function dismissToast(messageKey) {
            const entry = activeToasts.get(messageKey);
            if (!entry) return;
            if (entry.timer) cancel(entry.timer);
            entry.node.remove();
            activeToasts.delete(messageKey);
            toastContainer.hidden = activeToasts.size === 0;
        }

        function showToast(message, isError = false, toastOptions = {}) {
            if (!toastContainer) return;
            const messageText = String(message ?? '');
            dismissToast(messageText);

            const toast = documentRef.createElement('div');
            toast.className = 'toast';
            toast.setAttribute('role', isError ? 'alert' : 'status');
            const text = documentRef.createElement('span');
            text.textContent = messageText;
            toast.appendChild(text);
            if (toastOptions.actionLabel && typeof toastOptions.onAction === 'function') {
                const action = documentRef.createElement('button');
                action.type = 'button';
                action.className = 'toast-action';
                action.textContent = toastOptions.actionLabel;
                action.addEventListener('click', () => {
                    dismissToast(messageText);
                    toastOptions.onAction();
                });
                toast.appendChild(action);
                if (toastOptions.actionSuffix) {
                    const suffix = documentRef.createElement('span');
                    suffix.textContent = toastOptions.actionSuffix;
                    toast.appendChild(suffix);
                }
            }
            const close = documentRef.createElement('button');
            close.type = 'button';
            close.className = 'toast-close';
            close.setAttribute('aria-label', t('close'));
            close.setAttribute('title', t('close'));
            close.addEventListener('click', () => dismissToast(messageText));
            toast.appendChild(close);
            toast.classList.toggle('error', !!isError);
            toastContainer.appendChild(toast);
            const entry = { node: toast, timer: null };
            activeToasts.set(messageText, entry);
            toastContainer.hidden = false;
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

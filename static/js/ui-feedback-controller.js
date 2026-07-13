(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchImpl = options.fetch || root.fetch;
        const subtitleContainer = options.subtitleContainer || null;
        const toast = options.toast || null;
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
        let toastTimer = null;

        function hideToast() {
            if (!toast) return;
            toast.hidden = true;
            if (toastTimer) {
                cancel(toastTimer);
                toastTimer = null;
            }
        }

        function showToast(message, isError = false, toastOptions = {}) {
            if (!toast) return;
            if (toastTimer) {
                cancel(toastTimer);
                toastTimer = null;
            }
            toast.textContent = '';
            const text = documentRef.createElement('span');
            text.textContent = message;
            toast.appendChild(text);
            if (toastOptions.actionLabel && typeof toastOptions.onAction === 'function') {
                const action = documentRef.createElement('button');
                action.type = 'button';
                action.className = 'toast-action';
                action.textContent = toastOptions.actionLabel;
                action.addEventListener('click', () => {
                    toast.hidden = true;
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
            close.addEventListener('click', hideToast);
            toast.appendChild(close);
            toast.classList.toggle('error', !!isError);
            toast.hidden = false;
            if (isError) return;
            toastTimer = schedule(() => {
                toast.hidden = true;
                toastTimer = null;
            }, Number(toastOptions.timeoutMs) || 4000);
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

    const api = { create };
    root.UiFeedbackController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

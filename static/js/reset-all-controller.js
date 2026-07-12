(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const fetchImpl = options.fetch || root.fetch;
        const localStorageRef = options.localStorage || root.localStorage;
        const sessionStorageRef = options.sessionStorage || root.sessionStorage;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const showConfirm = typeof options.showConfirm === 'function'
            ? options.showConfirm
            : async () => false;

        async function handle() {
            const confirmed = await showConfirm(t('reset_all_confirm'), {
                okLabel: t('reset_all'),
                cancelLabel: t('cancel'),
                danger: true,
            });
            if (!confirmed) return;

            try {
                localStorageRef.clear();
            } catch (_) {}
            try {
                sessionStorageRef.clear();
            } catch (_) {}
            try {
                await fetchImpl('/shutdown', { method: 'POST' });
            } catch (_) {
                // The server may close the connection while completing shutdown.
            }
            try {
                windowRef.close();
            } catch (_) {}

            const doneColor = documentRef.body.classList.contains('dark-theme')
                ? '#e5e7eb'
                : '#1f2937';
            documentRef.body.innerHTML =
                '<div style="display:flex;align-items:center;justify-content:center;'
                + 'height:100vh;font-size:15px;opacity:0.7;text-align:center;padding:24px;'
                + 'color:' + doneColor + ';">'
                + t('reset_all_done') + '</div>';
        }

        return { handle };
    }

    const api = { create };
    root.ResetAllController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

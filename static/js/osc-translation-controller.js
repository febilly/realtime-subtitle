(function (root) {
    'use strict';

    function create(options = {}) {
        const fetchImpl = options.fetch || root.fetch;
        const button = options.button || null;
        const icon = options.icon || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const logger = options.console || root.console || { log() {}, error() {} };
        if (typeof fetchImpl !== 'function') {
            throw new TypeError('OscTranslationController.create requires fetch');
        }

        let enabled = false;
        let initialized = false;

        function updateButton() {
            if (!button || !icon) return false;
            button.classList.toggle('active', enabled);
            button.title = t(enabled ? 'osc_on' : 'osc_off');
            return true;
        }

        async function toggle() {
            const next = !enabled;
            try {
                const response = await fetchImpl('/osc-translation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: next }),
                });
                let data = null;
                try {
                    data = await response.json();
                } catch (parseError) {
                    logger.error('Failed to parse OSC translation toggle response:', parseError);
                }
                if (response.ok && data) {
                    enabled = !!data.enabled;
                    updateButton();
                    logger.log(`OSC translation ${enabled ? 'enabled' : 'disabled'}`);
                    return true;
                }
                logger.error('Failed to toggle OSC translation:', response.status, data?.message);
                return false;
            } catch (error) {
                logger.error('Error toggling OSC translation:', error);
                return false;
            }
        }

        async function fetchStatus() {
            if (!button) return false;
            try {
                const response = await fetchImpl('/osc-translation');
                if (!response.ok) return false;
                const data = await response.json();
                enabled = !!data.enabled;
                updateButton();
                return true;
            } catch (error) {
                logger.error('Error fetching OSC translation status:', error);
                return false;
            }
        }

        function onClick() {
            void toggle();
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (button) button.addEventListener('click', onClick);
            return true;
        }

        function destroy() {
            if (!initialized) return false;
            if (button) button.removeEventListener('click', onClick);
            initialized = false;
            return true;
        }

        return {
            destroy,
            fetchStatus,
            init,
            isEnabled: () => enabled,
            toggle,
            updateButton,
        };
    }

    const api = { create };
    root.OscTranslationController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

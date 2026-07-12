(function (root) {
    'use strict';

    const STORAGE_KEY = 'furiganaEnabled';

    function create(options = {}) {
        const storage = options.storage || root.sessionStorage;
        const button = options.button || null;
        const icon = options.icon || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const logger = options.console || root.console || { log() {}, warn() {} };
        const onChange = typeof options.onChange === 'function' ? options.onChange : () => {};
        if (!storage || typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') {
            throw new TypeError('FuriganaToggleController.create requires storage');
        }

        let enabled = false;
        let initialized = false;
        try {
            enabled = storage.getItem(STORAGE_KEY) === 'true';
        } catch (error) {
            logger.warn('Unable to access sessionStorage for furigana preference:', error);
        }

        function updateButton() {
            if (!button || !icon) return false;
            button.classList.toggle('active', enabled);
            button.title = t(enabled ? 'furigana_on' : 'furigana_off');
            return true;
        }

        function toggle() {
            enabled = !enabled;
            try {
                storage.setItem(STORAGE_KEY, enabled);
            } catch (error) {
                logger.warn('Unable to persist furigana preference:', error);
            }
            updateButton();
            onChange(enabled);
            logger.log(`Furigana ${enabled ? 'enabled' : 'disabled'}`);
            return enabled;
        }

        function onClick() {
            toggle();
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
            init,
            isEnabled: () => enabled,
            toggle,
            updateButton,
        };
    }

    const api = { STORAGE_KEY, create };
    root.FuriganaToggleController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

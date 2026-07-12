(function (root) {
    'use strict';

    const MOBILE_USER_AGENT = /Mobi|Android|iPhone|iPad|iPod/i;

    function isMobileUserAgent(userAgent) {
        return MOBILE_USER_AGENT.test(String(userAgent || ''));
    }

    function create(options = {}) {
        const settingsStore = options.settingsStore;
        const storage = options.storage || root.localStorage;
        const button = options.button || null;
        const icon = options.icon || null;
        const container = options.container || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const setControlIcon = typeof options.setControlIcon === 'function'
            ? options.setControlIcon
            : () => {};
        const logger = options.console || root.console || { log() {}, warn() {} };
        const mobile = options.isMobile !== undefined
            ? !!options.isMobile
            : isMobileUserAgent(
                options.userAgent !== undefined
                    ? options.userAgent
                    : root.navigator && root.navigator.userAgent,
            );

        if (!settingsStore || typeof settingsStore.loadBottomSafeAreaEnabled !== 'function') {
            throw new TypeError('MobileSafeAreaController.create requires settingsStore');
        }
        if (!storage || typeof storage.setItem !== 'function') {
            throw new TypeError('MobileSafeAreaController.create requires storage');
        }

        let enabled = settingsStore.loadBottomSafeAreaEnabled();
        let initialized = false;

        function updateButton() {
            if (!button || !icon) return false;
            button.style.display = mobile ? '' : 'none';
            if (!mobile) return true;
            button.classList.toggle('active', enabled);
            button.title = t(enabled ? 'bottom_safe_area_on' : 'bottom_safe_area_off');
            setControlIcon(
                icon,
                enabled ? 'arrow-up-from-line' : 'arrow-down-to-line',
            );
            return true;
        }

        function apply() {
            if (!container) return false;
            container.classList.toggle('mobile-bottom-safe-area', mobile && enabled);
            return mobile && enabled;
        }

        function toggle() {
            if (!mobile) return false;
            enabled = !enabled;
            try {
                storage.setItem('bottomSafeAreaEnabled', enabled);
            } catch (error) {
                logger.warn('Unable to persist bottom safe area preference:', error);
            }
            apply();
            updateButton();
            logger.log(`Mobile bottom safe area ${enabled ? 'enabled' : 'disabled'}`);
            return true;
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
            apply,
            destroy,
            init,
            isEnabled: () => enabled,
            isMobile: () => mobile,
            toggle,
            updateButton,
        };
    }

    const api = { MOBILE_USER_AGENT, create, isMobileUserAgent };
    root.MobileSafeAreaController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

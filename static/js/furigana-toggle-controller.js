(function (root) {
    'use strict';

    const STORAGE_KEY = 'furiganaEnabled';
    const LEARNING_LANGUAGES = Object.freeze([
        Object.freeze({ code: 'ja', labelKey: 'learning_ja_furigana' }),
        Object.freeze({ code: 'zh', labelKey: 'learning_zh_pinyin' }),
        Object.freeze({ code: 'ko', labelKey: 'learning_ko_romanization' }),
    ]);

    function getListSeparator(uiLang) {
        const base = String(uiLang || 'en').trim().toLowerCase().split(/[-_]/)[0];
        return (base === 'zh' || base === 'ja') ? '、' : ', ';
    }

    function create(options = {}) {
        const storage = options.storage || root.sessionStorage;
        const button = options.button || null;
        const icon = options.icon || null;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getUiLanguage = typeof options.getUiLanguage === 'function'
            ? options.getUiLanguage
            : () => (root.I18N && root.I18N.lang) || 'en';
        const getEnabledLanguages = typeof options.getEnabledLanguages === 'function'
            ? options.getEnabledLanguages
            : null;
        const logger = options.console || root.console || { log() {}, warn() {} };
        const onChange = typeof options.onChange === 'function' ? options.onChange : () => {};
        if (!storage || typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') {
            throw new TypeError('FuriganaToggleController.create requires storage');
        }

        let userEnabled = false;
        let packEnabled = true;
        let initialized = false;
        try {
            userEnabled = storage.getItem(STORAGE_KEY) === 'true';
        } catch (error) {
            logger.warn('Unable to access sessionStorage for furigana preference:', error);
        }

        function isEnabled() {
            return packEnabled && userEnabled;
        }

        function formatTooltip(active) {
            if (getEnabledLanguages) {
                const langMap = getEnabledLanguages();
                if (langMap && typeof langMap === 'object') {
                    const enabledLabels = LEARNING_LANGUAGES
                        .filter((entry) => !!langMap[entry.code])
                        .map((entry) => t(entry.labelKey));
                    if (enabledLabels.length === 0) {
                        return t(active ? 'furigana_no_langs_on' : 'furigana_no_langs_off');
                    }
                    const languages = enabledLabels.join(getListSeparator(getUiLanguage()));
                    return t(active ? 'furigana_on_with_langs' : 'furigana_off_with_langs', { languages });
                }
            }
            return t(active ? 'furigana_on' : 'furigana_off');
        }

        function updateButton() {
            if (!button || !icon) return false;
            const active = isEnabled();
            button.classList.toggle('active', active);
            button.title = formatTooltip(active);
            return true;
        }

        function toggle() {
            if (!packEnabled) return false;
            userEnabled = !userEnabled;
            try {
                storage.setItem(STORAGE_KEY, userEnabled);
            } catch (error) {
                logger.warn('Unable to persist furigana preference:', error);
            }
            const active = isEnabled();
            updateButton();
            onChange(active);
            logger.log(`Furigana ${active ? 'enabled' : 'disabled'}`);
            return active;
        }

        function setPackEnabled(nextPackEnabled) {
            const next = !!nextPackEnabled;
            if (packEnabled === next) {
                updateButton();
                return isEnabled();
            }
            packEnabled = next;
            const active = isEnabled();
            updateButton();
            onChange(active);
            return active;
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
            isEnabled,
            setPackEnabled,
            toggle,
            updateButton,
        };
    }

    const api = { STORAGE_KEY, create };
    root.FuriganaToggleController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

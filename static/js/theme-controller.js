(function (root) {
    'use strict';

    function create(options = {}) {
        const settingsUi = options.settingsUi;
        const fetchImpl = options.fetch || root.fetch;
        const storage = options.storage || root.localStorage;
        const toggle = options.toggle;
        const themeIcon = options.themeIcon || null;
        const setControlIcon = typeof options.setControlIcon === 'function'
            ? options.setControlIcon
            : () => {};

        if (
            !settingsUi
            || typeof settingsUi.getAvailableThemes !== 'function'
            || typeof settingsUi.applyTheme !== 'function'
        ) {
            throw new TypeError('ThemeController.create requires settingsUi');
        }
        if (typeof fetchImpl !== 'function') {
            throw new TypeError('ThemeController.create requires fetch');
        }
        if (!storage || typeof storage.getItem !== 'function') {
            throw new TypeError('ThemeController.create requires storage');
        }
        if (!toggle || typeof toggle.addEventListener !== 'function') {
            throw new TypeError('ThemeController.create requires a theme toggle');
        }

        let currentTheme = 'dark';
        let lastWindowOnTopState = null;
        let chromaEnabled = false;
        let initialized = false;

        function getAvailableThemes() {
            return settingsUi.getAvailableThemes(chromaEnabled);
        }

        async function syncWindowOnTop(theme) {
            const shouldOnTop = theme !== 'chroma';
            if (lastWindowOnTopState === shouldOnTop) return false;
            lastWindowOnTopState = shouldOnTop;
            try {
                await fetchImpl('/window-on-top', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ on_top: shouldOnTop }),
                });
            } catch (error) {
                // Browser mode and older backends may not expose this endpoint.
            }
            return true;
        }

        function apply(theme) {
            currentTheme = settingsUi.applyTheme(theme, {
                enableChromaTheme: chromaEnabled,
                themeIcon,
                setControlIcon,
            });
            if (chromaEnabled) void syncWindowOnTop(currentTheme);
            return currentTheme;
        }

        function cycle() {
            const available = getAvailableThemes();
            const currentIndex = available.indexOf(currentTheme);
            const actualIndex = currentIndex >= 0 ? currentIndex : 0;
            return apply(available[(actualIndex + 1) % available.length]);
        }

        function setChromaEnabled(enabled) {
            const wasEnabled = chromaEnabled;
            chromaEnabled = !!enabled;
            if (chromaEnabled && !wasEnabled) {
                const savedTheme = storage.getItem('theme');
                if (savedTheme === 'chroma' && currentTheme !== 'chroma') {
                    apply('chroma');
                }
                void syncWindowOnTop(currentTheme);
            } else if (!chromaEnabled && wasEnabled) {
                if (currentTheme === 'chroma') apply('dark');
            } else if (chromaEnabled) {
                void syncWindowOnTop(currentTheme);
            }
            return currentTheme;
        }

        function onToggle() {
            cycle();
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            apply(storage.getItem('theme'));
            toggle.addEventListener('click', onToggle);
            return true;
        }

        function destroy() {
            if (!initialized) return false;
            toggle.removeEventListener('click', onToggle);
            initialized = false;
            return true;
        }

        function getDebugState() {
            return { currentTheme, lastWindowOnTopState, chromaEnabled, initialized };
        }

        return {
            apply,
            cycle,
            destroy,
            getAvailableThemes,
            getCurrentTheme: () => currentTheme,
            getDebugState,
            init,
            setChromaEnabled,
            syncWindowOnTop,
        };
    }

    const api = { create };
    root.ThemeController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

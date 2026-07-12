(function (root) {
    'use strict';

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const elements = options.elements || {};
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const actions = options.actions || {};
        const call = (name) => {
            if (typeof actions[name] === 'function') actions[name]();
        };

        function applyStaticText() {
            if (documentRef && documentRef.documentElement) {
                try {
                    documentRef.documentElement.lang = (
                        windowRef.I18N && windowRef.I18N.lang
                    ) ? windowRef.I18N.lang : 'en';
                } catch (error) {
                    // Preserve the startup fallback when an injected i18n object is unavailable.
                }
            }

            if (elements.themeToggle) elements.themeToggle.title = t('theme_toggle');
            if (elements.restartButton) elements.restartButton.title = t('restart');
            if (elements.translationLangButton) {
                elements.translationLangButton.title = t('translation_language');
            }
            if (elements.pauseButton) call('updatePauseButtonUi');
            if (elements.overlayButton) call('updateOverlayButton');
            if (elements.settingsButton) elements.settingsButton.title = t('settings');

            if (elements.subtitleContainer) {
                const emptyNode = elements.subtitleContainer.querySelector('.empty-state');
                if (emptyNode) emptyNode.textContent = t('empty_state');
            }
        }

        function applyManualControlPolicy() {
            const state = getState() || {};
            const locked = !!state.lockManualControls;
            for (const control of [
                elements.restartButton,
                elements.pauseButton,
                elements.audioSourceButton,
                elements.oscTranslationButton,
                elements.translationLangButton,
            ]) {
                if (control) control.style.display = locked ? 'none' : '';
            }
            if (elements.segmentModeButton) {
                elements.segmentModeButton.style.display = (
                    locked || !state.segmentModeSupported
                ) ? 'none' : '';
            }
            if (locked) updateState({ autoRestartEnabled: true });
            call('updateAutoRestartButton');
        }

        function syncIpcStatus(connected) {
            const button = elements.ipcStatusButton;
            if (!button) return false;
            if (connected) {
                button.style.display = 'flex';
                button.classList.add('ipc-connected');
            } else {
                button.style.display = 'none';
                button.classList.remove('ipc-connected');
            }
            return true;
        }

        return {
            applyManualControlPolicy,
            applyStaticText,
            syncIpcStatus,
        };
    }

    const api = { create };
    root.AppShellController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

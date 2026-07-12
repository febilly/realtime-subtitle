(function (root) {
    'use strict';

    const FRAME_TYPES = Object.freeze([
        'subtitle_font_preference',
        'overlay_visibility',
        'ipc_status',
        'error',
        'llm_cost',
        'translation_mode_fallback',
        'segment_mode_changed',
        'speaker_labels_changed',
    ]);

    function create(options = {}) {
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const actions = options.actions || {};
        const call = (name, ...args) => {
            if (typeof actions[name] !== 'function') return undefined;
            return actions[name](...args);
        };

        function handle(frame = {}) {
            if (!FRAME_TYPES.includes(frame.type)) return false;

            if (frame.type === 'subtitle_font_preference') {
                call('applyBundledCjkFontPreference', !!frame.use_bundled_cjk_fonts, {
                    persist: true,
                });
                return true;
            }
            if (frame.type === 'overlay_visibility') {
                call('syncOverlayState', frame.visible);
                return true;
            }
            if (frame.type === 'ipc_status') {
                call('syncIpcStatus', frame.connected);
                return true;
            }
            if (frame.type === 'error') {
                call('displayErrorMessage', frame.message);
                if (frame.code === 'api_key' && !(getState() || {}).lockManualControls) {
                    call('openSettings', { forced: true });
                }
                return true;
            }
            if (frame.type === 'llm_cost') {
                call('addLlmCost', frame.credits);
                return true;
            }
            if (frame.type === 'translation_mode_fallback') {
                call('setTranslationUiMode', 'fast', { restartIfNeeded: false });
                call('renderTranslationModePicker');
                call('showToast', t('translation_mode_fallback_toast'), true);
                if (frame.needs_restart) call('restartRecognition', { auto: true });
                return true;
            }
            if (frame.type === 'segment_mode_changed') {
                call('handleSegmentModeChanged', frame);
                return true;
            }

            call('handleSpeakerLabelsChanged', frame);
            return true;
        }

        return { handle };
    }

    const api = { FRAME_TYPES, create };
    root.RuntimeFrameController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

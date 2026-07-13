(function (root) {
    'use strict';

    const MESSAGE_KEYS = Object.freeze({
        'Manual restart is disabled by server config': 'backend_manual_restart_disabled',
        'Pause is disabled by server config': 'backend_pause_disabled',
        'Resume is disabled by server config': 'backend_resume_disabled',
        'Audio source switching is disabled by server config': 'backend_audio_source_disabled',
        'Microphone device switching is disabled by server config': 'backend_microphone_device_disabled',
        'Output device switching is disabled by server config': 'backend_output_device_disabled',
        'OSC translation toggle is disabled by server config': 'backend_osc_disabled',
        'Overlay control is disabled by server config': 'backend_overlay_disabled',
        'Segment mode switching is disabled': 'backend_segment_mode_disabled',
        'Speaker label switching is disabled by server config': 'backend_speaker_labels_disabled',
        'Auto sleep switching is disabled by server config': 'backend_auto_sleep_disabled',
        'LLM refine toggle is disabled by server config': 'backend_llm_refine_disabled',
        'Furigana feature not available (pykakasi not installed)': 'backend_furigana_unavailable',
    });

    function createLocalizer(options = {}) {
        const t = typeof options.t === 'function' ? options.t : (key) => key;

        return function localizeBackendMessage(message) {
            if (message === null || message === undefined) return message;
            const raw = String(message).trim();
            if (!raw) return raw;
            const key = MESSAGE_KEYS[raw];
            if (key) return t(key);

            // Lightweight heuristics for similar messages without changing backend.
            if (/disabled by server config/i.test(raw)) return raw;
            return raw;
        };
    }

    const api = { MESSAGE_KEYS, createLocalizer };
    root.BackendMessage = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

const BackendMessage = require('../../static/js/backend-message');

const EXPECTED_MAPPINGS = [
    ['Manual restart is disabled by server config', 'backend_manual_restart_disabled'],
    ['Pause is disabled by server config', 'backend_pause_disabled'],
    ['Resume is disabled by server config', 'backend_resume_disabled'],
    ['Audio source switching is disabled by server config', 'backend_audio_source_disabled'],
    ['Microphone device switching is disabled by server config', 'backend_microphone_device_disabled'],
    ['Output device switching is disabled by server config', 'backend_output_device_disabled'],
    ['OSC translation toggle is disabled by server config', 'backend_osc_disabled'],
    ['Overlay control is disabled by server config', 'backend_overlay_disabled'],
    ['Segment mode switching is disabled', 'backend_segment_mode_disabled'],
    ['Speaker label switching is disabled by server config', 'backend_speaker_labels_disabled'],
    ['Auto sleep switching is disabled by server config', 'backend_auto_sleep_disabled'],
    ['LLM refine toggle is disabled by server config', 'backend_llm_refine_disabled'],
    ['Furigana feature not available (pykakasi not installed)', 'backend_furigana_unavailable'],
];

describe('BackendMessage', () => {
    it('exports the complete backend message mapping', () => {
        expect(Object.entries(BackendMessage.MESSAGE_KEYS)).toEqual(EXPECTED_MAPPINGS);
        expect(Object.isFrozen(BackendMessage.MESSAGE_KEYS)).toBe(true);
    });

    it.each(EXPECTED_MAPPINGS)('localizes %s through exactly %s', (message, key) => {
        const t = vi.fn((receivedKey) => `translated:${receivedKey}`);
        const localize = BackendMessage.createLocalizer({ t });

        expect(localize(message)).toBe(`translated:${key}`);
        expect(t).toHaveBeenCalledOnce();
        expect(t).toHaveBeenCalledWith(key);
    });

    it.each([
        [null, null],
        [undefined, undefined],
        ['', ''],
        [' \t\r\n ', ''],
    ])('preserves empty input semantics for %p', (message, expected) => {
        const t = vi.fn();
        const localize = BackendMessage.createLocalizer({ t });

        expect(localize(message)).toBe(expected);
        expect(t).not.toHaveBeenCalled();
    });

    it.each([
        [123, '123'],
        [false, 'false'],
        [{ toString: () => ' custom backend message ' }, 'custom backend message'],
    ])('stringifies and trims non-string input %p', (message, expected) => {
        const t = vi.fn();
        const localize = BackendMessage.createLocalizer({ t });

        expect(localize(message)).toBe(expected);
        expect(t).not.toHaveBeenCalled();
    });

    it.each([
        'An unknown backend failure',
        'manual restart is disabled by server config',
        'Camera selection is disabled by server config',
    ])('returns an unmapped message unchanged without translating it: %s', (message) => {
        const t = vi.fn();
        const localize = BackendMessage.createLocalizer({ t });

        expect(localize(message)).toBe(message);
        expect(t).not.toHaveBeenCalled();
    });

    it('falls back to returning message keys when no valid translator is supplied', () => {
        const [message, key] = EXPECTED_MAPPINGS[0];

        expect(BackendMessage.createLocalizer()(message)).toBe(key);
        expect(BackendMessage.createLocalizer({ t: 'not a function' })(message)).toBe(key);
    });
});

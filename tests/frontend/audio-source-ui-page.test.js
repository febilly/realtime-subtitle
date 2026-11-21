const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

describe('full-page audio window startup recovery', () => {
    it.each([false, true])(
        'keeps startup recovery silent and flashes only while waiting (%j)', async (missing) => {
            const page = await createPageHarness({
                localStorage: {
                    'subtitleServer.v1': JSON.stringify({ mode: 'direct', modeChosen: true }),
                },
                fetch: async (url, options = {}) => {
                    if (url === '/audio-source') return { ok: true, json: async () => ({
                        source: 'system',
                        capture_error: missing ? 'Selected audio window is unavailable' : '',
                        settings: {
                            desktop_enabled: true, microphone_enabled: false, desktop_mode: 'window',
                            output_device_id: '', microphone_device_id: '', remember: true,
                            window: { pid: 42, hwnd: '84', created: '100', executable: 'C:\\player.exe', title: 'Player' },
                        },
                    }) };
                    if (url === '/audio-windows') return { ok: true, json: async () => ({ available: true, windows: [] }) };
                    return defaultFetchResponse(url, options);
                },
            });
            try {
                expect(page.document.querySelector('.audio-source-popover')).toBeNull();
                expect(page.document.getElementById('audioSourceButton').classList.contains('audio-window-waiting')).toBe(missing);
                expect(page.fetchCalls.filter(([url, init]) => url === '/audio-source' && init?.method === 'POST')).toHaveLength(0);
            } finally {
                page.close();
            }
        },
    );

    it('wires VRChat mic mute follow option with tooltip and updates when VRChat pack toggles', async () => {
        const { MESSAGES } = require('../../static/i18n.js');
        expect(MESSAGES.zh.audio_follow_vrchat_mute).toBe('跟随VRChat中麦克风静音状态');
        expect(MESSAGES.zh.audio_follow_vrchat_mute_hint).toBe('请在VRChat中使用切换麦。请勿使用按键麦。');

        const page = await createPageHarness({
            localStorage: {
                'subtitleServer.v1': JSON.stringify({ mode: 'direct', modeChosen: true }),
                'featurePacks.v1': JSON.stringify({
                    version: 1,
                    confirmed: true,
                    isUpgrade: false,
                    enabled: { vrchat: true, learning: false, obs: false, transcript: false },
                }),
            },
            fetch: async (url, options = {}) => {
                if (url === '/audio-source') return { ok: true, json: async () => ({
                    source: 'mix',
                    settings: {
                        desktop_enabled: true, microphone_enabled: true, desktop_mode: 'output',
                        output_device_id: '', microphone_device_id: '', follow_vrchat_mic_mute: false,
                        window: null, remember: false,
                    },
                }) };
                if (url === '/audio-windows') return { ok: true, json: async () => ({ available: true, windows: [] }) };
                return defaultFetchResponse(url, options);
            },
        });
        try {
            page.document.getElementById('audioSourceButton').click();
            await page.flush(6);
            const label = page.document.getElementById('audio-follow_vrchat_mic_mute-label');
            const toggle = page.document.getElementById('audio-follow_vrchat_mic_mute');
            const row = toggle.closest('.audio-follow-vrchat-mute-row');
            expect(label.textContent).toBe('Follow VRChat microphone mute state');
            expect(label.classList.contains('audio-tooltip-underline')).toBe(true);
            expect(toggle.getAttribute('aria-checked')).toBe('false');
            expect(row.getAttribute('data-custom-title')).toBe('Please use toggle-to-mute in VRChat. Do not use push-to-talk.');

            // Disabling VRChat pack via WebSocket updates the open popover immediately
            page.sockets[0].onmessage({ data: JSON.stringify({
                type: 'feature_packs',
                packs: { vrchat: false, learning: false, obs: false, transcript: false },
            }) });
            await page.flush(2);
            expect(page.document.getElementById('audio-follow_vrchat_mic_mute')).toBeNull();
        } finally {
            page.close();
        }
    });
});

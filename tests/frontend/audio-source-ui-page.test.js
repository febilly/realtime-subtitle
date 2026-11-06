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
});

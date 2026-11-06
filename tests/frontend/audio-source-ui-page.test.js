const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

describe('full-page audio window startup recovery', () => {
    it.each([null, { pid: 42, hwnd: '84', created: '100', executable: 'C:\\player.exe', title: 'Player' }])(
        'prompts only when startup restore cleared the target (%j)', async (window) => {
            const page = await createPageHarness({
                localStorage: {
                    'subtitleServer.v1': JSON.stringify({ mode: 'direct', modeChosen: true }),
                },
                fetch: async (url, options = {}) => {
                    if (url === '/audio-source') return { ok: true, json: async () => ({
                        source: 'system',
                        settings: {
                            desktop_enabled: true, microphone_enabled: false, desktop_mode: 'window',
                            output_device_id: '', microphone_device_id: '', remember: true, window,
                        },
                    }) };
                    if (url === '/audio-windows') return { ok: true, json: async () => ({ available: true, windows: [] }) };
                    return defaultFetchResponse(url, options);
                },
            });
            try {
                const panel = page.document.querySelector('.audio-source-popover');
                if (window) {
                    expect(panel).toBeNull();
                } else {
                    expect(panel).not.toBeNull();
                    expect(panel.querySelector('.audio-window-placeholder .lang-picker-label').textContent).toBe('Select a window');
                    expect(panel.querySelector('[role="alert"]').hidden).toBe(false);
                }
                expect(page.fetchCalls.filter(([url, init]) => url === '/audio-source' && init?.method === 'POST')).toHaveLength(0);
            } finally {
                page.close();
            }
        },
    );
});

const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

function json(data) {
    return { ok: true, status: 200, json: async () => data };
}

function missingModels() {
    return {
        status: 'ok',
        models: {
            asr: {
                'qwen3-asr': {
                    ready: false, estimated_bytes: 1.5e9, estimated_vram_bytes: 2e9,
                },
            },
            translation: {
                hymt2: {
                    ready: false, model_ready: false,
                    estimated_bytes: 1.1e9, estimated_vram_bytes: 1.5e9,
                },
            },
        },
        devices: [],
        selected_device: null,
    };
}

it('returns to Soniox paused when startup local models are not downloaded', async () => {
    let provider = 'local';
    const setupBodies = [];
    const fetch = async (url, options = {}) => {
        const path = new URL(String(url), 'http://localhost/').pathname;
        if (path === '/local-inference/status') return json(missingModels());
        if (path === '/setup') {
            const body = options.body ? JSON.parse(options.body) : {};
            setupBodies.push(body);
            provider = body.provider || provider;
            return json({
                status: 'ok', provider, boot_id: 'boot-local-fallback',
                setup_required: false, mode: body.mode || 'direct',
            });
        }
        return defaultFetchResponse(url, {
            translationUiMode: 'hybrid',
            uiConfig: { provider, relay_available: false, lock_manual_controls: false },
        });
    };

    const page = await createPageHarness({
        initialUiConfig: { provider: 'local' },
        uiConfig: { provider: 'local' },
        localStorage: {
            'providerSettings.v1': JSON.stringify({
                providerOverride: 'local',
                localConfig: { backend: 'local' },
                keys: {},
            }),
        },
        fetch,
    });

    try {
        await vi.waitFor(() => {
            expect(page.document.querySelector('.local-model-dialog')).not.toBeNull();
        });
        const dialogButtons = [...page.document.querySelectorAll('.local-model-dialog-actions button')];
        expect(dialogButtons).toHaveLength(2);
        dialogButtons[0].click();

        await vi.waitFor(() => {
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        });
        const selected = page.document.querySelector('input[name="provider"]:checked');
        expect(selected && selected.value).toBe('soniox');
        expect(setupBodies.at(-1)).toMatchObject({
            provider: 'soniox', mode: 'direct', start_paused: true,
        });
        expect(page.fetchCalls.some(([url]) => String(url).endsWith('/resume'))).toBe(false);
    } finally {
        page.close();
    }
});

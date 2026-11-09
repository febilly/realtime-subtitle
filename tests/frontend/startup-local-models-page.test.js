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

it.each([
    { mode: 'off', buttonCount: 2, needsTranslationModel: false },
    { mode: 'fast', buttonCount: 3, needsTranslationModel: true },
])('returns to Soniox hybrid with settings open when startup local models are declined ($mode)', async ({
    mode, buttonCount, needsTranslationModel,
}) => {
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
            translationUiMode: mode,
            uiConfig: { provider, relay_available: false, lock_manual_controls: false },
        });
    };

    const page = await createPageHarness({
        initialUiConfig: { provider: 'local' },
        uiConfig: { provider: 'local' },
        translationUiMode: mode,
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
        const firstDialogText = page.document.querySelector('.local-model-dialog').textContent;
        expect(firstDialogText).toContain('Qwen3-ASR');
        if (needsTranslationModel) {
            expect(firstDialogText).toContain('Hy-MT2');
        } else {
            expect(firstDialogText).not.toContain('Hy-MT2');
        }
        const dialogButtons = [...page.document.querySelectorAll('.local-model-dialog-actions button')];
        expect(dialogButtons).toHaveLength(buttonCount);
        dialogButtons[0].click();

        await vi.waitFor(() => {
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
        });
        const selected = page.document.querySelector('input[name="provider"]:checked');
        expect(selected && selected.value).toBe('soniox');
        expect(setupBodies.at(-1)).toMatchObject({
            provider: 'soniox', mode: 'direct', start_paused: true,
        });
        const translationModeCall = page.fetchCalls.find(([url, options]) => (
            new URL(String(url), 'http://localhost/').pathname === '/translation-mode'
            && options && options.body
            && JSON.parse(options.body).mode === 'hybrid'
        ));
        expect(translationModeCall).toBeTruthy();
        expect(JSON.parse(translationModeCall[1].body)).toMatchObject({
            mode: 'hybrid', provider: 'soniox',
        });
        expect(page.window.localStorage.getItem('translationUiMode')).toBe('hybrid');
        expect(page.fetchCalls.some(([url]) => String(url).endsWith('/resume'))).toBe(false);
    } finally {
        page.close();
    }
});

it('downloads ASR only and turns translation off when restoring local mode', async () => {
    let provider = 'local';
    const setupBodies = [];
    const fetch = async (url, options = {}) => {
        const path = new URL(String(url), 'http://localhost/').pathname;
        if (path === '/local-inference/status') return json(missingModels());
        if (path === '/local-inference/download') return json({ status: 'ok' });
        if (path === '/setup') {
            const body = options.body ? JSON.parse(options.body) : {};
            setupBodies.push(body);
            provider = body.provider || provider;
            return json({
                status: 'ok', provider, boot_id: 'boot-local-asr-only',
                setup_required: false, mode: body.mode || 'direct',
            });
        }
        return defaultFetchResponse(url, {
            translationUiMode: 'fast',
            uiConfig: { provider, relay_available: false, lock_manual_controls: false },
        });
    };

    const page = await createPageHarness({
        initialUiConfig: { provider: 'local' },
        uiConfig: { provider: 'local' },
        translationUiMode: 'fast',
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
        expect(dialogButtons).toHaveLength(3);
        dialogButtons[1].click();

        await vi.waitFor(() => {
            const call = page.fetchCalls.find(([url, options]) => (
                new URL(String(url), 'http://localhost/').pathname === '/translation-mode'
                && options && options.body
                && JSON.parse(options.body).mode === 'off'
            ));
            expect(call).toBeTruthy();
        });
        expect(setupBodies).toHaveLength(1);
        expect(setupBodies[0]).toMatchObject({
            provider: 'local', mode: 'direct', start_paused: true,
        });
        expect(page.window.localStorage.getItem('translationUiMode')).toBe('off');
        expect(page.fetchCalls.some(([url]) => String(url).endsWith('/resume'))).toBe(false);
    } finally {
        page.close();
    }
});

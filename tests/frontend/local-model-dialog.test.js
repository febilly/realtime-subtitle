const { JSDOM } = require('jsdom');
const LocalModelDialog = require('../../static/js/local-model-dialog');

function response(data, { ok = true } = {}) {
    return { ok, json: vi.fn().mockResolvedValue(data) };
}

const GIB = 1024 ** 3;

function status({
    asrReady = false, mtReady = false, diskFree = 500e9, totalGib = 24, freeGib = 6.4,
} = {}) {
    const gpu = { index: 0, description: 'RTX 3080', total_bytes: totalGib * GIB, free_bytes: freeGib * GIB };
    return {
        status: 'ok',
        models: {
            models_dir: 'D:/models',
            disk_free_bytes: diskFree,
            asr: { 'qwen3-asr': { ready: asrReady, estimated_bytes: 1.5e9, estimated_vram_bytes: 2e9 } },
            translation: { hymt2: { model_ready: mtReady, estimated_bytes: 1.1e9, estimated_vram_bytes: 1.5e9 } },
        },
        devices: [gpu],
        selected_device: gpu,
    };
}

async function openedText(page, start) {
    const pending = start();
    await vi.waitFor(() => expect(page.dialog.isOpen()).toBe(true));
    const text = page.document.querySelector('.local-model-dialog').textContent;
    const warnings = [...page.document.querySelectorAll('.local-model-dialog-warning')]
        .map((node) => node.textContent.split('|')[0]);
    buttons(page.document)[0].click();
    await pending;
    page.dom.window.close();
    return { text, warnings };
}

function setup(routes) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
    const document = dom.window.document;
    const calls = [];
    const fetch = vi.fn(async (url, init = {}) => {
        const body = init.body ? JSON.parse(init.body) : null;
        calls.push({ url, body });
        const route = routes[url];
        const result = typeof route === 'function' ? await route(body) : route;
        return result || response({});
    });
    const t = (key, vars = {}) => [key, ...Object.entries(vars).map(
        ([name, value]) => `${name}=${value}`,
    )].join('|');
    const dialog = LocalModelDialog.create({
        document, fetch, t, setTimeout: () => 1, clearTimeout: () => {},
    });
    return { calls, dialog, document, dom };
}

function buttons(document) {
    return [...document.querySelectorAll('.local-model-dialog-actions button')];
}

async function clickButton(document, prefix) {
    await vi.waitFor(() => {
        expect(buttons(document).some((button) => button.textContent.startsWith(prefix))).toBe(true);
    });
    buttons(document).find((button) => button.textContent.startsWith(prefix)).click();
}

describe('LocalModelDialog', () => {
    it('decides when the local provider runs Hy-MT2', () => {
        const needs = LocalModelDialog.needsMachineTranslation;
        expect(needs({ uiTranslationMode: 'none', translationUiMode: 'fast', llmAvailable: true })).toBe(false);
        expect(needs({ uiTranslationMode: 'one_way', translationUiMode: 'accurate', llmAvailable: true })).toBe(false);
        expect(needs({ uiTranslationMode: 'one_way', translationUiMode: 'accurate', llmAvailable: false })).toBe(true);
        expect(needs({ uiTranslationMode: 'one_way', translationUiMode: 'hybrid', llmAvailable: true })).toBe(true);
    });

    it('switches without asking when both models are downloaded', async () => {
        const page = setup({ '/local-inference/status': response(status({ asrReady: true, mtReady: true })) });
        const result = await page.dialog.confirmLocalProvider({ previousProvider: 'Soniox', llmAvailable: true });
        expect(result).toEqual({ ok: true, skippedTranslation: false });
        expect(page.dialog.isOpen()).toBe(false);
        page.dom.window.close();
    });

    it('lists sizes and VRAM, and cancel keeps the previous provider', async () => {
        const page = setup({ '/local-inference/status': response(status()) });
        const pending = page.dialog.confirmLocalProvider({ previousProvider: 'Soniox', llmAvailable: true });
        await vi.waitFor(() => expect(page.dialog.isOpen()).toBe(true));
        const text = page.document.querySelector('.local-model-dialog').textContent;
        expect(text).toContain('download=1.5 GB|vram=2.0 GB');
        expect(text).toContain('download=1.1 GB|vram=1.5 GB');
        expect(text).toContain('local_dialog_gpu_free|gpu=RTX 3080|free=6.4 GB');
        expect(text).toContain('local_dialog_asr_only_llm');
        expect(buttons(page.document).map((button) => button.textContent)).toEqual([
            'local_dialog_cancel_keep|provider=Soniox',
            'local_dialog_download_asr|size=1.5 GB',
            'local_dialog_download_both|size=2.6 GB',
        ]);
        await clickButton(page.document, 'local_dialog_cancel_keep');
        await expect(pending).resolves.toEqual({ ok: false });
        expect(page.calls.some((call) => call.url === '/local-inference/download')).toBe(false);
        page.dom.window.close();
    });

    it('downloads both models in order before switching', async () => {
        const page = setup({
            '/local-inference/status': response(status()),
            '/local-inference/download': response({ status: 'ok' }),
        });
        const pending = page.dialog.confirmLocalProvider({ previousProvider: 'Soniox', llmAvailable: true });
        await clickButton(page.document, 'local_dialog_download_both');
        await expect(pending).resolves.toEqual({ ok: true, skippedTranslation: false });
        expect(page.calls.filter((call) => call.url === '/local-inference/download').map((call) => call.body))
            .toEqual([{ component: 'qwen3-asr' }, { component: 'hymt2' }]);
        expect(page.dialog.isOpen()).toBe(false);
        page.dom.window.close();
    });

    it('reports an ASR-only choice so the caller can pick the translation mode', async () => {
        const page = setup({
            '/local-inference/status': response(status()),
            '/local-inference/download': response({ status: 'ok' }),
        });
        const pending = page.dialog.confirmLocalProvider({ previousProvider: 'Soniox', llmAvailable: false });
        await vi.waitFor(() => expect(page.dialog.isOpen()).toBe(true));
        expect(page.document.querySelector('.local-model-dialog').textContent)
            .toContain('local_dialog_asr_only_no_llm');
        await clickButton(page.document, 'local_dialog_download_asr');
        await expect(pending).resolves.toEqual({ ok: true, skippedTranslation: true });
        page.dom.window.close();
    });

    it('stays on the previous provider when the download is cancelled', async () => {
        let finishDownload;
        const page = setup({
            '/local-inference/status': response(status({ asrReady: false, mtReady: true })),
            '/local-inference/download': () => new Promise((resolve) => { finishDownload = resolve; }),
            '/local-inference/download/cancel': () => {
                finishDownload(response({ code: 'download_cancelled' }, { ok: false }));
                return response({ cancelled: true });
            },
        });
        const pending = page.dialog.confirmLocalProvider({ previousProvider: 'Gemini', llmAvailable: true });
        await clickButton(page.document, 'local_dialog_download_mt');
        await clickButton(page.document, 'local_dialog_cancel_download');
        await expect(pending).resolves.toEqual({ ok: false });
        page.dom.window.close();
    });

    it('offers retry after a failed download and fails closed on close', async () => {
        const page = setup({
            '/local-inference/status': response(status({ asrReady: true, mtReady: false })),
            '/local-inference/download': response({ message: 'all mirrors down' }, { ok: false }),
        });
        const pending = page.dialog.confirmTranslationModel({ modeLabel: 'Fast', previousModeLabel: 'Accurate' });
        await vi.waitFor(() => expect(buttons(page.document).map((button) => button.textContent)).toEqual([
            'local_dialog_cancel_mode|mode=Accurate',
            'local_dialog_download_mt|size=1.1 GB',
        ]));
        await clickButton(page.document, 'local_dialog_download_mt');
        await vi.waitFor(() => expect(page.document.querySelector('.local-model-dialog-error').textContent)
            .toBe('local_dialog_failed|error=all mirrors down'));
        await clickButton(page.document, 'local_dialog_close');
        await expect(pending).resolves.toBe(false);
        page.dom.window.close();
    });

    it('shows no resource warning when disk and VRAM are enough', async () => {
        const page = setup({ '/local-inference/status': response(status()) });
        const { warnings } = await openedText(page, () => page.dialog.confirmLocalProvider({ previousProvider: 'Soniox' }));
        expect(warnings).toEqual([]);
    });

    it('warns when the download drive lacks space for what is missing', async () => {
        const page = setup({ '/local-inference/status': response(status({ diskFree: 2e9 })) });
        const { text, warnings } = await openedText(
            page, () => page.dialog.confirmLocalProvider({ previousProvider: 'Soniox' }),
        );
        expect(warnings).toEqual(['local_dialog_disk_low']);
        expect(text).toContain('local_dialog_disk_low|need=2.6 GB|free=2.0 GB|dir=D:/models');
        // Hy-MT2 alone (1.1 GB) fits in 2 GB.
        const mtOnly = setup({ '/local-inference/status': response(status({ asrReady: true, diskFree: 2e9 })) });
        const second = await openedText(mtOnly, () => mtOnly.dialog.confirmTranslationModel({ modeLabel: 'Fast' }));
        expect(second.warnings).toEqual([]);
    });

    it('warns about low VRAM below 7.8 GB total or 3.8 GB free', async () => {
        for (const [gpu, expected] of [
            [{ totalGib: 6, freeGib: 5 }, ['local_dialog_vram_low']],
            [{ totalGib: 24, freeGib: 3.5 }, ['local_dialog_vram_low']],
            // A nominal 8 GB card that reports a little under 8 GiB.
            [{ totalGib: 7.9, freeGib: 6 }, ['local_dialog_vram_no_game']],
            [{ totalGib: 12, freeGib: 10 }, ['local_dialog_vram_no_game']],
            [{ totalGib: 15.9, freeGib: 12 }, []],
        ]) {
            const page = setup({ '/local-inference/status': response(status({ asrReady: true, ...gpu })) });
            const { warnings } = await openedText(page, () => page.dialog.confirmTranslationModel({ modeLabel: 'Fast' }));
            expect(warnings).toEqual(expected);
        }
    });

    it('skips the VRAM check when the models run on the CPU', async () => {
        const cpu = { ...status({ totalGib: 4, freeGib: 1 }), selected_device: null };
        const page = setup({ '/local-inference/status': response(cpu) });
        const { text, warnings } = await openedText(
            page, () => page.dialog.confirmLocalProvider({ previousProvider: 'Soniox' }),
        );
        expect(warnings).toEqual([]);
        expect(text).not.toContain('local_dialog_gpu_free');
    });

    it('skips the translation-model prompt when Hy-MT2 is already there', async () => {
        const page = setup({ '/local-inference/status': response(status({ asrReady: true, mtReady: true })) });
        await expect(page.dialog.confirmTranslationModel({ modeLabel: 'Fast' })).resolves.toBe(true);
        expect(page.dialog.isOpen()).toBe(false);
        page.dom.window.close();
    });
});

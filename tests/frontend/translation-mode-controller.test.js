const TranslationModeController = require('../../static/js/translation-mode-controller');

function jsonResponse(body = {}, options = {}) {
    return {
        ok: options.ok !== false,
        status: options.status || (options.ok === false ? 500 : 200),
        json: vi.fn(async () => body),
    };
}

function createHarness(options = {}) {
    let storedUiMode = options.storedUiMode ?? null;
    let storedLlmMode = options.storedLlmMode ?? null;
    const runtime = { lockManualControls: false, ...(options.runtime || {}) };
    const storage = {
        setItem: vi.fn((key, value) => {
            if (options.storageThrows) throw new Error('storage unavailable');
            if (key === 'translationUiMode') storedUiMode = value;
        }),
    };
    const settingsStore = {
        loadLlmRefineMode: vi.fn(() => storedLlmMode),
        saveLlmRefineMode: vi.fn((mode) => {
            storedLlmMode = mode;
            return options.persistLlmResult !== false;
        }),
        readTranslationUiMode: vi.fn(() => storedUiMode),
    };
    const session = {
        applyLlmMode: vi.fn(),
        disableLlmBoundary: vi.fn(),
        noteHybridBoundary: vi.fn(),
    };
    const actions = {
        enforceTranslateSegmentMode: vi.fn(),
        updateSegmentModeButton: vi.fn(),
        renderSubtitles: vi.fn(),
        updateTranslationModeHint: vi.fn(),
        renderTranslationModePicker: vi.fn(),
        restartRecognition: vi.fn(),
    };
    const logger = { warn: vi.fn(), error: vi.fn() };
    const fetch = options.fetch || vi.fn(async () => jsonResponse());
    const controller = TranslationModeController.create({
        fetch,
        storage,
        settingsStore,
        console: logger,
        session,
        getRuntimeState: () => runtime,
        actions,
    });
    return {
        actions,
        controller,
        fetch,
        logger,
        runtime,
        session,
        settingsStore,
        storage,
        stored: {
            get llmMode() { return storedLlmMode; },
            get uiMode() { return storedUiMode; },
        },
    };
}

describe('TranslationModeController normalization and dependencies', () => {
    it('normalizes LLM and UI modes with the existing fallback rules', () => {
        expect(TranslationModeController.normalizeLlmRefineMode(' TRANSLATE ')).toBe('translate');
        expect(TranslationModeController.normalizeLlmRefineMode('unknown')).toBe('off');
        expect(TranslationModeController.normalizeTranslationUiMode('accurate')).toBe('accurate');
        expect(TranslationModeController.normalizeTranslationUiMode('ACCURATE')).toBe('hybrid');
        expect(TranslationModeController.TRANSLATION_UI_MODE_TO_LLM).toEqual({
            fast: 'off', accurate: 'translate', hybrid: 'refine',
        });
    });

    it('loads persisted state and fails fast for an incomplete store', () => {
        const page = createHarness({ storedUiMode: 'accurate', storedLlmMode: 'translate' });
        expect(page.controller.getDebugState()).toMatchObject({
            llmRefineAvailable: false,
            llmRefineMode: 'translate',
            translationUiMode: 'accurate',
            translationModeSynced: false,
        });
        expect(page.controller.isTranslateMode()).toBe(true);

        expect(() => TranslationModeController.create({
            fetch: vi.fn(),
            storage: { setItem() {} },
            settingsStore: {},
        })).toThrow('TranslationModeController settingsStore requires loadLlmRefineMode');
    });
});

describe('TranslationModeController local mode transitions', () => {
    it('applies LLM state, segment locking, persistence, and translate exit rendering', () => {
        const page = createHarness({ storedLlmMode: 'off' });

        expect(page.controller.applyLlmRefineMode('translate')).toBe('translate');
        expect(page.settingsStore.saveLlmRefineMode).toHaveBeenLastCalledWith('translate');
        expect(page.session.applyLlmMode).toHaveBeenLastCalledWith('translate', 'off');
        expect(page.actions.enforceTranslateSegmentMode).toHaveBeenCalledOnce();
        expect(page.actions.updateSegmentModeButton).toHaveBeenCalledOnce();

        expect(page.controller.applyLlmRefineMode('refine', { persist: false })).toBe('refine');
        expect(page.settingsStore.saveLlmRefineMode).toHaveBeenCalledTimes(1);
        expect(page.session.applyLlmMode).toHaveBeenLastCalledWith('refine', 'translate');
        expect(page.actions.renderSubtitles).toHaveBeenCalledOnce();
        expect(page.actions.updateSegmentModeButton).toHaveBeenCalledTimes(2);
    });

    it('maps a silent UI change without contacting the backend', async () => {
        const page = createHarness({ storedUiMode: 'fast', storageThrows: true });

        await expect(page.controller.setTranslationUiMode('accurate', { silent: true }))
            .resolves.toBe(true);
        expect(page.controller.getTranslationUiMode()).toBe('accurate');
        expect(page.session.noteHybridBoundary).toHaveBeenCalledWith('accurate', 'fast');
        expect(page.settingsStore.saveLlmRefineMode).toHaveBeenCalledWith('translate');
        expect(page.actions.updateTranslationModeHint).toHaveBeenCalledOnce();
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('posts the normalized mode and restarts only when requested by both sides', async () => {
        const fetch = vi.fn(async () => jsonResponse({ needs_restart: true }));
        const page = createHarness({ storedUiMode: 'hybrid', fetch });

        await expect(page.controller.setTranslationUiMode('accurate', { restartIfNeeded: true }))
            .resolves.toBe(true);
        expect(fetch).toHaveBeenCalledWith('/translation-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'accurate' }),
        });
        expect(page.actions.restartRecognition).toHaveBeenCalledWith({ auto: true });

        page.actions.restartRecognition.mockClear();
        await page.controller.setTranslationUiMode('fast', { restartIfNeeded: false });
        expect(page.actions.restartRecognition).not.toHaveBeenCalled();
    });

    it('rolls back UI, boundaries, and persisted LLM state after a POST failure', async () => {
        const fetch = vi.fn(async () => jsonResponse({}, { ok: false, status: 503 }));
        const page = createHarness({ storedUiMode: 'hybrid', storedLlmMode: 'refine', fetch });

        await expect(page.controller.setTranslationUiMode('accurate')).resolves.toBe(false);
        expect(page.controller.getTranslationUiMode()).toBe('hybrid');
        expect(page.session.noteHybridBoundary.mock.calls).toEqual([
            ['accurate', 'hybrid'],
            ['hybrid', 'accurate'],
        ]);
        expect(page.storage.setItem.mock.calls).toEqual([
            ['translationUiMode', 'accurate'],
            ['translationUiMode', 'hybrid'],
        ]);
        expect(page.settingsStore.saveLlmRefineMode.mock.calls).toEqual([
            ['translate'],
            ['refine'],
        ]);
        expect(page.actions.renderTranslationModePicker).toHaveBeenCalledOnce();
        expect(page.logger.warn).toHaveBeenCalledWith(
            'Failed to set translation mode:',
            expect.any(Error),
        );
    });
});

describe('TranslationModeController backend synchronization', () => {
    it('disables runtime LLM state without overwriting a saved preference', () => {
        const page = createHarness({ storedUiMode: 'accurate', storedLlmMode: 'translate' });

        expect(page.controller.applyBackendConfig({
            llm_refine_available: false,
            boot_id: 'boot-a',
        })).toBeNull();
        expect(page.controller.getDebugState()).toMatchObject({
            llmRefineAvailable: false,
            llmRefineMode: 'off',
            translationUiMode: 'accurate',
        });
        expect(page.session.disableLlmBoundary).toHaveBeenCalledOnce();
        expect(page.settingsStore.saveLlmRefineMode).not.toHaveBeenCalled();
        expect(page.storage.setItem).not.toHaveBeenCalled();
        expect(page.actions.renderTranslationModePicker).toHaveBeenCalledOnce();
    });

    it('syncs once per backend boot and syncs again after login reset', async () => {
        const fetch = vi.fn(async () => jsonResponse({ needs_restart: false }));
        const page = createHarness({ storedUiMode: 'accurate', fetch });

        await page.controller.applyBackendConfig({
            llm_refine_available: true,
            llm_refine_default_mode: 'refine',
            boot_id: 'boot-a',
        });
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(page.controller.getDebugState()).toMatchObject({
            defaultLlmRefineMode: 'refine',
            translationModeSynced: true,
        });

        expect(page.controller.applyBackendConfig({
            llm_refine_available: true,
            boot_id: 'boot-a',
        })).toBeNull();
        expect(fetch).toHaveBeenCalledTimes(1);

        await page.controller.applyBackendConfig({
            llm_refine_available: true,
            boot_id: 'boot-b',
        }, { currentBootId: 'boot-a' });
        expect(fetch).toHaveBeenCalledTimes(2);

        page.controller.setTranslationModeSynced(false);
        await page.controller.applyBackendConfig({
            llm_refine_available: true,
            boot_id: 'boot-b',
        });
        expect(fetch).toHaveBeenCalledTimes(3);
    });

    it('preserves the existing synced flag after an automatic sync failure', async () => {
        const fetch = vi.fn(async () => jsonResponse({}, { ok: false }));
        const page = createHarness({ storedUiMode: 'accurate', fetch });

        await expect(page.controller.applyBackendConfig({
            llm_refine_available: true,
            boot_id: 'boot-a',
        })).resolves.toBe(false);
        expect(page.controller.getDebugState()).toMatchObject({
            translationModeSynced: true,
            translationUiMode: 'accurate',
        });
        expect(fetch).toHaveBeenCalledOnce();
    });
});

describe('TranslationModeController legacy status', () => {
    it('skips the legacy endpoint when unavailable or controls are unlocked', async () => {
        const page = createHarness({ storedLlmMode: 'translate' });

        await expect(page.controller.fetchLlmRefineStatus()).resolves.toBe(false);
        expect(page.session.disableLlmBoundary).toHaveBeenCalledOnce();
        expect(page.actions.enforceTranslateSegmentMode).toHaveBeenCalledOnce();
        expect(page.fetch).not.toHaveBeenCalled();

        page.controller.setTranslationModeSynced(true);
        page.controller.applyBackendConfig({ llm_refine_available: true, boot_id: 'boot-a' });
        await expect(page.controller.fetchLlmRefineStatus()).resolves.toBe(false);
        expect(page.fetch).not.toHaveBeenCalled();
    });

    it('uses the configured default in locked mode without persisting it', async () => {
        const fetch = vi.fn(async (url) => {
            expect(url).toBe('/llm-refine');
            return jsonResponse({ mode: 'off', enabled: false });
        });
        const page = createHarness({
            storedUiMode: 'hybrid',
            storedLlmMode: 'off',
            runtime: { lockManualControls: true },
            fetch,
        });
        page.controller.setTranslationModeSynced(true);
        page.controller.applyBackendConfig({
            llm_refine_available: true,
            llm_refine_default_mode: 'translate',
            boot_id: 'boot-a',
        });

        await expect(page.controller.fetchLlmRefineStatus()).resolves.toBe(true);
        expect(page.controller.getLlmRefineMode()).toBe('translate');
        expect(page.session.applyLlmMode).toHaveBeenCalledWith('translate', 'off');
        expect(page.settingsStore.saveLlmRefineMode).not.toHaveBeenCalled();
        expect(page.actions.enforceTranslateSegmentMode).toHaveBeenCalledOnce();
    });

    it('logs legacy fetch errors and keeps the current mode', async () => {
        const failure = new Error('offline');
        const page = createHarness({
            storedLlmMode: 'refine',
            runtime: { lockManualControls: true },
            fetch: vi.fn(async () => { throw failure; }),
        });
        page.controller.setTranslationModeSynced(true);
        page.controller.applyBackendConfig({ llm_refine_available: true, boot_id: 'boot-a' });

        await expect(page.controller.fetchLlmRefineStatus()).resolves.toBe(false);
        expect(page.controller.getLlmRefineMode()).toBe('refine');
        expect(page.logger.error).toHaveBeenCalledWith(
            'Error fetching LLM refine status:', failure,
        );
    });
});

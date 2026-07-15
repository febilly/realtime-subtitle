const { JSDOM } = require('jsdom');
const RecognitionControls = require('../../static/js/recognition-controls');

function flushMicrotasks() {
    return new Promise((resolve) => setImmediate(resolve));
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <button id="restart"></button>
        <button id="pause"></button>
        <button id="auto"><span id="autoIcon"></span></button>
        <button id="audio"></button>
        <button id="osc"></button>
        <button id="language"></button>
        <button id="segment"></button>
        <main id="subtitles"></main>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const state = {
        autoRestartEnabled: true,
        currentTranslationTargetLang: 'EN',
        isPaused: true,
        isRestarting: false,
        lockManualControls: false,
        segmentModeSupported: true,
        shouldReconnect: true,
        ...(overrides.state || {}),
    };
    const calls = [];
    let socket = overrides.socket === undefined
        ? { close: vi.fn(() => calls.push('socket.close')) }
        : overrides.socket;
    const fetchImpl = overrides.fetch || vi.fn(async () => {
        calls.push('fetch');
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
    });
    const delay = overrides.delay || vi.fn(async (ms) => { calls.push(`delay:${ms}`); });
    const schedule = overrides.setTimeout || vi.fn((callback, ms) => {
        calls.push(`schedule:${ms}`);
        return { callback, ms };
    });
    const logger = overrides.logger || {
        log: vi.fn(),
        warn: vi.fn(),
        error: vi.fn(),
    };
    const dependencies = {
        clearSubtitleState: vi.fn(() => calls.push('clear')),
        closeSocket: vi.fn(() => {
            if (!socket) return false;
            socket.close();
            socket = null;
            return true;
        }),
        connect: vi.fn(() => calls.push('connect')),
        finalizeCurrentNonFinalTokens: vi.fn(() => calls.push('finalize')),
        hasUsableWebSocket: vi.fn(() => false),
        sessionCostReset: vi.fn(() => calls.push('cost.reset')),
        updatePauseButtonUi: vi.fn(() => calls.push('pause.ui')),
        ...(overrides.dependencies || {}),
    };
    const storage = overrides.storage || { setItem: vi.fn() };
    const updateState = overrides.updateState || vi.fn((patch) => Object.assign(state, patch));
    const controller = RecognitionControls.create({
        elements: {
            restartButton: document.getElementById('restart'),
            autoRestartButton: document.getElementById('auto'),
            autoRestartIcon: document.getElementById('autoIcon'),
            subtitleContainer: document.getElementById('subtitles'),
        },
        storage,
        fetch: fetchImpl,
        delay,
        setTimeout: schedule,
        logger,
        t: (key) => key,
        escapeHtml: (value) => value,
        getState: () => state,
        updateState,
        ...dependencies,
    });

    return {
        calls,
        controller,
        delay,
        dependencies,
        document,
        dom,
        fetchImpl,
        getSocket: () => socket,
        logger,
        schedule,
        state,
        storage,
        updateState,
    };
}

describe('RecognitionControls payloads', () => {
    it('normalizes restart payload language fields without inventing optional fields', () => {
        expect(RecognitionControls.buildRestartPayload({}, ' ZH ')).toEqual({
            auto: false,
            target_lang: 'zh',
        });
        expect(RecognitionControls.buildRestartPayload({
            auto: true,
            targetLang: ' JA ',
            translationMode: 'two_way',
            targetLang1: ' EN ',
            targetLang2: ' ZH ',
        }, 'de')).toEqual({
            auto: true,
            target_lang: 'ja',
            translation_mode: 'two_way',
            target_lang_1: 'en',
            target_lang_2: 'zh',
        });
    });

    it('keeps a manual restart single-flight and preserves its request/UI timing', async () => {
        const pendingDelays = [];
        const delay = vi.fn((ms) => new Promise((resolve) => pendingDelays.push({ ms, resolve })));
        const env = setup({ delay });
        const restart = env.controller.restartRecognition({
            targetLang: ' JA ',
            translationMode: 'one_way',
        });

        expect(env.state.isRestarting).toBe(true);
        expect(env.state.shouldReconnect).toBe(false);
        expect(env.dependencies.sessionCostReset).toHaveBeenCalledOnce();
        expect(env.dependencies.closeSocket).toHaveBeenCalledOnce();
        expect(env.dependencies.clearSubtitleState).toHaveBeenCalledOnce();
        expect(env.getSocket()).toBeNull();
        expect(env.document.getElementById('restart').classList.contains('restarting')).toBe(true);
        expect(env.document.getElementById('subtitles').innerHTML).toContain('restarting');
        expect(env.fetchImpl).not.toHaveBeenCalled();
        expect(await env.controller.restartRecognition()).toBe(false);
        expect(env.dependencies.sessionCostReset).toHaveBeenCalledOnce();

        expect(pendingDelays[0].ms).toBe(500);
        pendingDelays.shift().resolve();
        await flushMicrotasks();

        expect(env.fetchImpl).toHaveBeenCalledWith('/restart', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({
                auto: false,
                target_lang: 'ja',
                translation_mode: 'one_way',
            }),
        }));
        expect(env.state.isPaused).toBe(false);
        expect(env.dependencies.updatePauseButtonUi).toHaveBeenCalledOnce();
        expect(env.dependencies.connect).not.toHaveBeenCalled();
        expect(pendingDelays[0].ms).toBe(1500);

        pendingDelays.shift().resolve();
        expect(await restart).toBe(true);
        expect(env.state.shouldReconnect).toBe(true);
        expect(env.state.isRestarting).toBe(false);
        expect(env.dependencies.connect).toHaveBeenCalledOnce();
        expect(env.document.getElementById('subtitles').innerHTML).toContain('empty_state');
        expect(env.schedule).toHaveBeenCalledWith(expect.any(Function), 1500);
        env.schedule.mock.calls[0][0]();
        expect(env.document.getElementById('restart').classList.contains('restarting')).toBe(false);
        env.dom.window.close();
    });

    it('finalizes pending tokens for auto restart and reconnects only without a usable socket', async () => {
        const usable = vi.fn(() => true);
        const env = setup({ dependencies: { hasUsableWebSocket: usable } });
        const before = env.document.getElementById('subtitles').innerHTML;

        expect(await env.controller.restartRecognition({
            auto: true,
            targetLang1: ' EN ',
            targetLang2: ' FR ',
        })).toBe(true);

        expect(env.dependencies.finalizeCurrentNonFinalTokens).toHaveBeenCalledOnce();
        expect(env.dependencies.closeSocket).not.toHaveBeenCalled();
        expect(env.dependencies.sessionCostReset).not.toHaveBeenCalled();
        expect(env.dependencies.clearSubtitleState).not.toHaveBeenCalled();
        expect(env.getSocket()).not.toBeNull();
        expect(env.document.getElementById('subtitles').innerHTML).toBe(before);
        expect(env.fetchImpl).toHaveBeenCalledWith('/restart', expect.objectContaining({
            body: JSON.stringify({
                auto: true,
                target_lang: 'en',
                target_lang_1: 'en',
                target_lang_2: 'fr',
            }),
        }));
        expect(env.delay.mock.calls.map(([ms]) => ms)).toEqual([500, 1500]);
        expect(usable).toHaveBeenCalledOnce();
        expect(env.dependencies.connect).not.toHaveBeenCalled();
        expect(env.schedule).not.toHaveBeenCalled();
        env.dom.window.close();
    });
});

describe('RecognitionControls recovery and events', () => {
    it('continues a manual restart when the connection close raises', async () => {
        const closeError = new Error('close failed');
        const closeSocket = vi.fn(() => { throw closeError; });
        const env = setup({ dependencies: { closeSocket } });

        expect(await env.controller.restartRecognition()).toBe(true);
        expect(closeSocket).toHaveBeenCalledOnce();
        expect(env.logger.warn).toHaveBeenCalledWith(
            'WebSocket close during restart raised an error:',
            closeError
        );
        expect(env.fetchImpl).toHaveBeenCalledOnce();
        env.dom.window.close();
    });

    it('restores reconnect/restart state and retains the HTTP failure message', async () => {
        const fetchImpl = vi.fn(async () => ({
            ok: false,
            status: 503,
            json: async () => ({}),
        }));
        const env = setup({ fetch: fetchImpl });

        expect(await env.controller.restartRecognition()).toBe(false);
        expect(env.state.shouldReconnect).toBe(true);
        expect(env.state.isRestarting).toBe(false);
        expect(env.document.getElementById('subtitles').innerHTML).toContain('restart_failed_try_again');
        expect(env.document.getElementById('subtitles').innerHTML).not.toContain('connection_error_try_again');
        expect(env.dependencies.connect).not.toHaveBeenCalled();
        expect(env.schedule).toHaveBeenCalledWith(expect.any(Function), 1500);
        expect(env.logger.error).toHaveBeenCalledWith('Restart error:', expect.any(Error));
        env.dom.window.close();
    });

    it('backs off failed auto restarts and re-checks the enabled gate on retry', async () => {
        const fetchImpl = vi.fn(async () => ({
            ok: false,
            status: 502,
            json: async () => ({}),
        }));
        const env = setup({ fetch: fetchImpl });

        env.controller.triggerAutoRestart();
        await flushMicrotasks();
        await flushMicrotasks();

        expect(fetchImpl).toHaveBeenCalledOnce();
        expect(env.schedule).toHaveBeenCalledWith(expect.any(Function), 2000);
        const firstRetry = env.schedule.mock.calls.find(([, ms]) => ms === 2000)[0];
        firstRetry();
        await flushMicrotasks();
        await flushMicrotasks();
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        expect(env.schedule).toHaveBeenCalledWith(expect.any(Function), 4000);

        const retry = env.schedule.mock.calls.find(([, ms]) => ms === 4000)[0];
        env.state.autoRestartEnabled = false;
        retry();
        await flushMicrotasks();
        expect(fetchImpl).toHaveBeenCalledTimes(2);

        env.state.isRestarting = true;
        env.state.autoRestartEnabled = true;
        env.controller.triggerAutoRestart();
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        expect(env.logger.log).toHaveBeenCalledWith('Restart already in progress; skipping auto restart trigger.');
        env.dom.window.close();
    });

    it('invalidates a scheduled auto-restart retry after a successful restart', async () => {
        const fetchImpl = vi.fn()
            .mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) })
            .mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
        const env = setup({ fetch: fetchImpl });

        env.controller.triggerAutoRestart();
        await flushMicrotasks();
        await flushMicrotasks();
        const staleRetry = env.schedule.mock.calls.find(([, ms]) => ms === 2000)[0];

        expect(await env.controller.restartRecognition()).toBe(true);
        staleRetry();
        await flushMicrotasks();
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        env.dom.window.close();
    });

    it('forces auto restart on while locked and ignores recognition-control clicks', async () => {
        const env = setup({ state: { autoRestartEnabled: false, lockManualControls: true } });
        env.controller.init();
        env.controller.updateAutoRestartButton();

        expect(env.state.autoRestartEnabled).toBe(true);
        expect(env.document.getElementById('auto').style.display).toBe('none');
        env.document.getElementById('auto').click();
        env.document.getElementById('restart').click();
        await flushMicrotasks();
        expect(env.state.autoRestartEnabled).toBe(true);
        expect(env.storage.setItem).not.toHaveBeenCalled();
        expect(env.fetchImpl).not.toHaveBeenCalled();

        env.state.lockManualControls = false;
        env.controller.updateAutoRestartButton();
        expect(env.document.getElementById('auto').style.display).toBe('');
        env.document.getElementById('auto').click();
        expect(env.state.autoRestartEnabled).toBe(false);
        expect(env.storage.setItem).toHaveBeenCalledWith('autoRestartEnabled', false);

        env.controller.destroy();
        env.document.getElementById('auto').click();
        expect(env.storage.setItem).toHaveBeenCalledOnce();
        env.dom.window.close();
    });
});

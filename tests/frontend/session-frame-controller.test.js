const SessionFrameController = require('../../static/js/session-frame-controller');

function createHarness(options = {}) {
    const state = {
        lockManualControls: false,
        autoRestartEnabled: true,
        isRestarting: false,
        backendLoggedIn: true,
        ...(options.state || {}),
    };
    const server = { token: 'relay-token', mode: 'relay', ...(options.server || {}) };
    const events = [];
    const actions = {
        syncPauseState: vi.fn((paused) => events.push(`pause:${paused}`)),
        handleHostedSessionFrame: vi.fn((frame) => events.push(`hosted:${frame.type}`)),
        showToast: vi.fn((...args) => events.push(`toast:${args[0]}`)),
        openSettings: vi.fn((settings) => events.push(`settings:${settings.forced}`)),
        loadServerSettings: vi.fn(() => {
            events.push('loadServer');
            return server;
        }),
        saveServerSettings: vi.fn(() => events.push('saveServer')),
        updateBalanceBarVisibility: vi.fn(() => events.push('balance')),
        openLogin: vi.fn((settings) => events.push(`login:${settings.forced}`)),
        handleApiKeyFailure: vi.fn(() => false),
        triggerAutoRestart: vi.fn(() => events.push('restart')),
        ...(options.actions || {}),
    };
    const logger = { warn: vi.fn((...args) => events.push(`warn:${args[1]}`)) };
    const updateState = vi.fn((patch) => {
        events.push(`state:${JSON.stringify(patch)}`);
        Object.assign(state, patch);
    });
    const controller = SessionFrameController.create({
        t: (key) => `label:${key}`,
        console: logger,
        getState: () => state,
        updateState,
        actions,
    });
    return {
        actions,
        controller,
        events,
        logger,
        server,
        state,
        updateState,
    };
}

describe('SessionFrameController frame boundary', () => {
    it('exports the lifecycle frame types and relay error mapping', () => {
        expect(SessionFrameController.FRAME_TYPES).toEqual([
            'recognition_paused',
            'session_connected',
            'session_idle',
            'session_disconnected',
        ]);
        expect(SessionFrameController.RELAY_ERROR_KEYS).toEqual({
            billing_exhausted: 'relay_err_billing_exhausted',
            upstream_key_error: 'relay_err_upstream_key_error',
            forbidden: 'relay_err_forbidden',
            model_not_allowed: 'relay_err_model_not_allowed',
            concurrency_limit: 'relay_err_concurrency_limit',
        });
        expect(Object.isFrozen(SessionFrameController.FRAME_TYPES)).toBe(true);
        expect(Object.isFrozen(SessionFrameController.RELAY_ERROR_KEYS)).toBe(true);
    });

    it('declines frames owned by other controllers', () => {
        const page = createHarness();

        expect(page.controller.handle({ type: 'llm_cost' })).toBe(false);
        expect(page.events).toEqual([]);
    });
});

describe('SessionFrameController connected and paused frames', () => {
    it('synchronizes pause state before forwarding the frame to hosted billing', () => {
        const page = createHarness();
        const frame = { type: 'recognition_paused', paused: true };

        expect(page.controller.handle(frame)).toBe(true);

        expect(page.actions.syncPauseState).toHaveBeenCalledWith(true);
        expect(page.actions.handleHostedSessionFrame).toHaveBeenCalledWith(frame);
        expect(page.events).toEqual(['pause:true', 'hosted:recognition_paused']);
    });

    it.each(['session_connected', 'session_idle'])(
        'forwards %s directly to hosted session accounting',
        (type) => {
            const page = createHarness();
            const frame = { type };

            expect(page.controller.handle(frame)).toBe(true);
            expect(page.actions.handleHostedSessionFrame).toHaveBeenCalledWith(frame);
            expect(page.events).toEqual([`hosted:${type}`]);
        },
    );
});

describe('SessionFrameController disconnect policy', () => {
    it('logs and forwards a disconnect before applying restart policy', () => {
        const page = createHarness();
        const frame = { type: 'session_disconnected', reason: 'network lost' };

        expect(page.controller.handle(frame)).toBe(true);

        expect(page.logger.warn).toHaveBeenCalledWith(
            'Recognition session disconnected:', 'network lost',
        );
        expect(page.events).toEqual([
            'warn:network lost',
            'hosted:session_disconnected',
            'restart',
        ]);
    });

    it.each([
        ['disabled', { autoRestartEnabled: false, isRestarting: false }],
        ['already restarting', { autoRestartEnabled: true, isRestarting: true }],
    ])('does not auto-restart when %s', (_label, state) => {
        const page = createHarness({ state });

        page.controller.handle({ type: 'session_disconnected' });

        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();
        expect(page.logger.warn).toHaveBeenCalledWith(
            'Recognition session disconnected:', 'unknown',
        );
    });

    it('shows a billing action toast and suppresses restart for a terminal relay error', () => {
        const page = createHarness();

        page.controller.handle({
            type: 'session_disconnected',
            code: 'billing_exhausted',
            relay_terminal: true,
        });

        expect(page.actions.showToast).toHaveBeenCalledWith(
            'label:relay_err_billing_exhausted',
            true,
            expect.objectContaining({ timeoutMs: 8000, actionLabel: 'label:open_settings' }),
        );
        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();

        const toastOptions = page.actions.showToast.mock.calls[0][2];
        toastOptions.onAction();
        expect(page.actions.openSettings).toHaveBeenCalledWith({ forced: false });
    });

    it('delegates billing exhaustion when a provider-switch offer handler is available', () => {
        const handleBillingExhausted = vi.fn();
        const page = createHarness({ actions: { handleBillingExhausted } });
        const frame = {
            type: 'session_disconnected',
            code: 'billing_exhausted',
            relay_terminal: true,
        };

        page.controller.handle(frame);

        expect(handleBillingExhausted).toHaveBeenCalledWith(frame);
        expect(page.actions.showToast).not.toHaveBeenCalled();
    });

    it.each([
        ['upstream_key_error', 'relay_err_upstream_key_error'],
        ['model_not_allowed', 'relay_err_model_not_allowed'],
        ['concurrency_limit', 'relay_err_concurrency_limit'],
    ])('localizes the mapped %s relay error', (code, key) => {
        const page = createHarness();

        page.controller.handle({
            type: 'session_disconnected',
            code,
            relay_terminal: true,
        });

        expect(page.actions.showToast).toHaveBeenCalledWith(`label:${key}`, true);
        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();
    });

    it('clears an unlocked forbidden login before reopening the forced login panel', () => {
        const page = createHarness();

        page.controller.handle({
            type: 'session_disconnected',
            code: 'forbidden',
            relay_terminal: true,
        });

        expect(page.actions.showToast).toHaveBeenCalledWith('label:relay_err_forbidden', true);
        expect(page.server.token).toBe('');
        expect(page.actions.saveServerSettings).toHaveBeenCalledWith(page.server);
        expect(page.updateState).toHaveBeenCalledWith({ backendLoggedIn: false });
        expect(page.state.backendLoggedIn).toBe(false);
        expect(page.actions.updateBalanceBarVisibility).toHaveBeenCalledOnce();
        expect(page.actions.openLogin).toHaveBeenCalledWith({ forced: true });
        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();
        expect(page.events.slice(2)).toEqual([
            'toast:label:relay_err_forbidden',
            'loadServer',
            'saveServer',
            'state:{"backendLoggedIn":false}',
            'balance',
            'login:true',
        ]);
    });

    it('does not clear a forbidden login when manual controls are locked', () => {
        const page = createHarness({ state: { lockManualControls: true } });

        page.controller.handle({
            type: 'session_disconnected',
            code: 'forbidden',
            relay_terminal: true,
        });

        expect(page.actions.showToast).toHaveBeenCalledWith('label:relay_err_forbidden', true);
        expect(page.actions.loadServerSettings).not.toHaveBeenCalled();
        expect(page.actions.openLogin).not.toHaveBeenCalled();
    });

    it('force-opens settings for an unlocked API-key disconnect without restarting', () => {
        const page = createHarness();

        page.controller.handle({ type: 'session_disconnected', code: 'api_key' });

        expect(page.actions.openSettings).toHaveBeenCalledWith({ forced: true });
        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();
    });

    it('lets a recoverable API-key disconnect bypass forced settings and restart policy', () => {
        const handleApiKeyFailure = vi.fn(() => true);
        const page = createHarness({ actions: { handleApiKeyFailure } });
        const frame = {
            type: 'session_disconnected',
            code: 'api_key',
            reason: 'Error: 403 - Temporary API key session duration limit exceeded.',
        };

        page.controller.handle(frame);

        expect(handleApiKeyFailure).toHaveBeenCalledWith(frame);
        expect(page.actions.openSettings).not.toHaveBeenCalled();
        expect(page.actions.triggerAutoRestart).not.toHaveBeenCalled();
    });

    it('uses normal restart policy for a locked API-key disconnect', () => {
        const page = createHarness({ state: { lockManualControls: true } });

        page.controller.handle({ type: 'session_disconnected', code: 'api_key' });

        expect(page.actions.openSettings).not.toHaveBeenCalled();
        expect(page.actions.triggerAutoRestart).toHaveBeenCalledOnce();
    });
});

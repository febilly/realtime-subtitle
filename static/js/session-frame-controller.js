(function (root) {
    'use strict';

    const FRAME_TYPES = Object.freeze([
        'recognition_paused',
        'session_connected',
        'session_idle',
        'session_disconnected',
    ]);
    const RELAY_ERROR_KEYS = Object.freeze({
        billing_exhausted: 'relay_err_billing_exhausted',
        upstream_key_error: 'relay_err_upstream_key_error',
        forbidden: 'relay_err_forbidden',
        model_not_allowed: 'relay_err_model_not_allowed',
        concurrency_limit: 'relay_err_concurrency_limit',
    });

    function create(options = {}) {
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const logger = options.console || root.console || { warn() {} };
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const actions = options.actions || {};
        const call = (name, ...args) => {
            if (typeof actions[name] !== 'function') return undefined;
            return actions[name](...args);
        };

        function handle(frame = {}) {
            if (!FRAME_TYPES.includes(frame.type)) return false;

            if (frame.type === 'recognition_paused') {
                call('syncPauseState', frame.paused);
                call('handleHostedSessionFrame', frame);
                return true;
            }

            if (frame.type === 'session_connected' || frame.type === 'session_idle') {
                call('handleHostedSessionFrame', frame);
                return true;
            }

            logger.warn('Recognition session disconnected:', frame.reason || 'unknown');
            call('handleHostedSessionFrame', frame);
            const state = getState() || {};
            const relayKey = RELAY_ERROR_KEYS[frame.code];
            if (relayKey) {
                if (frame.code === 'billing_exhausted') {
                    if (typeof actions.handleBillingExhausted === 'function') {
                        call('handleBillingExhausted', frame);
                    } else {
                        call('showToast', t(relayKey), true, {
                            timeoutMs: 8000,
                            actionLabel: t('open_settings'),
                            onAction: () => call('openSettings', { forced: false }),
                        });
                    }
                } else {
                    call('showToast', t(relayKey), true);
                }

                if (frame.code === 'forbidden' && !state.lockManualControls) {
                    const server = call('loadServerSettings');
                    server.token = '';
                    call('saveServerSettings', server);
                    updateState({ backendLoggedIn: false });
                    call('updateBalanceBarVisibility');
                    call('openLogin', { forced: true });
                    return true;
                }
                if (frame.relay_terminal) return true;
            }

            if (frame.code === 'api_key' && !state.lockManualControls) {
                call('openSettings', { forced: true });
                return true;
            }
            if (state.autoRestartEnabled && !state.isRestarting) {
                call('triggerAutoRestart');
            }
            return true;
        }

        return { handle };
    }

    const api = { FRAME_TYPES, RELAY_ERROR_KEYS, create };
    root.SessionFrameController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

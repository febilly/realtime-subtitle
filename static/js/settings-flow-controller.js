(function (root) {
    'use strict';

    function create(options = {}) {
        const panel = options.panel;
        const policy = options.policy;
        if (!panel || typeof panel.open !== 'function' || typeof panel.hide !== 'function') {
            throw new TypeError('SettingsFlowController requires a settings panel');
        }
        if (!policy || typeof policy.resolveForceOpenAction !== 'function') {
            throw new TypeError('SettingsFlowController requires settings policy');
        }

        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const readState = typeof options.getState === 'function' ? options.getState : () => ({});
        const writeState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const submit = typeof options.submit === 'function' ? options.submit : () => undefined;
        const actions = options.actions || {};

        function state() {
            return readState() || {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function open(openOptions = {}) {
            return panel.open(openOptions);
        }

        function hide() {
            panel.hide();
        }

        function close() {
            return typeof panel.close === 'function' ? panel.close() : undefined;
        }

        function handleSubmit(event) {
            return submit(event);
        }

        function finishHotSave() {
            writeState({ setupRequired: false });
            hide();
            call('showToast', t('settings_saved'));
        }

        function maybeForceOpen() {
            const current = state();
            const action = policy.resolveForceOpenAction({
                lockManualControls: current.lockManualControls,
                connectionMode: current.connectionMode,
                serverSettings: current.serverSettings,
                setupRequired: current.setupRequired,
            });
            if (action === 'login') call('openLogin', { forced: true });
            if (action === 'settings') open({ forced: true });
            return action;
        }

        return { close, finishHotSave, handleSubmit, hide, maybeForceOpen, open };
    }

    const api = { create };
    root.SettingsFlowController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

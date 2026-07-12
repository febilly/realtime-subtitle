(function (root) {
    'use strict';

    function create(options = {}) {
        const policy = options.policy || root.SettingsPolicy;
        if (!policy) throw new TypeError('HostedMode.create requires SettingsPolicy');
        const documentRef = options.document || root.document;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const loadServerSettings = options.loadServerSettings || (() => ({}));
        const saveServerSettings = options.saveServerSettings || (() => {});
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const actions = options.actions || {};
        const elements = options.elements || {};
        const chooserOverlay = elements.chooserOverlay || null;
        const chooser = elements.chooser || null;
        const relayButton = elements.relayButton
            || (documentRef && documentRef.getElementById('modeChooserRelay'));
        const directButton = elements.directButton
            || (documentRef && documentRef.getElementById('modeChooserDirect'));
        let startupLoginPreopened = false;

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function setText(id, value) {
            const element = documentRef && documentRef.getElementById(id);
            if (element) element.textContent = value;
        }

        function applyChooserI18n() {
            setText('modeChooserTitle', t('chooser_title'));
            setText('modeChooserHint', t('chooser_hint'));
            setText('modeChooserRelayTitle', t('chooser_relay_title'));
            setText('modeChooserRelayDesc', t('chooser_relay_desc'));
            setText('modeChooserDirectTitle', t('chooser_direct_title'));
            setText('modeChooserDirectDesc', t('chooser_direct_desc'));
        }

        function openModeChooser() {
            return new Promise((resolve) => {
                applyChooserI18n();
                if (chooserOverlay) chooserOverlay.hidden = false;
                if (chooser) chooser.hidden = false;
                const choose = (mode) => {
                    const server = loadServerSettings();
                    server.mode = mode;
                    server.modeChosen = true;
                    saveServerSettings(server);
                    if (chooserOverlay) chooserOverlay.hidden = true;
                    if (chooser) chooser.hidden = true;
                    resolve(mode);
                };
                if (relayButton) relayButton.onclick = () => choose('relay');
                if (directButton) directButton.onclick = () => choose('direct');
            });
        }

        function clearConnectionModeChoice() {
            const server = loadServerSettings();
            server.mode = null;
            server.modeChosen = false;
            saveServerSettings(server);
        }

        function shouldPreopenHostedLogin() {
            const current = state();
            return policy.shouldPreopenHostedLogin({
                lockManualControls: current.lockManualControls,
                relayAvailable: current.relayAvailable,
                relayServerUrl: current.relayServerUrl,
                serverSettings: loadServerSettings(),
            });
        }

        function preopenHostedLoginIfNeeded() {
            if (!shouldPreopenHostedLogin()) return false;
            const server = loadServerSettings();
            if (!policy.hasExplicitConnectionMode(server)) {
                server.mode = 'relay';
                server.modeChosen = true;
                saveServerSettings(server);
            }
            startupLoginPreopened = true;
            call('openLogin', { forced: true });
            return true;
        }

        function refreshPreopenedHostedLogin() {
            if (!startupLoginPreopened) return;
            const current = state();
            if (current.lockManualControls
                || !current.relayAvailable
                || !current.relayServerUrl
                || current.connectionMode !== 'relay') {
                call('hideLogin');
                return;
            }
            call('applyLoginI18n');
            call('updateLoginSubmitState');
        }

        async function returnToModeChooser() {
            const current = state();
            if (current.lockManualControls || !current.relayAvailable) return;
            clearConnectionModeChoice();
            call('resetBootGuard');
            call('hideSettingsPanel');
            call('hideLogin');
            await openModeChooser();
            await call('ensureHostedVersionAllowed');
            await call('syncProviderFromStorage');
            call('maybeForceOpenSettings');
            call('updateBalanceBarVisibility');
        }

        async function switchToOwnKeyMode() {
            const current = state();
            if (current.lockManualControls || !current.relayAvailable) return;
            const server = loadServerSettings();
            server.mode = 'direct';
            server.modeChosen = true;
            saveServerSettings(server);
            call('resetBootGuard');
            call('hideLogin');
            call('setModeRadio', 'direct');
            call('applyModeSectionsVisibility', 'direct');
            await call('syncProviderFromStorage');
            call('updateAccountSection');
            call('updateBalanceBarVisibility');
            call('openSettings', { forced: true });
        }

        async function maybeRunFirstLaunchFlow() {
            const current = state();
            if (current.lockManualControls) return;
            const server = loadServerSettings();
            if ((server.mode === 'relay' || server.mode === 'direct')
                && policy.hasExplicitConnectionMode(server)) return;
            if (!current.relayAvailable) {
                server.mode = 'direct';
                server.modeChosen = false;
                saveServerSettings(server);
                return;
            }
            server.mode = 'relay';
            server.modeChosen = true;
            saveServerSettings(server);
            await call('ensureHostedVersionAllowed', { candidateMode: 'relay' });
        }

        return {
            applyChooserI18n,
            clearConnectionModeChoice,
            maybeRunFirstLaunchFlow,
            openModeChooser,
            preopenHostedLoginIfNeeded,
            refreshPreopenedHostedLogin,
            returnToModeChooser,
            shouldPreopenHostedLogin,
            switchToOwnKeyMode,
        };
    }

    const api = { create };
    root.HostedMode = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

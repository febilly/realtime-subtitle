(function (root) {
    'use strict';

    function create(options = {}) {
        const policy = options.policy || root.SettingsPolicy;
        if (!policy || typeof policy.buildSetupBody !== 'function') {
            throw new TypeError('SettingsSetup.create requires SettingsPolicy');
        }
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const actions = options.actions || {};

        function state() {
            const value = getState();
            return value && typeof value === 'object' ? value : {};
        }

        function callAction(name, ...args) {
            const action = actions[name];
            return typeof action === 'function' ? action(...args) : undefined;
        }

        async function push(provider, apiKey, pushOptions = {}) {
            const {
                silent = false,
                region = null,
                mode = null,
                token = null,
            } = pushOptions;
            try {
                const body = policy.buildSetupBody(provider, apiKey, { region, mode, token });
                const response = await fetchRef('/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    return { ok: false, data };
                }

                const previous = state();
                const previousProvider = previous.translationProvider;
                const previousBootId = previous.backendBootId;
                const nextProvider = data.provider || provider;
                updateState({ translationProvider: nextProvider });
                if (nextProvider !== previousProvider) {
                    callAction('sessionCostReset');
                }

                const nextBootId = data.boot_id || previousBootId;
                const patch = {
                    backendBootId: nextBootId,
                    setupRequired: !!data.setup_required,
                    pushedOverrideBootId: nextBootId,
                };
                if (typeof data.mode === 'string') patch.backendMode = data.mode;
                if (typeof data.logged_in === 'boolean') patch.backendLoggedIn = !!data.logged_in;
                updateState(patch);

                if (data.downgraded_two_way) {
                    callAction('showToast', t('gemini_no_two_way_warning'), true);
                    updateState({ uiTranslationMode: 'one_way' });
                    callAction('setUiTranslationMode', 'one_way', { persistOnly: true });
                }

                await callAction('fetchUiConfig');
                if (!silent && !data.setup_required) {
                    callAction('showToast', t('settings_saved'));
                }
                return { ok: true, data };
            } catch (error) {
                return { ok: false, data: { message: String(error) } };
            }
        }

        function directNeedsSetup(input) {
            const current = state();
            return policy.directSettingsNeedSetup({
                ...(input || {}),
                translationProvider: current.translationProvider,
                backendMode: current.backendMode,
                backendSonioxCustomUrl: current.backendSonioxCustomUrl,
                backendSonioxRegion: current.backendSonioxRegion,
                backendKeySource: current.backendKeySource,
                setupRequired: current.setupRequired,
            });
        }

        function relayNeedsSetup(input) {
            const current = state();
            return policy.relaySettingsNeedSetup({
                ...(input || {}),
                translationProvider: current.translationProvider,
                backendMode: current.backendMode,
                backendLoggedIn: current.backendLoggedIn,
                setupRequired: current.setupRequired,
            });
        }

        async function syncFromStorage() {
            const current = state();
            if (current.lockManualControls) return null;
            const plan = policy.buildProviderSyncPlan(current);
            if (!plan) return null;
            return push(plan.provider, plan.apiKey, plan.options);
        }

        return {
            push,
            directNeedsSetup,
            relayNeedsSetup,
            syncFromStorage,
        };
    }

    const api = { create };
    root.SettingsSetup = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

(function (root) {
    'use strict';

    const SONIOX_REGIONS = Object.freeze(['us', 'eu', 'jp']);

    function hasExplicitConnectionMode(settings) {
        if (!settings || typeof settings !== 'object') {
            return false;
        }
        if (settings.modeChosen === true) {
            return true;
        }
        // Preserve the legacy migration rules from app.js: direct mode was an
        // explicit choice even before modeChosen existed, as was a logged-in
        // relay mode with a saved token.
        if (settings.mode === 'direct') {
            return true;
        }
        return settings.mode === 'relay' && !!settings.token;
    }

    function resolveConnectionMode({ relayAvailable, serverSettings } = {}) {
        if (!relayAvailable) {
            return 'direct';
        }
        const settings = serverSettings || {};
        if (
            (settings.mode === 'relay' || settings.mode === 'direct')
            && hasExplicitConnectionMode(settings)
        ) {
            return settings.mode;
        }
        return null;
    }

    function normalizeSonioxRegion(region, supportedRegions = SONIOX_REGIONS) {
        const normalized = String(region || '').trim().toLowerCase();
        return supportedRegions.includes(normalized) ? normalized : 'us';
    }

    function buildSetupBody(provider, apiKey, { region = null, mode = null, token = null } = {}) {
        const body = { provider };
        if (mode) {
            body.mode = mode;
        }
        if (mode === 'relay') {
            if (token) {
                body.token = token;
            }
        } else if (apiKey) {
            body.api_key = apiKey;
        }
        if (provider === 'soniox' && region) {
            body.soniox_region = region;
        }
        return body;
    }

    function directSettingsNeedSetup({
        provider,
        region,
        apiKeyToPush,
        previousKey,
        translationProvider,
        backendMode,
        backendSonioxCustomUrl,
        backendSonioxRegion,
        backendKeySource,
        setupRequired,
    } = {}) {
        const desiredKeySource = apiKeyToPush ? 'localstorage' : 'env';
        const keyChanged = String(previousKey || '') !== String(apiKeyToPush || '');
        const providerMismatch = provider !== translationProvider;
        const modeMismatch = backendMode !== 'direct';
        const regionMismatch = !backendSonioxCustomUrl
            && provider === 'soniox'
            && region
            && normalizeSonioxRegion(region) !== backendSonioxRegion;
        const keySourceMismatch = backendKeySource !== desiredKeySource;

        return !!(
            setupRequired
            || providerMismatch
            || modeMismatch
            || regionMismatch
            || keyChanged
            || keySourceMismatch
        );
    }

    function relaySettingsNeedSetup({
        provider,
        token,
        translationProvider,
        backendMode,
        backendLoggedIn,
        setupRequired,
    } = {}) {
        const providerMismatch = provider !== translationProvider;
        const modeMismatch = backendMode !== 'relay';
        const needsLoginPush = !!token && !backendLoggedIn;

        return !!(setupRequired || providerMismatch || modeMismatch || needsLoginPush);
    }

    // Return the exact pushSetup invocation needed by syncProviderFromStorage,
    // or null when the current backend already matches persisted preferences.
    function buildProviderSyncPlan({
        lockManualControls,
        providerSettings,
        translationProvider,
        backendSonioxCustomUrl,
        backendSonioxRegion,
        connectionMode,
        serverSettings,
        backendMode,
        backendLoggedIn,
        backendKeySource,
        pushedOverrideBootId,
        backendBootId,
    } = {}) {
        if (lockManualControls) {
            return null;
        }

        const settings = providerSettings || {};
        const desiredProvider = settings.providerOverride || translationProvider || 'soniox';
        const desiredRegion = backendSonioxCustomUrl
            ? null
            : normalizeSonioxRegion(settings.sonioxRegion || backendSonioxRegion || 'us');
        const providerMismatch = settings.providerOverride
            && desiredProvider !== translationProvider;

        if (connectionMode === 'relay') {
            const server = serverSettings || {};
            const token = server.token || '';
            if (!token) {
                return null;
            }
            const modeMismatch = backendMode !== 'relay';
            const needTokenPush = token && !backendLoggedIn;
            if (!providerMismatch && !modeMismatch && !needTokenPush) {
                return null;
            }
            if (pushedOverrideBootId === backendBootId) {
                return null;
            }
            return {
                provider: desiredProvider,
                apiKey: null,
                options: {
                    silent: true,
                    mode: 'relay',
                    token,
                    region: desiredRegion,
                },
            };
        }

        if (connectionMode === 'direct') {
            const overrideKey = settings.keys && settings.keys[desiredProvider];
            const needKeyPush = overrideKey && backendKeySource !== 'localstorage';
            const regionMismatch = !backendSonioxCustomUrl
                && desiredProvider === 'soniox'
                && settings.sonioxRegion
                && desiredRegion !== backendSonioxRegion;
            const modeMismatch = backendMode !== 'direct';
            if (!providerMismatch && !needKeyPush && !regionMismatch && !modeMismatch) {
                return null;
            }
            if (pushedOverrideBootId === backendBootId) {
                return null;
            }
            return {
                provider: desiredProvider,
                apiKey: overrideKey || null,
                options: {
                    silent: true,
                    mode: 'direct',
                    region: desiredRegion,
                },
            };
        }

        return null;
    }

    function resolveForceOpenAction({
        lockManualControls,
        connectionMode,
        serverSettings,
        setupRequired,
    } = {}) {
        if (lockManualControls) {
            return null;
        }
        if (connectionMode === 'relay') {
            const hasToken = !!(serverSettings && serverSettings.token);
            return (!hasToken || setupRequired) ? 'login' : null;
        }
        if (connectionMode === 'direct' && setupRequired) {
            return 'settings';
        }
        return null;
    }

    function shouldPreopenHostedLogin({
        lockManualControls,
        relayAvailable,
        relayServerUrl,
        serverSettings,
    } = {}) {
        if (lockManualControls || !relayAvailable || !relayServerUrl) {
            return false;
        }
        const server = serverSettings || {};
        if (server.token) {
            return false;
        }
        if (server.mode === 'direct' && hasExplicitConnectionMode(server)) {
            return false;
        }
        return server.mode === 'relay' || !hasExplicitConnectionMode(server);
    }

    const api = {
        SONIOX_REGIONS,
        hasExplicitConnectionMode,
        resolveConnectionMode,
        normalizeSonioxRegion,
        buildSetupBody,
        directSettingsNeedSetup,
        relaySettingsNeedSetup,
        buildProviderSyncPlan,
        resolveForceOpenAction,
        shouldPreopenHostedLogin,
    };

    root.SettingsPolicy = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

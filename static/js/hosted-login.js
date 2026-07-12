(function (root) {
    'use strict';

    const POLL_INTERVAL_MS = 1500;
    const POLL_TIMEOUT_MS = 5 * 60 * 1000;

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const navigatorRef = options.navigator || root.navigator || {};
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const localizeBackendMessage = typeof options.localizeBackendMessage === 'function'
            ? options.localizeBackendMessage
            : (message) => String(message || '');
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const loadServerSettings = options.loadServerSettings || (() => ({}));
        const saveServerSettings = options.saveServerSettings || (() => {});
        const loadProviderSettings = options.loadProviderSettings || (() => ({}));
        const setTimeoutRef = options.setTimeout || root.setTimeout.bind(root);
        const clearTimeoutRef = options.clearTimeout || root.clearTimeout.bind(root);
        const now = typeof options.now === 'function' ? options.now : () => Date.now();
        const actions = options.actions || {};
        const elements = options.elements || {};
        const overlay = elements.overlay || null;
        const panel = elements.panel || null;
        const form = elements.form || null;
        const closeButton = elements.closeButton || null;
        const userInput = elements.userInput || null;
        const primaryButton = elements.primaryButton || null;
        const modeBackButton = elements.modeBackButton || null;
        const backButton = elements.backButton || null;
        const pasteButton = elements.pasteButton || null;
        const codeLink = elements.codeLink || null;
        const errorElement = elements.errorElement || null;
        const manualToggle = elements.manualToggle
            || (documentRef && documentRef.getElementById('loginManualToggle'));

        let forcedOpen = false;
        let registrationInfo = null;
        let submitBusy = false;
        let waitingForBrowser = false;
        let manualShown = false;
        let pollTimer = null;
        let pollState = null;
        let pollDeadline = 0;
        let initialized = false;
        const listeners = [];

        function runtimeState() {
            const value = getRuntimeState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function setText(id, value) {
            const element = documentRef && documentRef.getElementById(id);
            if (element) element.textContent = value;
        }

        function applyI18n() {
            setText('loginTitle', t('login_hosted_title'));
            setText('loginUserInputLabel', t('login_code_input_label'));
            if (pasteButton) {
                const label = t('login_paste');
                pasteButton.setAttribute('aria-label', label);
                pasteButton.setAttribute('title', label);
            }
            setText('loginModeBackButton', t('use_own_key_mode'));
            setText('loginBackButton', t('login_back'));
            setText('loginCodeLink', t('login_vrchat_button'));
            setText('loginManualToggle', t('login_manual_toggle'));
            setText('loginWaitingHint', t('login_waiting'));
            const serverHint = documentRef && documentRef.getElementById('loginServerHint');
            const relayServerUrl = runtimeState().relayServerUrl || '';
            if (serverHint) serverHint.textContent = relayServerUrl
                ? t('login_server', { url: relayServerUrl })
                : '';
            const codeHint = documentRef && documentRef.getElementById('loginCodeHint');
            if (codeHint) codeHint.hidden = !relayServerUrl;
        }

        function setLoginStep(step) {
            const inputStep = documentRef && documentRef.getElementById('loginStepInput');
            const methodStep = documentRef && documentRef.getElementById('loginStepMethod');
            const challengeStep = documentRef && documentRef.getElementById('loginStepChallenge');
            if (inputStep) inputStep.hidden = step !== 'input';
            if (methodStep) methodStep.hidden = step !== 'method';
            if (challengeStep) challengeStep.hidden = step !== 'challenge';
            if (backButton) backButton.hidden = true;
            if (primaryButton) primaryButton.textContent = t('login_submit_code');
            if (form) form.setAttribute('data-step', step);
        }

        function hasLoginCodeInput() {
            return !!String((userInput && userInput.value) || '').trim();
        }

        function updateSubmitState() {
            if (!primaryButton) return;
            primaryButton.hidden = !manualShown;
            primaryButton.disabled = submitBusy || waitingForBrowser || !hasLoginCodeInput();
            if (!submitBusy) primaryButton.textContent = t('login_submit_code');
            if (codeLink) {
                codeLink.disabled = submitBusy || waitingForBrowser;
                if (!submitBusy) codeLink.textContent = t('login_vrchat_button');
            }
        }

        function setBusy(busy) {
            submitBusy = !!busy;
            if (submitBusy && primaryButton) {
                primaryButton.disabled = true;
                primaryButton.textContent = t('login_verifying');
            }
            if (codeLink) {
                codeLink.disabled = submitBusy || waitingForBrowser;
                codeLink.textContent = submitBusy ? t('login_verifying') : t('login_vrchat_button');
            }
            if (!submitBusy) updateSubmitState();
        }

        function resetToInput() {
            if (errorElement) errorElement.textContent = '';
            setLoginStep('input');
            updateSubmitState();
        }

        function rankLabel(rank) {
            const key = String(rank || '').toLowerCase();
            const labels = {
                zh: {
                    visitor: '游客 (Visitor)', new_user: '萌新 (New User)', user: '玩家 (User)',
                    known_user: '长期玩家 (Known User)', trusted_user: '资深玩家 (Trusted User)',
                },
                en: {
                    visitor: 'Visitor', new_user: 'New User', user: 'User',
                    known_user: 'Known User', trusted_user: 'Trusted User',
                },
                ja: {
                    visitor: 'Visitor', new_user: 'New User', user: 'User',
                    known_user: 'Known User', trusted_user: 'Trusted User',
                },
            };
            const lang = (windowRef.I18N && windowRef.I18N.lang) || 'en';
            return (labels[lang] && labels[lang][key])
                || labels.en[key]
                || String(rank || '').replace(/_/g, ' ');
        }

        function renderBonusLadder() {
            const section = documentRef && documentRef.getElementById('loginBonusSection');
            const thresholdHint = documentRef && documentRef.getElementById('loginThresholdHint');
            if (!section || !thresholdHint) return;
            const threshold = registrationInfo && registrationInfo.registration_threshold;
            if (threshold) {
                thresholdHint.hidden = false;
                thresholdHint.textContent = t('login_threshold', { rank: rankLabel(threshold) });
                section.hidden = false;
            } else {
                thresholdHint.hidden = true;
                thresholdHint.textContent = '';
                section.hidden = true;
            }
        }

        async function fetchRegistrationInfo() {
            const section = documentRef && documentRef.getElementById('loginBonusSection');
            try {
                const response = await fetchRef('/account/registration-info');
                if (!response.ok) {
                    if (section) section.hidden = true;
                    return false;
                }
                registrationInfo = await response.json();
                renderBonusLadder();
                return true;
            } catch (error) {
                if (section) section.hidden = true;
                return false;
            }
        }

        function setManualShown(show) {
            manualShown = !!show;
            const field = documentRef && documentRef.getElementById('loginManualField');
            if (field) field.hidden = !manualShown;
            if (manualShown && userInput) userInput.focus();
            updateSubmitState();
        }

        function setWaiting(waiting) {
            waitingForBrowser = !!waiting;
            const hint = documentRef && documentRef.getElementById('loginWaitingHint');
            if (hint) hint.hidden = !waitingForBrowser;
            if (codeLink) codeLink.disabled = submitBusy || waitingForBrowser;
            updateSubmitState();
        }

        function stopPolling() {
            if (pollTimer) {
                clearTimeoutRef(pollTimer);
                pollTimer = null;
            }
            pollState = null;
        }

        function open({ forced = false } = {}) {
            const current = runtimeState();
            if (current.lockManualControls) return;
            forcedOpen = !!forced;
            applyI18n();
            resetToInput();
            if (userInput) userInput.value = '';
            setManualShown(false);
            setWaiting(false);
            updateSubmitState();
            void fetchRegistrationInfo();
            if (overlay) overlay.hidden = false;
            if (panel) panel.hidden = false;
            if (manualToggle) manualToggle.hidden = false;
            if (closeButton) closeButton.style.display = forcedOpen ? 'none' : '';
            if (modeBackButton) modeBackButton.hidden = !current.relayAvailable;
        }

        function hide() {
            if (overlay) overlay.hidden = true;
            if (panel) panel.hidden = true;
            forcedOpen = false;
            stopPolling();
            setWaiting(false);
            setBusy(false);
        }

        function close() {
            if (forcedOpen) return;
            hide();
        }

        function schedulePoll() {
            if (pollTimer) clearTimeoutRef(pollTimer);
            pollTimer = setTimeoutRef(() => { void pollLoginCallback(); }, POLL_INTERVAL_MS);
        }

        async function startHostedLogin() {
            const base = String(runtimeState().relayServerUrl || '').replace(/\/+$/, '');
            if (!base) {
                call('showToast', t('server_not_configured'), true);
                return;
            }
            if (errorElement) errorElement.textContent = '';
            let state = '';
            try {
                const response = await fetchRef('/account/login-begin', { method: 'POST' });
                const data = await response.json().catch(() => ({}));
                state = data && data.state;
            } catch (error) {
                // Fall back to opening without a callback.
            }
            const origin = windowRef.location.origin;
            let url = `${base}/app/#/login?next=${encodeURIComponent('/login-code')}`;
            if (state) {
                const callback = `${origin}/account/login-callback`;
                url += `&client_callback=${encodeURIComponent(callback)}&state=${encodeURIComponent(state)}`;
            }
            try {
                windowRef.open(url, '_blank', 'noopener,noreferrer');
            } catch (error) {
                if (navigatorRef.clipboard) navigatorRef.clipboard.writeText(url).catch(() => {});
                call('showToast', url);
            }
            if (state) {
                pollState = state;
                pollDeadline = now() + POLL_TIMEOUT_MS;
                setWaiting(true);
                schedulePoll();
            }
        }

        function mapVerifyError(status, data) {
            if (status === 429) return t('login_rate_limited');
            const message = data && (data.detail || data.message);
            return localizeBackendMessage(message || t('connection_error_try_again'));
        }

        async function onLoginSuccess(data) {
            const server = loadServerSettings();
            server.mode = 'relay';
            server.modeChosen = true;
            server.token = data.api_key;
            server.displayName = data.display_name || '';
            server.trustRank = data.trust_rank || '';
            saveServerSettings(server);
            call('showToast', t('login_success', { name: server.displayName || data.display_name || '' }));
            hide();
            call('updateBalanceBarVisibility');
            void call('fetchBalance');
            call('clearSubtitleState');
            const settings = loadProviderSettings();
            const provider = settings.providerOverride || runtimeState().translationProvider || 'soniox';
            call('setTranslationModeSynced', false);
            try {
                await call('pushSetup', provider, null, {
                    silent: true,
                    mode: 'relay',
                    token: data.api_key,
                });
            } catch (error) {
                call('showToast', String(error), true);
            }
        }

        async function pollLoginCallback() {
            const state = pollState;
            if (!state) return;
            if (now() > pollDeadline) {
                stopPolling();
                setWaiting(false);
                return;
            }
            try {
                const response = await fetchRef(`/account/login-poll?state=${encodeURIComponent(state)}`);
                const data = await response.json().catch(() => ({}));
                if (data && data.status === 'done' && data.api_key) {
                    stopPolling();
                    setBusy(true);
                    setWaiting(false);
                    try {
                        await onLoginSuccess(data);
                    } catch (error) {
                        if (errorElement) errorElement.textContent = String(error);
                        setBusy(false);
                    }
                    return;
                }
                if (data && data.status === 'error') {
                    stopPolling();
                    setWaiting(false);
                    if (errorElement) errorElement.textContent = mapVerifyError(response.status, data);
                    return;
                }
            } catch (error) {
                // Transient error: keep polling.
            }
            if (pollState === state) schedulePoll();
        }

        async function tryLoginCode(code) {
            const response = await fetchRef('/account/login-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code }),
            });
            const data = await response.json().catch(() => ({}));
            if (response.ok && data && data.success && data.api_key) {
                await onLoginSuccess(data);
                return 'success';
            }
            if (errorElement) errorElement.textContent = mapVerifyError(response.status, data);
            return 'error';
        }

        async function handleLoginInput() {
            const raw = String((userInput && userInput.value) || '').trim();
            if (!raw) {
                if (errorElement) errorElement.textContent = t('login_code_required');
                updateSubmitState();
                return;
            }
            setBusy(true);
            if (errorElement) errorElement.textContent = '';
            try {
                await tryLoginCode(raw);
            } catch (error) {
                if (errorElement) errorElement.textContent = String(error);
            } finally {
                setBusy(false);
            }
        }

        function bind(element, type, listener) {
            if (!element) return;
            element.addEventListener(type, listener);
            listeners.push([element, type, listener]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            bind(form, 'submit', (event) => { event.preventDefault(); void handleLoginInput(); });
            bind(backButton, 'click', resetToInput);
            bind(userInput, 'input', updateSubmitState);
            bind(codeLink, 'click', (event) => { event.preventDefault(); void startHostedLogin(); });
            bind(manualToggle, 'click', () => setManualShown(!manualShown));
            bind(modeBackButton, 'click', () => call('switchToOwnKeyMode'));
            bind(closeButton, 'click', close);
            bind(overlay, 'click', close);
            bind(pasteButton, 'click', async () => {
                try {
                    const text = await navigatorRef.clipboard.readText();
                    if (userInput) {
                        userInput.value = String(text || '').trim();
                        userInput.focus();
                        updateSubmitState();
                    }
                } catch (error) {
                    // Clipboard read can be denied; manual paste remains available.
                }
            });
            return true;
        }

        function destroy() {
            hide();
            for (const [element, type, listener] of listeners.splice(0)) {
                element.removeEventListener(type, listener);
            }
            initialized = false;
        }

        function getDebugState() {
            return {
                forcedOpen,
                submitBusy,
                waitingForBrowser,
                manualShown,
                pollState,
                pollDeadline,
                registrationInfo,
            };
        }

        return {
            applyI18n,
            close,
            destroy,
            fetchRegistrationInfo,
            getDebugState,
            handleLoginInput,
            hide,
            init,
            mapVerifyError,
            onLoginSuccess,
            open,
            pollLoginCallback,
            rankLabel,
            resetToInput,
            setBusy,
            setManualShown,
            setWaiting,
            startHostedLogin,
            stopPolling,
            tryLoginCode,
            updateSubmitState,
        };
    }

    const api = { POLL_INTERVAL_MS, POLL_TIMEOUT_MS, create };
    root.HostedLogin = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

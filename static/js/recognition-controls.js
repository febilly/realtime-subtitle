(function (root) {
    'use strict';

    function buildRestartPayload(options = {}, currentTranslationTargetLang = '') {
        const payload = { auto: !!options.auto };
        const lang = (options.targetLang || currentTranslationTargetLang || '')
            .toString()
            .trim()
            .toLowerCase();
        if (lang) {
            payload.target_lang = lang;
        }
        if (options.translationMode) {
            payload.translation_mode = options.translationMode;
        }
        if (options.targetLang1) {
            payload.target_lang_1 = String(options.targetLang1).trim().toLowerCase();
        }
        if (options.targetLang2) {
            payload.target_lang_2 = String(options.targetLang2).trim().toLowerCase();
        }
        return payload;
    }

    function create(options = {}) {
        const elements = options.elements || {};
        const restartButton = elements.restartButton || null;
        const autoRestartButton = elements.autoRestartButton || null;
        const autoRestartIcon = elements.autoRestartIcon || null;
        const subtitleContainer = elements.subtitleContainer || null;

        const storage = options.storage || root.localStorage;
        const fetchImpl = options.fetch || (root.fetch && root.fetch.bind(root));
        const schedule = options.setTimeout || root.setTimeout.bind(root);
        const wait = options.delay || ((ms) => new Promise((resolve) => schedule(resolve, ms)));
        const logger = options.logger || root.console;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const escapeHtml = typeof options.escapeHtml === 'function'
            ? options.escapeHtml
            : (value) => String(value);

        let localState = {
            autoRestartEnabled: true,
            currentTranslationTargetLang: '',
            isPaused: false,
            isRestarting: false,
            lockManualControls: false,
            segmentModeSupported: true,
            shouldReconnect: true,
            ...(options.initialState || {}),
        };
        const readState = typeof options.getState === 'function'
            ? options.getState
            : () => localState;
        const writeState = typeof options.updateState === 'function'
            ? options.updateState
            : (patch) => { localState = { ...localState, ...patch }; };

        const getSocket = typeof options.getSocket === 'function' ? options.getSocket : () => null;
        const setSocket = typeof options.setSocket === 'function' ? options.setSocket : () => {};
        const finalizeCurrentNonFinalTokens = typeof options.finalizeCurrentNonFinalTokens === 'function'
            ? options.finalizeCurrentNonFinalTokens
            : () => {};
        const clearSubtitleState = typeof options.clearSubtitleState === 'function'
            ? options.clearSubtitleState
            : () => {};
        const sessionCostReset = typeof options.sessionCostReset === 'function'
            ? options.sessionCostReset
            : () => {};
        const updatePauseButtonUi = typeof options.updatePauseButtonUi === 'function'
            ? options.updatePauseButtonUi
            : () => {};
        const hasUsableWebSocket = typeof options.hasUsableWebSocket === 'function'
            ? options.hasUsableWebSocket
            : () => false;
        const connect = typeof options.connect === 'function' ? options.connect : () => {};

        let initialized = false;
        let autoRestartClickHandler = null;
        let restartClickHandler = null;

        function state() {
            return readState() || {};
        }

        function updateAutoRestartButton() {
            if (!autoRestartButton || !autoRestartIcon) {
                return;
            }

            const current = state();
            if (current.lockManualControls) {
                autoRestartButton.style.display = 'none';
                writeState({ autoRestartEnabled: true });
                return;
            }

            autoRestartButton.style.display = '';
            if (current.autoRestartEnabled) {
                autoRestartButton.classList.add('active');
                autoRestartButton.title = t('auto_restart_on');
            } else {
                autoRestartButton.classList.remove('active');
                autoRestartButton.title = t('auto_restart_off');
            }
        }

        async function restartRecognition(restartOptions = {}) {
            const auto = !!restartOptions.auto;
            if (state().isRestarting) {
                return false;
            }

            if (!auto) {
                sessionCostReset();
            }
            writeState({ isRestarting: true, shouldReconnect: false });

            if (!auto && restartButton) {
                restartButton.classList.add('restarting');
            }

            const manualStatusHtml = `<div style="text-align: center; padding: 40px; color: #6b7280;">${escapeHtml(t('restarting'))}</div>`;
            const manualErrorHtml = `<div style="text-align: center; padding: 40px; color: #ef4444;">${escapeHtml(t('connection_error_try_again'))}</div>`;
            const manualFailureHtml = `<div style="text-align: center; padding: 40px; color: #ef4444;">${escapeHtml(t('restart_failed_try_again'))}</div>`;

            try {
                if (auto) {
                    finalizeCurrentNonFinalTokens();
                } else {
                    const socket = getSocket();
                    if (socket) {
                        logger.log('Closing old WebSocket connection...');
                        try {
                            socket.close();
                        } catch (closeError) {
                            logger.warn('WebSocket close during restart raised an error:', closeError);
                        }
                        setSocket(null);
                    }
                }

                if (!auto) {
                    clearSubtitleState();
                    if (subtitleContainer) subtitleContainer.innerHTML = manualStatusHtml;
                }

                await wait(500);

                const payload = buildRestartPayload(restartOptions, state().currentTranslationTargetLang);
                const response = await fetchImpl('/restart', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });

                if (!response.ok) {
                    if (!auto && subtitleContainer) {
                        subtitleContainer.innerHTML = manualFailureHtml;
                    }
                    throw new Error(`Restart failed with status ${response.status}`);
                }

                await response.json().catch(() => ({}));
                if (!auto) {
                    writeState({ isPaused: false });
                    updatePauseButtonUi();
                }
                logger.log(auto
                    ? 'Auto restart: new recognition session requested.'
                    : 'Recognition restarted successfully');

                await wait(1500);

                if (!auto && subtitleContainer && subtitleContainer.innerHTML === manualStatusHtml) {
                    subtitleContainer.innerHTML = `<div class="empty-state">${escapeHtml(t('empty_state'))}</div>`;
                    subtitleContainer.scrollTop = 0;
                }

                writeState({ shouldReconnect: true });
                if (!auto || !hasUsableWebSocket()) {
                    connect();
                }
                return true;
            } catch (error) {
                logger.error(`${auto ? 'Auto restart' : 'Restart'} error:`, error);
                if (!auto && subtitleContainer && subtitleContainer.innerHTML === manualStatusHtml) {
                    subtitleContainer.innerHTML = manualErrorHtml;
                }
                writeState({ shouldReconnect: true });
                return false;
            } finally {
                if (!auto && restartButton) {
                    schedule(() => restartButton.classList.remove('restarting'), 1500);
                }
                writeState({ isRestarting: false });
            }
        }

        function shouldRetryAutoRestart() {
            const current = state();
            return !!current.autoRestartEnabled
                && !!current.shouldReconnect
                && !current.isRestarting;
        }

        function scheduleAutoRestartRetry() {
            if (!shouldRetryAutoRestart()) return;
            logger.log('Auto restart failed; retrying in 2 seconds...');
            schedule(triggerAutoRestart, 2000);
        }

        function triggerAutoRestart() {
            const current = state();
            if (!current.autoRestartEnabled) {
                return;
            }

            if (current.isRestarting) {
                logger.log('Restart already in progress; skipping auto restart trigger.');
                return;
            }

            restartRecognition({ auto: true })
                .then((success) => {
                    if (!success) scheduleAutoRestartRetry();
                })
                .catch((error) => {
                    logger.error('Auto restart promise rejected:', error);
                    scheduleAutoRestartRetry();
                });
        }

        function init() {
            if (initialized) return;
            initialized = true;

            if (autoRestartButton) {
                autoRestartClickHandler = () => {
                    const current = state();
                    if (current.lockManualControls) return;
                    const enabled = !current.autoRestartEnabled;
                    writeState({ autoRestartEnabled: enabled });
                    storage.setItem('autoRestartEnabled', enabled);
                    updateAutoRestartButton();
                    logger.log(`Auto restart ${enabled ? 'enabled' : 'disabled'}`);
                };
                autoRestartButton.addEventListener('click', autoRestartClickHandler);
            }

            if (restartButton) {
                restartClickHandler = () => {
                    if (state().lockManualControls) return;
                    void restartRecognition();
                };
                restartButton.addEventListener('click', restartClickHandler);
            }
        }

        function destroy() {
            if (!initialized) return;
            if (autoRestartButton && autoRestartClickHandler) {
                autoRestartButton.removeEventListener('click', autoRestartClickHandler);
            }
            if (restartButton && restartClickHandler) {
                restartButton.removeEventListener('click', restartClickHandler);
            }
            autoRestartClickHandler = null;
            restartClickHandler = null;
            initialized = false;
        }

        return {
            destroy,
            init,
            restartRecognition,
            triggerAutoRestart,
            updateAutoRestartButton,
        };
    }

    const api = { buildRestartPayload, create };
    root.RecognitionControls = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

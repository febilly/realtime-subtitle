(function (root) {
    'use strict';

    function buildObsWebSocketUrl(location = {}) {
        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        return `${protocol}://${location.host || ''}/ws?client=obs`;
    }

    function create(options = {}) {
        const windowRef = options.window || root;
        const documentRef = options.document || windowRef.document;
        const fetchImpl = options.fetch || (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const logger = options.console || root.console || { log() {}, error() {} };
        const obsRendererApi = options.ObsRenderer || root.ObsRenderer;
        const subtitleSessionApi = options.SubtitleSession || root.SubtitleSession;
        const subtitleFrameControllerApi = options.SubtitleFrameController || root.SubtitleFrameController;
        const wsClientApi = options.WsClient || root.WsClient;

        const rootElement = documentRef ? documentRef.getElementById('obsOverlayRoot') : null;
        const container = documentRef ? documentRef.getElementById('obsTextContainer') : null;
        if (!container || !obsRendererApi || !subtitleSessionApi || !subtitleFrameControllerApi || !wsClientApi) {
            return null;
        }

        const uiState = {
            translateMode: false,
            translationUiMode: 'fast',
            suppressTranslationDisplay: false,
            currentTranslationTargetLang: '',
            isPaused: false,
        };

        const session = subtitleSessionApi.create();
        const renderer = obsRendererApi.create({
            document: documentRef,
            rootElement,
            container,
            session,
            uiState,
            packEnabled: false,
        });

        let renderScheduled = false;
        function renderSubtitles() {
            const result = renderer.render();
            return result;
        }

        function scheduleRender() {
            if (renderScheduled) return;
            renderScheduled = true;
            const raf = typeof windowRef.requestAnimationFrame === 'function'
                ? windowRef.requestAnimationFrame.bind(windowRef)
                : (fn) => setTimeout(fn, 0);
            raf(() => {
                renderScheduled = false;
                renderSubtitles();
            });
        }

        function finalizeCurrentNonFinalTokens() {
            const finalized = session.finalizeCurrentNonFinalTokens();
            if (finalized.changed) {
                renderSubtitles();
            }
        }

        function clearSubtitleState() {
            session.clearAll({
                translateMode: uiState.translateMode,
                translationUiMode: uiState.translationUiMode,
            });
            renderer.invalidateAll();
            renderSubtitles();
        }

        const frameController = subtitleFrameControllerApi.create({
            session,
            renderer,
            console: logger,
            getState: () => uiState,
            renderSubtitles: scheduleRender,
            finalizeCurrentNonFinalTokens,
            clearSubtitleState,
        });

        function applyUiConfig(data = {}) {
            if (!data || typeof data !== 'object') return;
            const provider = typeof data.provider === 'string' ? data.provider.trim().toLowerCase() : '';
            const translationMode = typeof data.translation_mode === 'string'
                ? data.translation_mode.trim().toLowerCase()
                : '';
            const translationUiMode = typeof data.translation_ui_mode === 'string'
                ? data.translation_ui_mode.trim().toLowerCase()
                : 'fast';
            const llmRefineMode = typeof data.llm_refine_mode === 'string'
                ? data.llm_refine_mode.trim().toLowerCase()
                : '';
            const llmAvailable = !!data.llm_refine_available;
            uiState.translationUiMode = translationUiMode;
            uiState.translateMode = llmAvailable && (translationUiMode === 'accurate' || llmRefineMode === 'translate');
            uiState.suppressTranslationDisplay = provider === 'gemini' && translationMode === 'none';
            if (typeof data.translation_target_lang === 'string' && data.translation_target_lang.trim()) {
                uiState.currentTranslationTargetLang = data.translation_target_lang.trim().toLowerCase();
            }
            if (typeof data.recognition_paused === 'boolean') {
                uiState.isPaused = data.recognition_paused;
            }
            session.setTranslationModes({
                translateMode: uiState.translateMode,
                translationUiMode: uiState.translationUiMode,
            });
            renderer.setUiState(uiState);
            if (data.obs_settings && typeof data.obs_settings === 'object') {
                renderer.setSettings(data.obs_settings);
            }
            renderSubtitles();
        }

        async function refreshUiConfig() {
            if (!fetchImpl) return;
            try {
                const res = await fetchImpl('/ui-config');
                if (res && res.ok) {
                    const data = await res.json();
                    if (data) applyUiConfig(data);
                }
            } catch (_) {
                // ignore transient reload errors
            }
        }

        function handleFrame(data) {
            if (!data || typeof data !== 'object') return false;
            if (data.type === 'feature_packs' && data.packs && typeof data.packs === 'object') {
                renderer.setPackEnabled(!!data.packs.obs);
                renderSubtitles();
                return true;
            }
            if (data.type === 'obs_settings' && data.settings && typeof data.settings === 'object') {
                renderer.setSettings(data.settings);
                renderSubtitles();
                return true;
            }
            if (data.type === 'recognition_paused') {
                uiState.isPaused = !!data.paused;
                return true;
            }
            if (data.type === 'session_connected') {
                void refreshUiConfig();
                return true;
            }
            if (data.type === 'clear' && !data.preserve_existing) {
                void refreshUiConfig();
            }
            return frameController.handle(data);
        }

        async function hydrateInitialState() {
            if (!fetchImpl) return;
            await Promise.all([
                fetchImpl('/feature-packs')
                    .then((res) => (res && res.ok ? res.json() : null))
                    .then((data) => {
                        if (data && data.packs && typeof data.packs === 'object') {
                            renderer.setPackEnabled(!!data.packs.obs);
                        }
                    })
                    .catch(() => {}),
                fetchImpl('/obs-settings')
                    .then((res) => (res && res.ok ? res.json() : null))
                    .then((data) => {
                        if (data && data.settings && typeof data.settings === 'object') {
                            renderer.setSettings(data.settings);
                        }
                    })
                    .catch(() => {}),
                fetchImpl('/ui-config')
                    .then((res) => (res && res.ok ? res.json() : null))
                    .then((data) => {
                        if (data) applyUiConfig(data);
                    })
                    .catch(() => {}),
            ]);
            renderSubtitles();
        }

        const wsClient = wsClientApi.createClient({
            WebSocketImpl: windowRef.WebSocket,
            getUrl: () => buildObsWebSocketUrl(windowRef.location),
            onOpen: () => logger.log('OBS WebSocket connected'),
            onFrame: (frame) => handleFrame(frame),
            onError: (error) => logger.error('OBS WebSocket error:', error),
            onClose: () => logger.log('OBS WebSocket closed'),
            getShouldReconnect: () => true,
            reconnectDelayMs: 2000,
        });

        function start() {
            void hydrateInitialState();
            wsClient.connect();
        }

        return {
            start,
            handleFrame,
            applyUiConfig,
            renderSubtitles,
            session,
            renderer,
            wsClient,
        };
    }

    const api = { buildObsWebSocketUrl, create };
    root.ObsApp = api;
    if (typeof module !== 'undefined') module.exports = api;

    if (typeof window !== 'undefined' && window.document) {
        const startApp = () => {
            const app = create({ window });
            if (app) {
                window.__obsApp = app;
                app.start();
            }
        };
        if (window.document.readyState === 'loading') {
            window.document.addEventListener('DOMContentLoaded', startApp, { once: true });
        } else {
            startApp();
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);

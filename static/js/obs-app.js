(function (root) {
    'use strict';

    const OBS_RELOAD_BUILD_KEY = 'obsBuildReloaded';

    const PREVIEW_SAMPLES = Object.freeze({
        en: [
            { origLang: 'ja', origFinal: '今日はとてもいい天気ですね。', origNonFinal: '', transLang: 'en', transText: 'The weather is really nice today.' },
            { origLang: 'ja', origFinal: 'リアルタイム字幕のテストをしています。', origNonFinal: '', transLang: 'en', transText: 'Testing the realtime subtitle overlay.' },
            { origLang: 'ja', origFinal: 'もうすぐ配信が', origNonFinal: '始まります…', transLang: 'en', transText: 'The stream is starting soon…' },
        ],
        zh: [
            { origLang: 'ja', origFinal: '今日はとてもいい天気ですね。', origNonFinal: '', transLang: 'zh', transText: '今天天气真不错呢。' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'zh', transText: '正在测试实时字幕叠加效果。' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'zh', transText: '直播很快就要开始了…' },
        ],
        ja: [
            { origLang: 'en', origFinal: 'The weather is really nice today.', origNonFinal: '', transLang: 'ja', transText: '今日はとてもいい天気ですね。' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'ja', transText: 'リアルタイム字幕のテストをしています。' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'ja', transText: '配信がもうすぐ始まります…' },
        ],
        ko: [
            { origLang: 'en', origFinal: 'The weather is really nice today.', origNonFinal: '', transLang: 'ko', transText: '오늘 날씨가 정말 좋네요.' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'ko', transText: '실시간 자막 오버레이를 테스트하고 있습니다.' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'ko', transText: '방송이 곧 시작됩니다…' },
        ],
        ru: [
            { origLang: 'en', origFinal: 'The weather is really nice today.', origNonFinal: '', transLang: 'ru', transText: 'Сегодня действительно прекрасная погода.' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'ru', transText: 'Тестируем наложение субтитров в реальном времени.' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'ru', transText: 'Трансляция скоро начнётся…' },
        ],
        es: [
            { origLang: 'en', origFinal: 'The weather is really nice today.', origNonFinal: '', transLang: 'es', transText: 'Hace muy buen tiempo hoy.' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'es', transText: 'Probando la superposición de subtítulos en tiempo real.' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'es', transText: 'La transmisión comenzará pronto…' },
        ],
        pt: [
            { origLang: 'en', origFinal: 'The weather is really nice today.', origNonFinal: '', transLang: 'pt', transText: 'O tempo está muito bom hoje.' },
            { origLang: 'en', origFinal: 'Testing the realtime subtitle overlay.', origNonFinal: '', transLang: 'pt', transText: 'Testando a sobreposição de legendas em tempo real.' },
            { origLang: 'en', origFinal: 'The stream is starting ', origNonFinal: 'soon…', transLang: 'pt', transText: 'A transmissão vai começar em breve…' },
        ],
    });

    function normalizePreviewLang(input) {
        const base = String(input || '').trim().toLowerCase().split(/[-_]/)[0];
        return Object.prototype.hasOwnProperty.call(PREVIEW_SAMPLES, base) ? base : 'en';
    }

    function parseQueryParams(location = {}) {
        const search = typeof location.search === 'string' ? location.search : '';
        if (typeof URLSearchParams === 'function') {
            return new URLSearchParams(search);
        }
        const map = new Map();
        search.replace(/^\?/, '').split('&').forEach((part) => {
            if (!part) return;
            const eq = part.indexOf('=');
            if (eq === -1) map.set(decodeURIComponent(part), '');
            else map.set(decodeURIComponent(part.slice(0, eq)), decodeURIComponent(part.slice(eq + 1)));
        });
        return map;
    }

    function isPreviewLocation(location = {}) {
        const params = parseQueryParams(location);
        return params.get('preview') === '1';
    }

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

        const previewMode = options.preview !== undefined
            ? !!options.preview
            : isPreviewLocation(windowRef.location);
        const queryParams = parseQueryParams(windowRef.location);
        let previewLang = normalizePreviewLang(
            options.previewLang || queryParams.get('lang') || (windowRef.navigator && windowRef.navigator.language),
        );
        let receivedPreviewMessage = false;
        let messageListener = null;
        let pageBuildVersion = null;
        let reloadedBuildVersion = null;

        const reloadPage = typeof options.reload === 'function'
            ? options.reload
            : () => {
                if (windowRef.location && typeof windowRef.location.reload === 'function') {
                    windowRef.location.reload();
                }
            };

        function getSessionStorage() {
            if (options.sessionStorage !== undefined) {
                return options.sessionStorage;
            }
            try {
                return windowRef.sessionStorage || null;
            } catch (_) {
                return null;
            }
        }

        function hasReloadedForBuild(build) {
            if (reloadedBuildVersion === build) {
                return true;
            }
            try {
                const store = getSessionStorage();
                if (store && typeof store.getItem === 'function') {
                    return store.getItem(OBS_RELOAD_BUILD_KEY) === build;
                }
            } catch (_) {
                // ignore storage errors
            }
            return false;
        }

        function markReloadedForBuild(build) {
            reloadedBuildVersion = build;
            try {
                const store = getSessionStorage();
                if (store && typeof store.setItem === 'function') {
                    store.setItem(OBS_RELOAD_BUILD_KEY, build);
                }
            } catch (_) {
                // ignore storage errors
            }
        }

        function handleObsBuildFrame(data) {
            if (previewMode) return;
            const build = typeof data.build === 'string' ? data.build.trim() : '';
            if (!build) return;
            if (pageBuildVersion === null) {
                pageBuildVersion = build;
                return;
            }
            if (build === pageBuildVersion) {
                return;
            }
            if (hasReloadedForBuild(build)) {
                return;
            }
            markReloadedForBuild(build);
            reloadPage();
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
            packEnabled: previewMode,
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
            session.clear({
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

        function seedPreviewSamples(lang) {
            const normalized = normalizePreviewLang(lang);
            previewLang = normalized;
            const entries = PREVIEW_SAMPLES[normalized] || PREVIEW_SAMPLES.en;
            session.clear({ translateMode: false, translationUiMode: 'fast' });
            const finalTokens = [];
            const nonFinalTokens = [];
            entries.forEach((item, index) => {
                const sid = `preview_${index + 1}`;
                if (item.origFinal) {
                    finalTokens.push({
                        text: item.origFinal,
                        is_final: true,
                        language: item.origLang,
                        translation_status: 'original',
                        llm_sentence_id: sid,
                    });
                }
                if (item.transText) {
                    finalTokens.push({
                        text: item.transText,
                        is_final: true,
                        language: item.transLang,
                        translation_status: 'translation',
                        llm_sentence_id: sid,
                    });
                }
                if (item.origNonFinal) {
                    nonFinalTokens.push({
                        text: item.origNonFinal,
                        is_final: false,
                        language: item.origLang,
                        translation_status: 'original',
                    });
                }
                if (index < entries.length - 1) {
                    finalTokens.push({
                        is_separator: true,
                        is_final: true,
                    });
                }
            });
            session.applyUpdateFrame({
                final_tokens: finalTokens,
                non_final_tokens: nonFinalTokens,
            });
        }

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
            if (typeof session.setTranslationModes === 'function') {
                session.setTranslationModes({
                    translateMode: uiState.translateMode,
                    translationUiMode: uiState.translationUiMode,
                });
            }
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
            if (data.type === 'obs_build') {
                handleObsBuildFrame(data);
                return true;
            }
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
            if (previewMode) {
                try {
                    const res = await fetchImpl('/obs-settings');
                    if (res && res.ok) {
                        const data = await res.json();
                        if (!receivedPreviewMessage && data && data.settings && typeof data.settings === 'object') {
                            renderer.setSettings(data.settings);
                            renderSubtitles();
                        }
                    }
                } catch (_) {
                    // ignore transient errors in preview
                }
                return;
            }
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

        const wsClient = previewMode
            ? { connect() {}, close() {} }
            : wsClientApi.createClient({
                WebSocketImpl: windowRef.WebSocket,
                getUrl: () => buildObsWebSocketUrl(windowRef.location),
                onOpen: () => logger.log('OBS WebSocket connected'),
                onFrame: (frame) => handleFrame(frame),
                onError: (error) => logger.error('OBS WebSocket error:', error),
                onClose: () => logger.log('OBS WebSocket closed'),
                getShouldReconnect: () => true,
                reconnectDelayMs: 2000,
            });

        function handlePreviewMessage(event) {
            if (!event) return;
            const currentOrigin = windowRef.location && windowRef.location.origin;
            if (
                currentOrigin
                && currentOrigin !== 'null'
                && event.origin
                && event.origin !== 'null'
                && event.origin !== currentOrigin
            ) {
                return;
            }
            const data = event.data;
            if (!data || typeof data !== 'object' || data.type !== 'obs_preview_update') return;
            receivedPreviewMessage = true;
            if (typeof data.lang === 'string') {
                const nextLang = normalizePreviewLang(data.lang);
                if (nextLang !== previewLang) {
                    seedPreviewSamples(nextLang);
                }
            }
            if (data.settings && typeof data.settings === 'object') {
                renderer.setSettings(data.settings);
            }
            renderSubtitles();
        }

        function start() {
            if (previewMode) {
                renderer.setPackEnabled(true);
                seedPreviewSamples(previewLang);
                renderSubtitles();
                if (typeof windowRef.addEventListener === 'function' && !messageListener) {
                    messageListener = (event) => handlePreviewMessage(event);
                    windowRef.addEventListener('message', messageListener);
                }
                void hydrateInitialState();
                return;
            }
            void hydrateInitialState();
            wsClient.connect();
        }

        function dispose() {
            if (messageListener && typeof windowRef.removeEventListener === 'function') {
                windowRef.removeEventListener('message', messageListener);
                messageListener = null;
            }
            if (typeof renderer.dispose === 'function') {
                renderer.dispose();
            }
            if (wsClient && typeof wsClient.close === 'function') {
                wsClient.close();
            }
        }

        return {
            start,
            dispose,
            handleFrame,
            applyUiConfig,
            renderSubtitles,
            isPreviewMode: () => previewMode,
            session,
            renderer,
            wsClient,
        };
    }

    const api = {
        PREVIEW_SAMPLES,
        normalizePreviewLang,
        isPreviewLocation,
        buildObsWebSocketUrl,
        create,
    };
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

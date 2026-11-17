const { JSDOM } = require('jsdom');
const Segmentation = require('../../static/js/segmentation');
const TokenStream = require('../../static/js/token-stream');
const RefineState = require('../../static/js/refine-state');
const RenderModel = require('../../static/js/render-model');
const RenderHtml = require('../../static/js/render-html');
const SubtitleSession = require('../../static/js/subtitle-session');
const SubtitleFrameController = require('../../static/js/subtitle-frame-controller');
const WsClient = require('../../static/js/ws-client');
const ObsRenderer = require('../../static/js/obs-renderer');
const ObsApp = require('../../static/js/obs-app');

const doms = [];

function createDom() {
    const dom = new JSDOM(`<!doctype html>
        <div id="obsOverlayRoot" class="ov text-mode">
            <div id="obsTextContainer" class="ov-text"></div>
        </div>
    `, { url: 'http://127.0.0.1:18765/obs' });
    doms.push(dom);
    return dom;
}

function createDeps(dom) {
    const session = SubtitleSession.create({
        TokenStream,
        RenderModel,
        RefineState,
    });
    const rootElement = dom.window.document.getElementById('obsOverlayRoot');
    const container = dom.window.document.getElementById('obsTextContainer');
    return { session, rootElement, container };
}

afterEach(() => {
    while (doms.length) doms.pop().window.close();
});

describe('ObsRenderer settings & style calculation', () => {
    it('normalizes fontSize and displayContent with safe bounds', () => {
        expect(ObsRenderer.normalizeSettings()).toEqual({
            fontSize: 42,
            displayContent: 'both',
        });
        expect(ObsRenderer.normalizeSettings({ font_size: 4, display_content: 'TRANSLATION' })).toEqual({
            fontSize: 16,
            displayContent: 'translation',
        });
        expect(ObsRenderer.normalizeSettings({ fontSize: 300, displayContent: 'bogus' })).toEqual({
            fontSize: 120,
            displayContent: 'both',
        });
    });

    it('computes scaled overlay CSS custom properties', () => {
        const computed = ObsRenderer.computeStyleVars(60);
        expect(computed.vars).toEqual({
            '--fs': '60px',
            '--a': '0.592',
            '--pad': '16px',
            '--tag-fs': '33px',
            '--tag-px': '19px',
            '--tag-py': '9px',
            '--tag-gap': '14px',
        });
        expect(computed.sentMb).toBe(27);
    });
});

describe('ObsRenderer rendering & pack gating', () => {
    it('renders final and non-final lines cleanly without ruby tags or LLM diff markup', () => {
        const dom = createDom();
        const { session, rootElement, container } = createDeps(dom);
        const renderer = ObsRenderer.create({
            document: dom.window.document,
            rootElement,
            container,
            session,
            RenderModel,
            RenderHtml,
            Segmentation,
            packEnabled: true,
        });

        session.applyUpdateFrame({
            final_tokens: [
                {
                    text: 'こんにちは',
                    is_final: true,
                    language: 'ja',
                    translation_status: 'original',
                    llm_sentence_id: 's1',
                },
                {
                    text: 'Hello',
                    is_final: true,
                    language: 'en',
                    translation_status: 'translation',
                    llm_sentence_id: 's1',
                },
            ],
            non_final_tokens: [
                {
                    text: '世界',
                    is_final: false,
                    language: 'ja',
                    translation_status: 'original',
                },
            ],
        });
        session.applyRefineResult({
            source: 'こんにちは',
            sentence_id: 's1',
            refined_translation: 'Hello there!',
            target_lang: 'en',
        });

        const res = renderer.render();
        expect(res.rendered).toBe(true);
        expect(res.disabled).toBe(false);
        expect(rootElement.style.getPropertyValue('--fs')).toBe('42px');
        expect(container.querySelectorAll('.ov-line')).toHaveLength(2);
        expect(container.querySelector('.ov-tag').textContent).toBe('JA');
        expect(container.querySelector('.ov-nf').textContent).toBe('世界');
        expect(container.innerHTML).toContain('こんにちは');
        expect(container.innerHTML).toContain('Hello there!');
        expect(container.innerHTML).not.toContain('<ruby');
        expect(container.innerHTML).not.toContain('diff-');
    });

    it('switches between both, original, and translation display modes and handles RTL Arabic', () => {
        const dom = createDom();
        const { session, rootElement, container } = createDeps(dom);
        const renderer = ObsRenderer.create({
            document: dom.window.document,
            rootElement,
            container,
            session,
            RenderModel,
            RenderHtml,
            Segmentation,
            packEnabled: true,
        });

        session.applyUpdateFrame({
            final_tokens: [
                { text: 'مرحبا', is_final: true, language: 'ar', translation_status: 'original' },
                { text: 'Hello', is_final: true, language: 'en', translation_status: 'translation' },
            ],
        });

        renderer.render();
        let lines = container.querySelectorAll('.ov-line');
        expect(lines).toHaveLength(2);
        expect(lines[0].getAttribute('dir')).toBe('rtl');
        expect(lines[0].getAttribute('lang')).toBe('ar');
        expect(lines[0].style.marginBottom).toBe('0px');
        expect(lines[1].style.marginBottom).toBe('18px');

        renderer.setSettings({ fontSize: 50, displayContent: 'original' });
        renderer.render();
        lines = container.querySelectorAll('.ov-line');
        expect(lines).toHaveLength(1);
        expect(lines[0].textContent).toContain('مرحبا');

        renderer.setSettings({ fontSize: 50, displayContent: 'translation' });
        renderer.render();
        lines = container.querySelectorAll('.ov-line');
        expect(lines).toHaveLength(1);
        expect(lines[0].textContent).toContain('Hello');
    });

    it('renders completely blank when obs pack is disabled and resumes immediately when enabled', () => {
        const dom = createDom();
        const { session, rootElement, container } = createDeps(dom);
        const renderer = ObsRenderer.create({
            document: dom.window.document,
            rootElement,
            container,
            session,
            RenderModel,
            RenderHtml,
            Segmentation,
            packEnabled: false,
        });

        session.applyUpdateFrame({
            final_tokens: [
                { text: 'Test line', is_final: true, language: 'en', translation_status: 'original' },
            ],
        });

        const disabledResult = renderer.render();
        expect(disabledResult).toEqual({ rendered: false, disabled: true, empty: true });
        expect(container.innerHTML).toBe('');
        expect(rootElement.hidden).toBe(true);

        renderer.setPackEnabled(true);
        const enabledResult = renderer.render();
        expect(enabledResult.rendered).toBe(true);
        expect(rootElement.hidden).toBe(false);
        expect(container.textContent).toContain('Test line');
    });
});

describe('ObsApp WebSocket & frame orchestration', () => {
    it('connects with ?client=obs and reacts live to feature_packs and obs_settings frames', async () => {
        const dom = createDom();
        const sockets = [];
        class FakeWebSocket {
            constructor(url) {
                this.url = url;
                this.readyState = FakeWebSocket.OPEN;
                sockets.push(this);
            }
            close() {
                this.readyState = FakeWebSocket.CLOSED;
            }
        }
        FakeWebSocket.CONNECTING = 0;
        FakeWebSocket.OPEN = 1;
        FakeWebSocket.CLOSED = 3;

        dom.window.WebSocket = FakeWebSocket;
        const fetchMock = vi.fn(async (url) => {
            if (url === '/feature-packs') {
                return { ok: true, json: async () => ({ packs: { obs: false } }) };
            }
            if (url === '/obs-settings') {
                return { ok: true, json: async () => ({ settings: { font_size: 42, display_content: 'both' } }) };
            }
            return { ok: true, json: async () => ({ provider: 'soniox', translation_ui_mode: 'fast' }) };
        });

        const app = ObsApp.create({
            window: dom.window,
            document: dom.window.document,
            fetch: fetchMock,
            ObsRenderer,
            SubtitleSession,
            SubtitleFrameController,
            WsClient,
        });
        app.start();
        await Promise.resolve();
        await Promise.resolve();

        expect(ObsApp.buildObsWebSocketUrl(dom.window.location)).toBe('ws://127.0.0.1:18765/ws?client=obs');
        expect(sockets[0].url).toBe('ws://127.0.0.1:18765/ws?client=obs');

        // Send subtitle frame while obs pack is disabled -> stays blank
        sockets[0].onmessage({
            data: JSON.stringify({
                type: 'update',
                final_tokens: [
                    { text: 'Live OBS line', is_final: true, language: 'en', translation_status: 'original' },
                ],
                non_final_tokens: [],
            }),
        });
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(dom.window.document.getElementById('obsTextContainer').innerHTML).toBe('');

        // Enable obs pack + update settings over WebSocket
        sockets[0].onmessage({
            data: JSON.stringify({
                type: 'feature_packs',
                packs: { obs: true },
            }),
        });
        sockets[0].onmessage({
            data: JSON.stringify({
                type: 'obs_settings',
                settings: { font_size: 72, display_content: 'original' },
            }),
        });

        const root = dom.window.document.getElementById('obsOverlayRoot');
        const container = dom.window.document.getElementById('obsTextContainer');
        expect(root.hidden).toBe(false);
        expect(root.style.getPropertyValue('--fs')).toBe('72px');
        expect(container.textContent).toContain('Live OBS line');
    });
});

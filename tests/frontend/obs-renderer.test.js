const { JSDOM } = require('jsdom');
const Segmentation = require('../../static/js/segmentation');
const TokenStream = require('../../static/js/token-stream');
const RefineState = require('../../static/js/refine-state');
const RenderModel = require('../../static/js/render-model');
const RenderHtml = require('../../static/js/render-html');
const SubtitleSession = require('../../static/js/subtitle-session');
const SubtitleFrameController = require('../../static/js/subtitle-frame-controller');
const WsClient = require('../../static/js/ws-client');
const ObsSettings = require('../../static/js/obs-settings');
const ObsRenderer = require('../../static/js/obs-renderer');
const ObsApp = require('../../static/js/obs-app');

const doms = [];

function createDom(url = 'http://127.0.0.1:18765/obs') {
    const dom = new JSDOM(`<!doctype html>
        <div id="obsOverlayRoot" class="ov text-mode">
            <div id="obsTextContainer" class="ov-text"></div>
        </div>
    `, { url });
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
    vi.useRealTimers();
    while (doms.length) doms.pop().window.close();
});

describe('ObsRenderer settings & style calculation', () => {
    it('normalizes all 19 settings with safe bounds, enums, and hex colors', () => {
        expect(ObsRenderer.normalizeSettings()).toEqual({
            displayContent: 'both',
            fontSize: 42,
            translationFontScale: 100,
            fontFamily: 'system',
            bold: false,
            showLangTag: true,
            originalColor: '#e5e7eb',
            translationColor: '#e5e7eb',
            outlineWidth: 0,
            outlineColor: '#000000',
            dropShadow: true,
            bgMode: 'strip',
            bgColor: '#000000',
            bgOpacity: 59,
            position: 'bottom',
            alignment: 'center',
            maxWidth: 80,
            maxSentences: 3,
            autoHideSeconds: 0,
        });

        expect(ObsRenderer.normalizeSettings({
            display_content: 'TRANSLATION',
            font_size: 4,
            translation_font_scale: 20,
            font_family: 'NOTO_SANS_CJK',
            bold: true,
            show_lang_tag: false,
            original_color: '#ABCDEF',
            translation_color: 'invalid',
            outline_width: 99,
            outline_color: '#112233',
            drop_shadow: false,
            bg_mode: 'BLOCK',
            bg_color: '#0A0B0C',
            bg_opacity: 150,
            position: 'TOP',
            alignment: 'RIGHT',
            max_width: 10,
            max_sentences: 0,
            auto_hide_seconds: 99,
        })).toEqual({
            displayContent: 'translation',
            fontSize: 16,
            translationFontScale: 50,
            fontFamily: 'noto_sans_cjk',
            bold: true,
            showLangTag: false,
            originalColor: '#abcdef',
            translationColor: '#e5e7eb',
            outlineWidth: 8,
            outlineColor: '#112233',
            dropShadow: false,
            bgMode: 'block',
            bgColor: '#0a0b0c',
            bgOpacity: 100,
            position: 'top',
            alignment: 'right',
            maxWidth: 30,
            maxSentences: 1,
            autoHideSeconds: 30,
        });

        expect(ObsRenderer.normalizeSettings({ fontSize: 300, displayContent: 'bogus' })).toMatchObject({
            fontSize: 120,
            displayContent: 'both',
        });
    });

    it('computes scaled overlay CSS custom properties', () => {
        const computed = ObsRenderer.computeStyleVars({
            fontSize: 60,
            translationFontScale: 85,
            fontFamily: 'simhei',
            bold: true,
            originalColor: '#ffffff',
            translationColor: '#fbbf24',
            outlineWidth: 2,
            outlineColor: '#112233',
            dropShadow: false,
            bgMode: 'block',
            bgColor: '#102030',
            bgOpacity: 75,
            position: 'top',
            alignment: 'left',
            maxWidth: 70,
        });
        expect(computed.vars).toEqual({
            '--fs': '60px',
            '--a': '0.75',
            '--pad': '16px',
            '--tag-fs': '33px',
            '--tag-px': '19px',
            '--tag-py': '9px',
            '--tag-gap': '14px',
            '--ov-tr-scale': '0.85',
            '--ov-font-family': ObsSettings.FONT_FAMILY_STACKS.simhei,
            '--ov-font-weight': '700',
            '--ov-orig-color': '#ffffff',
            '--ov-trans-color': '#fbbf24',
            '--ov-stroke': '4px #112233',
            '--ov-text-shadow': 'none',
            '--ov-bg-rgba': 'rgba(16, 32, 48, 0.75)',
            '--ov-justify': 'flex-start',
            '--ov-align-items': 'flex-start',
            '--ov-text-align': 'left',
            '--ov-max-width': '70%',
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
        expect(lines[0].classList.contains('ov-line--original')).toBe(true);
        expect(lines[1].classList.contains('ov-line--translation')).toBe(true);
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

    it('applies typography, colors, outline, shadow, background mode, layout, showLangTag, and maxSentences', () => {
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
            settings: {
                fontFamily: 'noto_sans_cjk',
                bold: true,
                showLangTag: false,
                translationFontScale: 80,
                originalColor: '#ffffff',
                translationColor: '#fde047',
                outlineWidth: 3,
                outlineColor: '#111827',
                dropShadow: false,
                bgMode: 'block',
                bgColor: '#123456',
                bgOpacity: 80,
                position: 'top',
                alignment: 'right',
                maxWidth: 65,
                maxSentences: 2,
            },
        });

        session.applyUpdateFrame({
            final_tokens: [
                { text: 'First sentence.', is_final: true, language: 'en', translation_status: 'original' },
                { is_separator: true, is_final: true },
                { text: 'Second sentence.', is_final: true, language: 'en', translation_status: 'original' },
                { is_separator: true, is_final: true },
                { text: 'Third sentence.', is_final: true, language: 'en', translation_status: 'original' },
                { is_separator: true, is_final: true },
                { text: 'Fourth sentence.', is_final: true, language: 'en', translation_status: 'original' },
            ],
        });

        renderer.render();
        expect(rootElement.getAttribute('data-bg-mode')).toBe('block');
        expect(rootElement.getAttribute('data-font-family')).toBe('noto_sans_cjk');
        expect(rootElement.getAttribute('data-position')).toBe('top');
        expect(rootElement.getAttribute('data-alignment')).toBe('right');
        expect(rootElement.style.getPropertyValue('--ov-font-weight')).toBe('700');
        expect(rootElement.style.getPropertyValue('--ov-tr-scale')).toBe('0.8');
        expect(rootElement.style.getPropertyValue('--ov-orig-color')).toBe('#ffffff');
        expect(rootElement.style.getPropertyValue('--ov-trans-color')).toBe('#fde047');
        expect(rootElement.style.getPropertyValue('--ov-stroke')).toBe('6px #111827');
        expect(rootElement.style.getPropertyValue('--ov-text-shadow')).toBe('none');
        expect(rootElement.style.getPropertyValue('--ov-bg-rgba')).toBe('rgba(18, 52, 86, 0.8)');
        expect(rootElement.style.getPropertyValue('--ov-justify')).toBe('flex-start');
        expect(rootElement.style.getPropertyValue('--ov-align-items')).toBe('flex-end');
        expect(rootElement.style.getPropertyValue('--ov-text-align')).toBe('right');
        expect(rootElement.style.getPropertyValue('--ov-max-width')).toBe('65%');

        expect(container.querySelectorAll('.ov-tag')).toHaveLength(0);
        const text = container.textContent;
        expect(text).not.toContain('First sentence.');
        expect(text).not.toContain('Second sentence.');
        expect(text).toContain('Third sentence.');
        expect(text).toContain('Fourth sentence.');
    });

    it('hides overlay after autoHideSeconds of inactivity and wakes on text update', () => {
        vi.useFakeTimers();
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
            settings: { autoHideSeconds: 5 },
        });

        session.applyUpdateFrame({
            final_tokens: [{ text: 'Hello.', is_final: true, language: 'en', translation_status: 'original' }],
        });
        renderer.render();
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(false);

        vi.advanceTimersByTime(4999);
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(false);

        vi.advanceTimersByTime(1);
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(true);

        // New speech wakes overlay and restarts timer
        session.applyUpdateFrame({
            final_tokens: [{ text: 'World.', is_final: true, language: 'en', translation_status: 'original' }],
        });
        renderer.render();
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(false);

        vi.advanceTimersByTime(5000);
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(true);

        // Disabling auto-hide shows it immediately
        renderer.setSettings({ autoHideSeconds: 0 });
        expect(rootElement.classList.contains('is-auto-hidden')).toBe(false);
        renderer.dispose();
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

describe('ObsApp WebSocket & preview orchestration', () => {
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
                settings: { font_size: 72, display_content: 'original', bold: true },
            }),
        });

        const root = dom.window.document.getElementById('obsOverlayRoot');
        const container = dom.window.document.getElementById('obsTextContainer');
        expect(root.hidden).toBe(false);
        expect(root.style.getPropertyValue('--fs')).toBe('72px');
        expect(root.style.getPropertyValue('--ov-font-weight')).toBe('700');
        expect(container.textContent).toContain('Live OBS line');
        app.dispose();
    });

    it('runs offline preview mode (?preview=1) without WebSocket and updates live via postMessage', async () => {
        const dom = createDom('http://127.0.0.1:18765/obs?preview=1&lang=zh');
        const wsConstructor = vi.fn();
        dom.window.WebSocket = wsConstructor;

        let resolveObsSettings;
        const fetchMock = vi.fn(() => new Promise((resolve) => {
            resolveObsSettings = resolve;
        }));

        const app = ObsApp.create({
            window: dom.window,
            document: dom.window.document,
            fetch: fetchMock,
            ObsRenderer,
            SubtitleSession,
            SubtitleFrameController,
            WsClient,
        });
        expect(app.isPreviewMode()).toBe(true);
        app.start();

        expect(wsConstructor).not.toHaveBeenCalled();
        const root = dom.window.document.getElementById('obsOverlayRoot');
        const container = dom.window.document.getElementById('obsTextContainer');
        expect(root.hidden).toBe(false);
        expect(container.textContent).toContain('正在测试实时字幕叠加效果。');
        expect(container.querySelector('.ov-nf')).not.toBeNull();

        // Live preview update via postMessage
        dom.window.dispatchEvent(new dom.window.MessageEvent('message', {
            origin: 'http://127.0.0.1:18765',
            data: {
                type: 'obs_preview_update',
                lang: 'ja',
                settings: {
                    fontSize: 64,
                    showLangTag: false,
                    bgMode: 'none',
                    alignment: 'left',
                },
            },
        }));

        expect(root.style.getPropertyValue('--fs')).toBe('64px');
        expect(root.getAttribute('data-bg-mode')).toBe('none');
        expect(root.getAttribute('data-alignment')).toBe('left');
        expect(container.querySelectorAll('.ov-tag')).toHaveLength(0);
        expect(container.textContent).toContain('リアルタイム字幕のテストをしています。');

        // Late /obs-settings fetch must not clobber postMessage updates
        resolveObsSettings({
            ok: true,
            json: async () => ({ settings: { font_size: 20, show_lang_tag: true } }),
        });
        await Promise.resolve();
        await Promise.resolve();
        expect(root.style.getPropertyValue('--fs')).toBe('64px');

        app.dispose();
    });
});

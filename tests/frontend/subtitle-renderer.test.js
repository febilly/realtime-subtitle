const { JSDOM } = require('jsdom');
const RenderHtml = require('../../static/js/render-html');
const RenderModel = require('../../static/js/render-model');
const Segmentation = require('../../static/js/segmentation');
const SubtitleRenderer = require('../../static/js/subtitle-renderer');

const pages = [];

function original(text, overrides = {}) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'en',
        translation_status: 'original',
        llm_sentence_id: 'sentence-1',
        _sequenceIndex: 0,
        ...overrides,
    };
}

function translation(text, overrides = {}) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'zh',
        source_language: 'en',
        translation_status: 'translation',
        llm_sentence_id: 'sentence-1',
        _sequenceIndex: 1,
        ...overrides,
    };
}

function createHarness(options = {}) {
    const dom = new JSDOM('<!doctype html><body><main id="subtitles"></main></body>');
    pages.push(dom);
    const document = dom.window.document;
    const container = document.getElementById('subtitles');
    container.innerHTML = options.containerHtml || '';

    let tokens = options.tokens || [];
    const view = {
        displayMode: 'both',
        suppressTranslationDisplay: false,
        translateMode: false,
        translationUiMode: 'fast',
        currentTranslationTargetLang: '',
        furiganaEnabled: false,
        speakerDiarizationEnabled: true,
        hideSpeakerLabels: false,
        ...(options.view || {}),
    };
    const capturedScrollState = { wasAtBottom: false, scrollTop: 37 };
    const scroll = {
        capture: vi.fn(() => capturedScrollState),
        reset: vi.fn(),
        restoreAfterEmpty: vi.fn(),
        completeRender: vi.fn(),
    };
    const state = {
        refined: new Map(),
        overrides: new Map(),
        confirmed: new Set(),
        translationLanguages: new Map(),
        speculative: new Map(),
        pending: new Map(),
        llmTranslateHideAfterSequence: null,
        hybridInterimAfterSequence: null,
    };
    const session = {
        buildRenderTokens: vi.fn(() => tokens),
        assignRenderTokenSequences: vi.fn(),
        getRefinedTranslation: vi.fn((id) => state.refined.get(id)),
        getTranslationOverride: vi.fn((id) => state.overrides.get(id)),
        getConfirmed: vi.fn((id) => state.confirmed.has(id)),
        getTranslationLanguage: vi.fn((id) => state.translationLanguages.get(id)),
        getSpecTranslation: vi.fn((source) => state.speculative.get(source)),
        isSpecPending: vi.fn((source) => state.pending.has(source)),
        getSpecPendingLanguage: vi.fn((source) => state.pending.get(source)),
        getLlmTranslateHideAfterSequence: vi.fn(
            () => state.llmTranslateHideAfterSequence,
        ),
        getHybridInterimAfterSequence: vi.fn(
            () => state.hybridInterimAfterSequence,
        ),
        ...(options.session || {}),
    };
    const furiganaCache = new Map();
    const furiganaService = {
        cache: furiganaCache,
        getCached: vi.fn((text) => furiganaCache.get(text)),
        request: vi.fn(),
        clearPending: vi.fn(),
        ...(options.furiganaService || {}),
    };
    const getViewState = vi.fn(() => view);
    const escapeHtml = (text) => {
        const node = document.createElement('div');
        node.textContent = text;
        return node.innerHTML;
    };
    const renderer = SubtitleRenderer.create({
        document,
        container,
        session,
        scroll,
        furiganaService,
        RenderModel,
        RenderHtml,
        Segmentation,
        escapeHtml,
        t: (key, vars = {}) => {
            if (key === 'empty_state') return 'Nothing to show';
            if (key === 'speaker_label') return `Speaker ${vars.speaker}`;
            return key;
        },
        getViewState,
    });

    return {
        capturedScrollState,
        container,
        document,
        furiganaService,
        getViewState,
        renderer,
        scroll,
        session,
        setTokens(nextTokens) {
            tokens = nextTokens;
        },
        state,
        view,
    };
}

afterEach(() => {
    while (pages.length) pages.pop().window.close();
});

describe('SubtitleRenderer dependency boundary', () => {
    it('rejects missing collaborators at creation time', () => {
        expect(() => SubtitleRenderer.create()).toThrow(
            'SubtitleRenderer.create requires container, session, scroll, and furiganaService',
        );

        const dom = new JSDOM('<main></main>');
        pages.push(dom);
        expect(() => SubtitleRenderer.create({
            container: dom.window.document.querySelector('main'),
            session: {},
            scroll: {},
            furiganaService: {},
        })).toThrow(
            'SubtitleRenderer.create requires render model/html, segmentation, and escapeHtml',
        );

        expect(() => SubtitleRenderer.create({
            document: dom.window.document,
            container: dom.window.document.querySelector('main'),
            session: {},
            scroll: {},
            furiganaService: {},
            RenderModel,
            RenderHtml,
            Segmentation,
            escapeHtml: (text) => text,
        })).toThrow('SubtitleRenderer.create requires a complete furigana service');
    });

    it('renders empty states and delegates the two empty scroll paths', () => {
        const page = createHarness({ containerHtml: '<p>stale</p>' });

        expect(page.renderer.render()).toEqual({
            rendered: false,
            empty: true,
            blocked: false,
        });
        expect(page.container.innerHTML).toBe('<div class="empty-state">Nothing to show</div>');
        expect(page.scroll.reset).toHaveBeenCalledOnce();
        expect(page.scroll.restoreAfterEmpty).not.toHaveBeenCalled();
        expect(page.scroll.completeRender).not.toHaveBeenCalled();

        page.setTokens([original('Source only.')]);
        page.view.displayMode = 'translation';
        expect(page.renderer.render()).toMatchObject({ empty: true, blocked: false });
        expect(page.scroll.reset).toHaveBeenCalledOnce();
        expect(page.scroll.restoreAfterEmpty).toHaveBeenCalledWith(page.capturedScrollState);
        expect(page.scroll.completeRender).not.toHaveBeenCalled();
    });

    it('reads dynamic view state on every render and updates ordinary lines', () => {
        const page = createHarness({
            tokens: [original('Hello.'), translation('你好。')],
        });

        expect(page.renderer.render()).toMatchObject({ rendered: true, empty: false });
        expect(page.container.textContent).toContain('Hello.');
        expect(page.container.textContent).toContain('你好。');
        expect(page.container.textContent).toContain('Speaker 1');
        expect(page.session.assignRenderTokenSequences).toHaveBeenCalledWith(
            page.session.buildRenderTokens.mock.results[0].value,
        );

        page.view.displayMode = 'original';
        page.view.hideSpeakerLabels = true;
        expect(page.renderer.render()).toMatchObject({ rendered: true, empty: false });
        expect(page.container.textContent).toContain('Hello.');
        expect(page.container.textContent).not.toContain('你好。');
        expect(page.container.querySelector('.speaker-label')).toBeNull();
        expect(page.getViewState).toHaveBeenCalled();
        expect(page.scroll.completeRender).toHaveBeenCalledTimes(2);
    });

    it('applies translate and hybrid sequence boundaries from one view snapshot', () => {
        const page = createHarness({
            tokens: [original('Hello.'), translation('你好。')],
            view: { translateMode: true, translationUiMode: 'hybrid' },
        });
        page.state.llmTranslateHideAfterSequence = 1;
        page.state.hybridInterimAfterSequence = 1;

        page.renderer.render();
        let translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.textContent).not.toContain('你好。');
        expect(translated.querySelector('.placeholder')).not.toBeNull();

        page.state.confirmed.add('sentence-1');
        page.renderer.render();
        translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.textContent).toContain('你好。');
        expect(translated.classList.contains('subtitle-line--stt-interim')).toBe(false);

        page.state.confirmed.clear();
        page.state.llmTranslateHideAfterSequence = null;
        page.renderer.render();
        translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.textContent).toContain('你好。');
        expect(translated.classList.contains('subtitle-line--stt-interim')).toBe(true);
        expect(page.getViewState).toHaveBeenCalledTimes(3);
    });

    it('renders accurate-mode speculative results and pending placeholders', () => {
        const page = createHarness({
            tokens: [original('Hello.')],
            view: {
                translateMode: true,
                translationUiMode: 'accurate',
                currentTranslationTargetLang: 'zh',
            },
        });
        page.state.speculative.set('Hello.', { text: '临时译文。', lang: 'zh' });

        page.renderer.render();
        let translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated).not.toBeNull();
        expect(translated.textContent).toContain('临时译文。');
        expect(translated.querySelector('.subtitle-text').classList.contains('non-final')).toBe(true);
        expect(translated.querySelector('.language-tag').textContent).toBe('ZH');

        page.state.speculative.clear();
        page.state.pending.set('Hello.', 'ar');
        page.renderer.render();
        translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.dir).toBe('rtl');
        expect(translated.querySelector('.subtitle-text').classList.contains('placeholder')).toBe(true);
        expect(translated.querySelector('.language-tag').textContent).toBe('AR');
    });

    it('keeps cached per-sentence translations visible while a combined source is pending', () => {
        const source = 'First sentence. Second sentence.';
        const page = createHarness({
            tokens: [original(source)],
            view: {
                translateMode: true,
                translationUiMode: 'accurate',
                currentTranslationTargetLang: 'zh',
            },
        });
        page.state.speculative.set('First sentence.', { text: '第一句。', lang: 'zh' });
        page.state.speculative.set('Second sentence.', { text: '第二句。', lang: 'zh' });
        page.state.pending.set(source, 'zh');

        page.renderer.render();
        let translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.textContent).toContain('第一句。第二句。');
        expect(translated.querySelector('.placeholder')).toBeNull();

        page.state.speculative.delete('Second sentence.');
        page.renderer.render();
        translated = page.container.querySelector('.subtitle-line:not(.original-line)');
        expect(translated.textContent).toContain('第一句。');
        expect(translated.querySelector('.placeholder')).not.toBeNull();
    });

    it('blocks a first furigana cache miss without patching the current DOM', () => {
        const page = createHarness({
            containerHtml: '<p id="sentinel">keep this DOM</p>',
            tokens: [
                original('日本語。', { language: 'ja', _sequenceIndex: 7 }),
                translation('Japanese.', { _sequenceIndex: 8 }),
            ],
            view: { furiganaEnabled: true },
        });
        const before = page.container.innerHTML;

        expect(page.renderer.render()).toEqual({
            rendered: false,
            empty: false,
            blocked: true,
        });
        expect(page.furiganaService.request).toHaveBeenCalledWith('日本語。');
        expect(page.container.innerHTML).toBe(before);
        expect(page.renderer.getDebugState().renderedSentences.size).toBe(0);
        expect(page.renderer.getDebugState().renderedBlocks.size).toBe(0);
        expect(page.scroll.completeRender).not.toHaveBeenCalled();
    });

    it('reuses the complete previous sentence on a furigana miss and skips new translation work', () => {
        const page = createHarness({
            tokens: [
                original('日本語。', { language: 'ja', _sequenceIndex: 7 }),
                translation('Old translation.', { _sequenceIndex: 8 }),
            ],
        });
        page.renderer.render();
        const previousHtml = page.container.innerHTML;
        const previousSentence = page.container.querySelector('[data-sentence-id="sent-7"]');
        const translationCalls = page.session.getTranslationOverride.mock.calls.length;

        page.view.furiganaEnabled = true;
        page.setTokens([
            original('更新後。', { language: 'ja', _sequenceIndex: 7 }),
            translation('New translation.', { _sequenceIndex: 8 }),
        ]);
        expect(page.renderer.render()).toMatchObject({ rendered: true, blocked: false });

        expect(page.furiganaService.request).toHaveBeenCalledWith('更新後。');
        expect(page.session.getTranslationOverride).toHaveBeenCalledTimes(translationCalls);
        expect(page.container.innerHTML).toBe(previousHtml);
        expect(page.container.querySelectorAll('[data-sentence-id="sent-7"]')).toHaveLength(1);
        expect(page.container.querySelector('[data-sentence-id="sent-7"]')).toBe(previousSentence);
        expect(page.container.textContent).toContain('日本語。');
        expect(page.container.textContent).toContain('Old translation.');
        expect(page.container.textContent).not.toContain('New translation.');

        page.furiganaService.cache.set('更新後。', '<ruby>更新後<rt>こうしんご</rt></ruby>。');
        page.renderer.render();
        expect(page.container.querySelector('.subtitle-line--furigana')).not.toBeNull();
        expect(page.container.textContent).toContain('New translation.');
    });

    it('invalidates sentence/block caches independently and clears pending furigana work', () => {
        const page = createHarness({ tokens: [original('Cached sentence.')] });
        page.renderer.render();
        const debug = page.renderer.getDebugState();
        expect(debug.renderedSentences.size).toBe(1);
        expect(debug.renderedBlocks.size).toBe(1);

        page.renderer.invalidateSentences();
        expect(debug.renderedSentences.size).toBe(0);
        expect(debug.renderedBlocks.size).toBe(1);

        page.renderer.render();
        expect(debug.renderedSentences.size).toBe(1);
        page.renderer.invalidateAll();
        expect(debug.renderedSentences.size).toBe(0);
        expect(debug.renderedBlocks.size).toBe(0);

        page.renderer.render();
        const visibleHtml = page.container.innerHTML;
        page.renderer.clearSession();
        expect(debug.renderedSentences.size).toBe(0);
        expect(debug.renderedBlocks.size).toBe(0);
        expect(page.furiganaService.clearPending).toHaveBeenCalledOnce();
        expect(page.container.innerHTML).toBe(visibleHtml);
    });
});

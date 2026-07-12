const { JSDOM } = require('jsdom');
const RenderHtml = require('../../static/js/render-html');

function createRenderer() {
    const dom = new JSDOM('<!doctype html><div id="subtitles"></div>');
    const { document } = dom.window;
    const renderer = RenderHtml.createRenderHtml({
        document,
        t: (key, vars) => (key === 'speaker_label' ? `Speaker <${vars.speaker}>` : key),
    });
    return {
        dom,
        document,
        container: document.getElementById('subtitles'),
        renderer,
    };
}

function line(renderer, text, language = 'en') {
    return renderer.renderSubtitleLine({
        language,
        bodyHtml: `<span class="subtitle-text">${renderer.escapeHtml(text)}</span>`,
        className: 'subtitle-line original-line',
    });
}

function sentence(renderer, id, text, language = 'en') {
    return renderer.renderSentenceHtml(id, [line(renderer, text, language)]);
}

function block(renderer, sentences, options = {}) {
    return renderer.renderSpeakerBlockHtml({
        speaker: '1',
        sentenceHtml: sentences,
        direction: 'ltr',
        ...options,
    });
}

describe('RenderHtml leaf rendering', () => {
    it('uses the current RTL language set and line direction', () => {
        const { dom, renderer } = createRenderer();
        expect(RenderHtml.isRtlLanguage('AR')).toBe(true);
        expect(RenderHtml.getLangDir('he')).toBe('rtl');
        expect(RenderHtml.getLangDir('en')).toBe('ltr');
        expect(renderer.renderSubtitleLine({
            language: 'ar',
            bodyHtml: '<span>مرحبا</span>',
        })).toBe('<div class="subtitle-line" lang="ar" dir="rtl"><span class="language-tag">AR</span><span class="subtitle-line-body" lang="ar" dir="rtl"><span>مرحبا</span></span></div>');
        dom.window.close();
    });

    it('escapes text through the injected document without escaping text-node quotes', () => {
        const { dom, renderer } = createRenderer();
        expect(renderer.escapeHtml('<b>&"\'')).toBe('&lt;b&gt;&amp;"\'');
        expect(renderer.renderTokenSpan({ text: '<unsafe>', is_final: true }))
            .toBe('<span class="subtitle-text">&lt;unsafe&gt;</span>');
        dom.window.close();
    });

    it('removes provider-inserted spaces only across East Asian token boundaries', () => {
        const tokens = [
            { text: '你', is_final: true },
            { text: '   ', is_final: true },
            { text: ' 好', is_final: true },
            { text: ' world', is_final: true },
        ];
        expect(RenderHtml.normalizeTranslationTokenTexts(tokens)).toEqual([
            '你', '', '好', ' world',
        ]);
        expect(tokens.map((token) => token.text)).toEqual(['你', '   ', ' 好', ' world']);
    });

    it('trims only the concatenated token stream edges and preserves token classes', () => {
        const { dom, renderer } = createRenderer();
        const tokens = [
            { text: '   ', is_final: true },
            { text: '  <hello> ', is_final: false },
            { text: 'world  ', is_final: true },
            { text: '\t', is_final: true },
        ];
        expect(renderer.renderTokenSpansTrimmed(tokens)).toBe(
            '<span class="subtitle-text non-final">&lt;hello&gt; </span>'
            + '<span class="subtitle-text">world</span>',
        );
        expect(tokens[1].text).toBe('  <hello> ');
        dom.window.close();
    });

    it('builds escaped speaker labels, sentence wrappers, and same-speaker blocks', () => {
        const { dom, renderer } = createRenderer();
        const sentenceHtml = sentence(renderer, 'sent-16', 'hello');
        const first = renderer.renderSpeakerBlockHtml({
            speaker: '16',
            sentenceHtml: [sentenceHtml],
            direction: 'ltr',
        });
        expect(first).toContain('class="speaker-label speaker-1"');
        expect(first).toContain('Speaker &lt;16&gt;');
        expect(first).toContain('data-sentence-id="sent-16"');

        const repeated = renderer.renderSpeakerBlockHtml({
            speaker: '16',
            previousSpeaker: '16',
            sentenceHtml: [sentenceHtml],
            direction: 'ltr',
        });
        expect(repeated).toContain('class="subtitle-block same-speaker"');
        expect(repeated).not.toContain('speaker-label');
        expect(RenderHtml.getSpeakerClass(undefined)).toBe('speaker-undefined');
        dom.window.close();
    });
});

describe('RenderHtml incremental DOM patching', () => {
    it('updates a stable render key while preserving the sentence DOM node', () => {
        const { dom, container, renderer } = createRenderer();
        const renderedSentences = new Map();
        const renderedBlocks = new Map();
        const patcher = renderer.createDomPatcher({
            container,
            renderedSentences,
            renderedBlocks,
        });

        patcher.patch(block(renderer, [sentence(renderer, 'sent-1', 'draft')]));
        const originalNode = container.querySelector('[data-sentence-id="sent-1"]');
        patcher.patch(block(renderer, [sentence(renderer, 'sent-1', 'refined')]));
        const updatedNode = container.querySelector('[data-sentence-id="sent-1"]');

        expect(updatedNode).toBe(originalNode);
        expect(updatedNode.textContent).toContain('refined');
        expect(updatedNode.textContent).not.toContain('draft');
        expect(renderedSentences.get('sent-1')).toContain('refined');
        expect(renderedBlocks.has('block-sent-1')).toBe(true);
        dom.window.close();
    });

    it('deletes a stale sentence without replacing the surviving sentence node', () => {
        const { dom, container, renderer } = createRenderer();
        const renderedSentences = new Map();
        const patcher = renderer.createDomPatcher({
            container,
            renderedSentences,
            renderedBlocks: new Map(),
        });

        patcher.patch(block(renderer, [
            sentence(renderer, 'sent-1', 'keep'),
            sentence(renderer, 'sent-2', 'remove'),
        ]));
        const survivor = container.querySelector('[data-sentence-id="sent-1"]');
        patcher.patch(block(renderer, [sentence(renderer, 'sent-1', 'keep')]));

        expect(container.querySelector('[data-sentence-id="sent-1"]')).toBe(survivor);
        expect(container.querySelector('[data-sentence-id="sent-2"]')).toBeNull();
        expect(renderedSentences.has('sent-1')).toBe(true);
        expect(renderedSentences.has('sent-2')).toBe(false);
        dom.window.close();
    });
});

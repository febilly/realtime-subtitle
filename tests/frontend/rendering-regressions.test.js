const { afterEach, describe, expect, it } = globalThis;
const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

const pages = [];

function original(text, id, language = 'en', speaker = '1', extra = {}) {
    return {
        text,
        is_final: true,
        speaker,
        language,
        translation_status: 'original',
        llm_sentence_id: id,
        ...extra,
    };
}

function translation(text, id, language = 'zh', speaker = '1', sourceLanguage = 'en') {
    return {
        text,
        is_final: true,
        speaker,
        language,
        source_language: sourceLanguage,
        translation_status: 'translation',
        llm_sentence_id: id,
    };
}

function separator() {
    return { text: '', is_final: true, is_separator: true };
}

async function openPage(options = {}) {
    const page = await createPageHarness({
        localStorage: { autoRestartEnabled: 'false', ...(options.localStorage || {}) },
        ...options,
    });
    pages.push(page);
    return page;
}

async function update(page, finalTokens, nonFinalTokens = []) {
    await page.emitFrame({ type: 'update', final_tokens: finalTokens, non_final_tokens: nonFinalTokens });
}

afterEach(() => {
    while (pages.length) pages.pop().close();
});

describe('subtitle rendering regressions', () => {
    it('keeps mixed source languages with one LLM id in one sentence and renders refine once', async () => {
        const page = await openPage();
        await update(page, [
            original('Say ', 'same', 'en'),
            original('「大丈夫。」', 'same', 'ja'),
            separator(),
            translation('没问题。', 'same'),
        ]);
        await page.emitFrame({
            type: 'refine_result', sentence_id: 'same', source: 'Say 「大丈夫。」',
            original_translation: '没问题。', refined_translation: '说“没关系”。', target_lang: 'zh',
        });

        expect(page.document.querySelectorAll('.sentence-block')).toHaveLength(1);
        expect(page.html().match(/说“没关系”。/g)).toHaveLength(1);
    });

    it('splits different ids on a language change and attaches each translation to its id', async () => {
        const page = await openPage();
        await update(page, [
            original('First.', 'one', 'en'), translation('第一。', 'one'), separator(),
            original('二番。', 'two', 'ja'), translation('Second.', 'two', 'en', 'ja'),
        ]);

        const sentences = [...page.document.querySelectorAll('.sentence-block')];
        expect(sentences).toHaveLength(2);
        expect(sentences[0].textContent).toContain('第一。');
        expect(sentences[1].textContent).toContain('Second.');
    });

    it('still splits id-less non-final source tokens when their language changes', async () => {
        const page = await openPage();
        await update(page, [], [
            original('hello ', undefined, 'en', '1', { is_final: false }),
            original('こんにちは', undefined, 'ja', '1', { is_final: false }),
        ]);
        expect(page.document.querySelectorAll('.sentence-block')).toHaveLength(2);
    });

    it('attaches a late final translation back to the earlier sentence id', async () => {
        const page = await openPage();
        await update(page, [original('Earlier.', 'early'), separator(), original('Later.', 'late')]);
        await update(page, [translation('较早。', 'early')]);

        const sentences = [...page.document.querySelectorAll('.sentence-block')];
        expect(sentences).toHaveLength(2);
        expect(sentences[0].textContent).toContain('较早。');
        expect(sentences[1].textContent).not.toContain('较早。');
    });

    it('replaces a hybrid draft after applied refine and keeps the sentence DOM node', async () => {
        const page = await openPage();
        await update(page, [original('Hello.', 'refined'), translation('你好。', 'refined')]);
        const before = page.document.querySelector('.sentence-block');
        expect(before.querySelector('.subtitle-line--stt-interim')).not.toBeNull();

        await page.emitFrame({
            type: 'refine_result', sentence_id: 'refined', source: 'Hello.',
            original_translation: '你好。', refined_translation: '您好。', target_lang: 'zh',
        });
        const after = page.document.querySelector('.sentence-block');
        expect(after).toBe(before);
        expect(after.textContent).toContain('您好。');
        expect(after.textContent).not.toContain('你好。');
        expect(after.querySelector('.subtitle-line--stt-interim')).toBeNull();
    });

    it('keeps a no-change draft but promotes it out of provisional styling', async () => {
        const page = await openPage();
        await update(page, [original('Hello.', 'confirmed'), translation('你好。', 'confirmed')]);
        expect(page.document.querySelector('.subtitle-line--stt-interim')).not.toBeNull();

        await page.emitFrame({
            type: 'refine_result', sentence_id: 'confirmed', source: 'Hello.',
            original_translation: '你好。', refined_translation: '', no_change: true,
        });
        expect(page.html()).toContain('你好。');
        expect(page.document.querySelector('.subtitle-line--stt-interim')).toBeNull();
    });

    it('removes only the retracted sentence', async () => {
        const page = await openPage();
        await update(page, [original('Remove me.', 'gone'), separator(), original('Keep me.', 'kept')]);
        await page.emitFrame({ type: 'subtitle_retract', sentence_id: 'gone' });
        expect(page.html()).not.toContain('Remove me.');
        expect(page.html()).toContain('Keep me.');
    });

    it('opens a new speaker block when the speaker changes', async () => {
        const page = await openPage();
        await update(page, [original('One.', 's1', 'en', '1'), separator(), original('Two.', 's2', 'en', '2')]);
        expect(page.document.querySelectorAll('.subtitle-block')).toHaveLength(2);
        expect(page.document.querySelectorAll('.speaker-label')).toHaveLength(2);
    });

    it.each([
        ['both', true, true],
        ['original', true, false],
        ['translation', false, true],
    ])('honors display mode %s', async (displayMode, hasOriginal, hasTranslation) => {
        const page = await openPage({ localStorage: { displayMode } });
        await update(page, [original('Source.', 'mode'), translation('译文。', 'mode')]);
        expect(page.html().includes('Source.')).toBe(hasOriginal);
        expect(page.html().includes('译文。')).toBe(hasTranslation);
    });

    it('does not create an empty speaker block when the selected mode has no visible line', async () => {
        const page = await openPage({ localStorage: { displayMode: 'translation' } });
        await update(page, [original('Only source.', 'empty')]);
        expect(page.document.querySelectorAll('.subtitle-block')).toHaveLength(0);
        expect(page.document.querySelector('.empty-state')).not.toBeNull();
    });

    it('renders an accurate-mode synthesized translation once with its language tag', async () => {
        const page = await openPage({
            localStorage: {
                translationUiMode: 'accurate',
                llmTranslationMode: 'translate',
                llmRefineMode: 'translate',
            },
            fetch: async (url) => {
                const response = defaultFetchResponse(url);
                if (new URL(String(url), 'http://localhost/').pathname !== '/ui-config') return response;
                const body = await response.json();
                return { ...response, json: async () => ({ ...body, llm_refine_available: true, translation_target_lang: 'zh' }) };
            },
        });
        await update(page, [original('Hello.', 'accurate')]);
        await page.emitFrame({
            type: 'refine_result', sentence_id: 'accurate', source: 'Hello.', original_translation: '',
            refined_translation: '您好。', target_lang: 'zh', no_change: false,
        });

        expect(page.html().match(/您好。/g)).toHaveLength(1);
        const tags = [...page.document.querySelectorAll('.language-tag')].map((node) => node.textContent);
        expect(tags).toContain('ZH');
    });
});

const { createPageHarness } = require('./helpers/page-harness');

function original(text, id) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'en',
        translation_status: 'original',
        llm_sentence_id: id,
    };
}

function translation(text, id) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'zh',
        source_language: 'en',
        translation_status: 'translation',
        llm_sentence_id: id,
    };
}

describe('full-page UI config controller wiring', () => {
    it('applies provider suppression and accepts normalized Soniox region config', async () => {
        const page = await createPageHarness({
            uiConfig: {
                provider: 'gemini',
                translation_mode: 'none',
                soniox_region: 'eu',
            },
        });
        try {
            await page.emitFrame({
                type: 'update',
                final_tokens: [
                    original('Visible source.', 'config'),
                    translation('Hidden translation.', 'config'),
                ],
                non_final_tokens: [],
            });
            expect(page.html()).toContain('Visible source.');
            expect(page.html()).not.toContain('Hidden translation.');
        } finally {
            page.close();
        }
    });
});

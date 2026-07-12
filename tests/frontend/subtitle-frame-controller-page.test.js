const { createPageHarness } = require('./helpers/page-harness');

function sourceToken(text, sentenceId) {
    return {
        text,
        is_final: true,
        speaker: '1',
        language: 'en',
        translation_status: 'original',
        llm_sentence_id: sentenceId,
    };
}

describe('full-page subtitle frame controller wiring', () => {
    it('renders updates and removes only a retracted sentence', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({
                type: 'update',
                final_tokens: [
                    sourceToken('Remove me.', 'gone'),
                    sourceToken('Keep me.', 'kept'),
                ],
                non_final_tokens: [],
            });
            expect(page.html()).toContain('Remove me.');
            expect(page.html()).toContain('Keep me.');

            await page.emitFrame({ type: 'subtitle_retract', sentence_id: 'gone' });

            expect(page.html()).not.toContain('Remove me.');
            expect(page.html()).toContain('Keep me.');
        } finally {
            page.close();
        }
    });

    it('clears model state while preserving the existing DOM until the next update', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({
                type: 'update',
                final_tokens: [sourceToken('Old sentence.', 'old')],
                non_final_tokens: [],
            });
            await page.emitFrame({ type: 'clear' });
            expect(page.html()).toContain('Old sentence.');

            await page.emitFrame({
                type: 'update',
                final_tokens: [sourceToken('New sentence.', 'new')],
                non_final_tokens: [],
            });

            expect(page.html()).not.toContain('Old sentence.');
            expect(page.html()).toContain('New sentence.');
        } finally {
            page.close();
        }
    });
});

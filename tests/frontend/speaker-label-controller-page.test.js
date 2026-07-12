const { createPageHarness } = require('./helpers/page-harness');

function original(text) {
    return {
        text,
        is_final: true,
        speaker: '2',
        language: 'en',
        translation_status: 'original',
        llm_sentence_id: 'speaker-config',
    };
}

describe('full-page speaker label controller wiring', () => {
    it('applies UI config and WebSocket visibility updates to rendered subtitles', async () => {
        const page = await createPageHarness({
            uiConfig: { hide_speaker_labels: true },
        });
        try {
            await page.emitFrame({
                type: 'update',
                final_tokens: [original('Speaker text.')],
                non_final_tokens: [],
            });
            const container = page.document.getElementById('subtitleContainer');
            expect(container.classList.contains('hide-speaker-labels')).toBe(true);
            expect(page.document.querySelector('.speaker-label')).toBeNull();

            await page.emitFrame({ type: 'speaker_labels_changed', enabled: true });
            expect(container.classList.contains('hide-speaker-labels')).toBe(false);
            expect(page.document.querySelector('.speaker-label')).not.toBeNull();
        } finally {
            page.close();
        }
    });
});

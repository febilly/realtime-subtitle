const TokenStream = require('../../static/js/token-stream');
const RenderModel = require('../../static/js/render-model');
const RefineState = require('../../static/js/refine-state');
const SubtitleSession = require('../../static/js/subtitle-session');
const SubtitleFrameController = require('../../static/js/subtitle-frame-controller');
const { createPageHarness } = require('./helpers/page-harness');

function createSession() {
    return SubtitleSession.create({ TokenStream, RenderModel, RefineState });
}

function row(id, source, options = {}) {
    return {
        id,
        source,
        translation: options.translation || '',
        source_language: options.source_language || 'en',
        target_language: options.target_language || 'zh',
        order: options.order ?? 0,
        revision: options.revision ?? 1,
        is_final: options.is_final ?? false,
        requires_translation: options.requires_translation ?? true,
    };
}

function displayedSources(session) {
    const model = RenderModel.buildRenderModel({
        tokens: session.buildRenderTokens(),
        displayMode: 'original',
    });
    return model.sentences.map((sentence) => sentence.originalTokens
        .map((token) => token.text)
        .join(''));
}

describe('local subtitle row snapshots', () => {
    it('appends a new local run after intervening online content', () => {
        const session = createSession();
        session.applyUpdateFrame({ local_segments: [row('local-old', 'Local first', { order: 0 })] });
        session.applyUpdateFrame({ final_tokens: [{
            text: 'Online middle', is_final: true, speaker: '0', language: 'en',
            translation_status: 'original',
        }] });
        session.applyUpdateFrame({ local_segments: [row('local-new', 'Local last', { order: 1 })] });
        expect(displayedSources(session)).toEqual(['Local first', 'Online middle', 'Local last']);
    });

    it('does not split an ordinary live sentence because local history exists or receives a late translation', () => {
        const session = createSession();
        session.applyUpdateFrame({ local_segments: [row('history', 'Local history')] });
        session.applyUpdateFrame({ final_tokens: [{
            text: 'Hello ', is_final: true, speaker: '1', language: 'en',
            translation_status: 'original',
        }], non_final_tokens: [{
            text: 'world', is_final: false, speaker: '1', language: 'en',
            translation_status: 'original',
        }] });
        expect(displayedSources(session)).toEqual(['Local history', 'Hello world']);
        session.applyUpdateFrame({ local_segments: [row('history', 'Local history', {
            revision: 2, translation: '旧句迟到的翻译',
        })] });
        session.applyUpdateFrame({ final_tokens: [{
            text: 'world', is_final: true, speaker: '1', language: 'en',
            translation_status: 'original',
        }], non_final_tokens: [] });
        expect(displayedSources(session)).toEqual(['Local history', 'Hello world']);
    });

    it('places an online caption received after a local final row below that row', () => {
        const session = createSession();
        session.applyUpdateFrame({
            local_segments: [row('run-local', 'Local final', { is_final: true })],
        });
        session.applyUpdateFrame({
            final_tokens: [{
                text: 'Online later', is_final: true, speaker: '0', language: 'en',
                translation_status: 'original',
            }],
        });

        expect(displayedSources(session)).toEqual(['Local final', 'Online later']);
    });

    it('keeps ordinary-local-ordinary arrival order while local rows remain visible', () => {
        const session = createSession();
        session.applyUpdateFrame({
            final_tokens: [{
                text: 'Online first', is_final: true, speaker: '0', language: 'en',
                translation_status: 'original',
            }],
        });
        session.applyUpdateFrame({
            local_segments: [row('run-local', 'Local middle', { is_final: true })],
        });
        session.applyUpdateFrame({
            final_tokens: [{
                text: 'Online last', is_final: true, speaker: '0', language: 'en',
                translation_status: 'original',
            }],
        });

        expect(displayedSources(session)).toEqual([
            'Online first', 'Local middle', 'Online last',
        ]);
    });

    it('upserts stable rows in backend order and inserts a split before an existing suffix', () => {
        const session = createSession();
        session.applyUpdateFrame({
            type: 'update',
            local_segments: [
                row('run-a', 'First ', { order: 0 }),
                row('run-c', 'third', { order: 2 }),
            ],
        });
        const before = session.getDebugState().localSegments;
        const firstToken = before[0].originalToken;
        const firstSequence = firstToken._sequenceIndex;

        session.applyUpdateFrame({
            type: 'update',
            local_segments: [
                row('run-a', 'First revised ', { order: 0, revision: 2 }),
                row('run-b', 'second ', { order: 1 }),
            ],
        });

        const state = session.getDebugState();
        expect(state.localSegments.map((entry) => entry.id)).toEqual(['run-a', 'run-b', 'run-c']);
        expect(state.localSegments[0].originalToken).toBe(firstToken);
        expect(state.localSegments[0].originalToken).toMatchObject({
            text: 'First revised ',
            speaker: '0',
            _sequenceIndex: firstSequence,
        });
        const model = RenderModel.buildRenderModel({
            tokens: session.buildRenderTokens(),
            displayMode: 'both',
        });
        expect(model.sentences.map((sentence) => sentence.originalTokens.map((token) => token.text).join('')))
            .toEqual(['First revised ', 'second ', 'third']);
    });

    it('keeps local rows isolated from IPC token frames and tombstones removed rows', () => {
        const session = createSession();
        session.applyUpdateFrame({
            final_tokens: [{
                text: 'IPC text', is_final: true, speaker: '0', language: 'en',
                translation_status: 'original',
            }],
            local_segments: [row('run-local', 'Local row', { order: 0 })],
        });

        const model = RenderModel.buildRenderModel({
            tokens: session.buildRenderTokens(),
            displayMode: 'original',
        });
        expect(model.sentences.map((sentence) => sentence.originalTokens.map((token) => token.text).join('')))
            .toEqual(['IPC text', 'Local row']);

        session.applyUpdateFrame({
            non_final_tokens: [{
                text: 'IPC draft', is_final: false, speaker: '0', language: 'en',
                translation_status: 'original',
            }],
        });
        session.applyUpdateFrame({
            local_segments: [row('run-local', 'Revised local row', { revision: 2 })],
        });
        expect(session.getDebugState().currentNonFinalTokens).toMatchObject([
            { text: 'IPC draft' },
        ]);

        session.applyUpdateFrame({
            local_removed_segment_ids: ['run-local'],
            local_segments: [row('run-local', 'must not return', { revision: 2 })],
        });
        expect(session.getDebugState().localSegments).toEqual([]);

        session.applyUpdateFrame({ local_reset: true });
        session.applyUpdateFrame({ local_segments: [row('run-local', 'new run', { revision: 1 })] });
        expect(session.getDebugState().localSegments.map((entry) => entry.originalToken.text)).toEqual(['new run']);
    });

    it('forwards local row fields through the frame controller', () => {
        const session = createSession();
        const renderSubtitles = vi.fn();
        const controller = SubtitleFrameController.create({
            session,
            renderer: { invalidateAll: vi.fn() },
            renderSubtitles,
        });

        expect(controller.handle({
            type: 'update',
            local_segments: [row('run-1', 'Forwarded row', { translation: '已转写' })],
        })).toBe(true);
        expect(renderSubtitles).toHaveBeenCalledOnce();
        expect(session.getDebugState().localSegments[0]).toMatchObject({
            id: 'run-1', source: 'Forwarded row', translation: '已转写',
        });
    });
});

describe('local subtitle DOM updates', () => {
    const pages = [];

    afterEach(() => {
        while (pages.length) pages.pop().close();
    });

    it('updates a row in place rather than recreating its sentence block', async () => {
        const page = await createPageHarness();
        pages.push(page);
        await page.emitFrame({
            type: 'update',
            local_segments: [row('run-dom', 'Initial source', { translation: '初始译文' })],
        });
        const firstNode = page.document.querySelector('.sentence-block');
        expect(firstNode).not.toBeNull();

        await page.emitFrame({
            type: 'update',
            local_segments: [row('run-dom', 'Revised source', {
                translation: '修订译文', revision: 2,
            })],
        });

        expect(page.document.querySelector('.sentence-block')).toBe(firstNode);
        expect(page.html()).toContain('Revised source');
        expect(page.html()).toContain('修订译文');
    });
});

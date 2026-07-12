(function (root) {
    'use strict';

    const FRAME_TYPES = Object.freeze([
        'spec_translation_pending',
        'spec_translation',
        'refine_result',
        'subtitle_retract',
        'clear',
        'update',
    ]);

    function create(options = {}) {
        const session = options.session;
        const renderer = options.renderer;
        const logger = options.console || root.console || { log() {} };
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const renderSubtitles = typeof options.renderSubtitles === 'function'
            ? options.renderSubtitles
            : () => {};
        const finalizeCurrentNonFinalTokens = typeof options.finalizeCurrentNonFinalTokens === 'function'
            ? options.finalizeCurrentNonFinalTokens
            : () => {};
        const clearSubtitleState = typeof options.clearSubtitleState === 'function'
            ? options.clearSubtitleState
            : () => {};
        if (!session) throw new TypeError('SubtitleFrameController.create requires a session');
        if (!renderer) throw new TypeError('SubtitleFrameController.create requires a renderer');

        function handle(frame = {}) {
            if (!FRAME_TYPES.includes(frame.type)) return false;

            if (frame.type === 'spec_translation_pending') {
                const source = (frame.source || '').toString().trim();
                if (session.markSpecPending(source, frame.target_lang).changed) {
                    renderSubtitles();
                }
                return true;
            }

            if (frame.type === 'spec_translation') {
                if (session.applySpecTranslation(frame).changed) renderSubtitles();
                return true;
            }

            if (frame.type === 'refine_result') {
                const state = getState() || {};
                if (session.applyRefineResult(frame, {
                    translateMode: !!state.translateMode,
                }).changed) {
                    renderSubtitles();
                }
                return true;
            }

            if (frame.type === 'subtitle_retract') {
                const sentenceId = frame.sentence_id
                    ? String(frame.sentence_id).trim()
                    : '';
                const result = session.retract(sentenceId);
                if (result.removed) {
                    renderer.invalidateAll();
                    renderSubtitles();
                }
                return true;
            }

            if (frame.type === 'clear') {
                if (frame.preserve_existing) {
                    logger.log('Finalizing pending subtitles before restart...');
                    finalizeCurrentNonFinalTokens();
                    const revealed = session.revealPendingForRestart();
                    if (revealed.translateChanged) renderSubtitles();
                    if (revealed.hybridChanged) renderSubtitles();
                } else {
                    logger.log('Clearing all subtitles...');
                    clearSubtitleState();
                }
                return true;
            }

            session.applyUpdateFrame(frame);
            renderSubtitles();
            return true;
        }

        return { handle };
    }

    const api = { FRAME_TYPES, create };
    root.SubtitleFrameController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

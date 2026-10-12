// Invariants over backend output frames replayed through the real frontend
// session/render model. Shared by tests/frontend/segmentation-replay.test.js
// and scripts/check-segmentation-frames.js (ad-hoc audits of recordings).
const TokenStream = require('../../../static/js/token-stream');
const RenderModel = require('../../../static/js/render-model');
const RefineState = require('../../../static/js/refine-state');
const SubtitleSession = require('../../../static/js/subtitle-session');

function originalText(sentence) {
    return sentence.originalTokens.map((token) => token.text || '').join('');
}

// Once a sentence line is complete (every token final, closed by a
// separator, and not the speaker's latest line), its original text never
// changes and the line never disappears. Interrupt repair is the one
// sanctioned exception: it retracts the line via a subtitle_retract frame.
function findChangedFinalLines(frames) {
    const session = SubtitleSession.create({ TokenStream, RenderModel, RefineState });
    const closed = new Map();
    const violations = [];
    frames.forEach((frame, index) => {
        if (frame.type === 'subtitle_retract') {
            session.retract(frame.sentence_id);
            for (const [key, entry] of closed) {
                if (entry.sentenceId === String(frame.sentence_id)) closed.delete(key);
            }
            return;
        }
        if (frame.type !== 'update') return;
        session.applyUpdateFrame(frame);
        const tokens = session.buildRenderTokens();
        session.assignRenderTokenSequences(tokens);
        const sentences = RenderModel.buildRenderModel({ tokens, displayMode: 'both' })
            .sentences.filter((sentence) => sentence.originalTokens.length);
        const present = new Map(sentences.map((sentence) => [
            RenderModel.getSentenceRenderKey(sentence, 0),
            originalText(sentence),
        ]));
        for (const [key, entry] of closed) {
            const now = present.get(key);
            if (now === entry.text) continue;
            violations.push({
                frame: index,
                before: entry.text.slice(-60),
                after: now === undefined ? '<line gone>' : now.slice(-60),
            });
            closed.delete(key);
        }
        const latestBySpeaker = new Map(sentences.map((sentence) => [sentence.speaker, sentence]));
        for (const sentence of sentences) {
            if (!sentence.isClosed || latestBySpeaker.get(sentence.speaker) === sentence) continue;
            const allFinal = sentence.originalTokens.every((token) => token.is_final)
                && sentence.translationTokens.every((token) => token.is_final);
            if (!allFinal) continue;
            const key = RenderModel.getSentenceRenderKey(sentence, 0);
            if (!closed.has(key)) {
                closed.set(key, {
                    text: originalText(sentence),
                    sentenceId: String(sentence.llmSentenceId || ''),
                });
            }
        }
    });
    return violations;
}

// While text is non-final the frontend splits lines speculatively
// (RenderModel.shouldSpeculativelySplitAfterToken). If the backend does not
// break at the same place once the text is final, the viewer sees the line
// split and then glued back together. Walk each speaker's final source
// tokens and report every speculative split point without a backend break.
// Breaks the backend adds on its own (endpoints, translation boundaries)
// are returned separately: they are not a rejoin, the line just splits late.
function findSplitBoundaryMismatches(frames) {
    const rejoined = [];
    const lateSplits = [];
    const contexts = new Map();
    const previous = new Map();
    let ordinal = 0;
    const visit = (token) => {
        if (token.is_separator) {
            for (const entry of previous.values()) {
                if (!entry.separatorType) entry.separatorType = token.separator_type || 'separator';
            }
            contexts.clear();
            return;
        }
        if ((token.translation_status || 'original') === 'translation') return;
        ordinal += 1;
        const speaker = String(token.speaker);
        const before = previous.get(speaker);
        if (before) {
            const wouldSplit = RenderModel.shouldSpeculativelySplitAfterToken(
                before.text,
                token.text,
                before.context,
            );
            if (wouldSplit && !before.separatorType) {
                rejoined.push({ token: ordinal, before: before.context.slice(-50), next: token.text });
            } else if (!wouldSplit && before.separatorType && before.separatorType !== 'speaker_change') {
                lateSplits.push({
                    token: ordinal,
                    separator: before.separatorType,
                    before: before.context.slice(-40),
                    next: token.text,
                });
            }
        }
        const context = `${contexts.get(speaker) || ''}${token.text || ''}`;
        contexts.set(speaker, context);
        previous.set(speaker, { text: token.text, context, separatorType: null });
    };
    for (const frame of frames) {
        if (frame.type === 'update') (frame.final_tokens || []).forEach(visit);
    }
    return { rejoined, lateSplits };
}

module.exports = { findChangedFinalLines, findSplitBoundaryMismatches };

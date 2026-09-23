# Realtime Independent Caption Lines Design

## Problem

The SteamVR overlay currently consumes `final_tokens` but ignores the desktop
WebSocket's production `non_final_tokens` field. As a result, live source speech
does not reach the overlay. It becomes visible only after the desktop pipeline
finalizes it, which can be triggered by later translation or sentence activity.

The existing regression test sends an idealized source `final_tokens` frame
followed by `refine_result`. It proves JSON parsing in isolation, but it does not
replay the production frame shape or prove that a texture is submitted before a
next sentence arrives.

The current Rust contract also describes source as the primary/top row and
translation as the secondary/bottom row. The product contract is the opposite:
translation is displayed above source.

## Goals

- Display the current source text as soon as it appears in
  `non_final_tokens`, including revisions to an in-progress utterance.
- Update translation independently whenever displayable translation data
  arrives from live tokens, final tokens, or `refine_result`.
- Render translation above source.
- Submit a changed caption frame during the same runtime event turn. A later
  source sentence must never be required to reveal an earlier translation.
- Correlate completed refinements by `sentence_id` when available and prevent
  stale results from replacing a newer sentence.
- Make the production event path reproducible and diagnosable without an HMD.
- Keep every changed path under `vr_overlay/`.

## Non-goals

- Changing the Python producers, frontend renderer, or desktop subtitle model.
- Replacing sentence segmentation or translation policy.
- Proving physical SteamVR/HMD visibility with automated tests.
- Preserving the legacy source-above-translation row order.

## Recommended Architecture

Extract desktop caption protocol handling into a pure state reducer owned by
the Rust bridge. The reducer consumes one complete desktop JSON message at a
time and returns a caption change or a no-op. Network I/O remains responsible
only for WebSocket framing and JSON decoding; the runtime remains responsible
for presentation state and frame submission.

The resulting path is:

```text
/ws JSON frame
  -> desktop protocol reducer
  -> independent translation/source state
  -> CaptionUpdate
  -> OverlayRuntime
  -> renderer
  -> OverlayFrameSubmitter::submit_frame
```

This boundary makes real frame sequences testable without TCP, OpenVR, or font
rendering. A separate integration test retains those downstream boundaries up
to a recording frame submitter.

## Reducer Data Model

The pure reducer owns protocol state only. The implementation uses the
following conceptual structures (Rust names and field types are normative;
private helper layout may vary without changing behavior):

```rust
struct DesktopCaptionReducer {
    source: CaptionLineState,
    translation: CaptionLineState,
    source_final: FinalAccumulator,
    translation_final: FinalAccumulator,
    sentence_ordinals: HashMap<String, u64>,
    recent_source_by_sentence: VecDeque<(SentenceRef, String)>,
    next_sentence_ordinal: u64,
    previous_update_final_tokens: Option<FinalTokensFingerprint>,
}

struct CaptionLineState {
    visible_text: String,
    owner: Option<SentenceRef>,
}

struct FinalAccumulator {
    text: String,
    owner: Option<SentenceRef>,
    replace_on_next_token: bool,
}

#[derive(Clone, Eq, PartialEq)]
struct SentenceRef {
    id: String,
    ordinal: u64,
}

struct CaptionChange {
    source: Option<String>,
    translation: Option<String>,
}
```

`CaptionChange::source` and `CaptionChange::translation` are independent.
`None` means preserve that row; `Some(text)` means replace it. `clear` remains
an explicit reducer outcome rather than overloading two empty strings.

`CaptionLineState::owner` identifies the sentence represented by that visible
row. Most importantly, advancing `source.owner` never mutates
`translation.owner`. `FinalAccumulator` is separate from visible text because
a live draft may be visible while final deltas for the same line are still
being accumulated.

The ordinal registry lasts until `clear`. `recent_source_by_sentence` is capped
at 256 entries and exists only for source-text correlation fallback. A final
tokens fingerprint is the canonical ordered tuple of every token's separator,
line kind, text, sentence ID, and finality fields; it is not a randomized
process hash.

## State Transition Table

Transitions are applied in message order. Within `update`, final tokens are
processed in array order before the non-final snapshot; a line changed by final
tokens in that frame is not overwritten by its draft from the same frame.

| Input | Guard | Source result | Translation result | Other state |
| --- | --- | --- | --- | --- |
| source final delta | current/newer owner | replace or append per final accumulation rules | preserve | register owner and source text |
| translation final delta | current/newer owner | preserve | replace or append per final accumulation rules | set translation owner |
| separator | always | preserve visible source | preserve visible translation | set both final accumulators to replace on next token |
| source non-final snapshot | no source final change in same frame; non-empty | replace visible source | preserve | owner is matching ID when present, otherwise unchanged/unknown |
| translation non-final snapshot | no translation final change in same frame; non-empty | preserve | replace visible translation | set owner when ID is present |
| `refine_result` | same owner as translation line | preserve | replace with completed translation | preserve both owners |
| `refine_result` | known newer than translation owner | preserve | replace with completed translation | advance translation owner |
| `refine_result` | known older than translation owner | preserve | preserve | emit stale no-op reason |
| `refine_result` | translation owner is A, source owner is B, refine owner is A | preserve source B | replace translation A | source B does not make A stale |
| duplicate consecutive `final_tokens` array | fingerprint matches previous update | do not re-accumulate finals; still process source draft | do not re-accumulate finals; still process translation draft | retain replay fingerprint |
| `clear` | always | clear | clear | reset accumulators, owners, ordinals, correlation cache, and fingerprint |

The reducer emits at most one `CaptionChange` per desktop message, containing
the final values reached after all transitions in that message. It emits a
no-op when neither visible row changed.

## Protocol Semantics

### `update.final_tokens`

`final_tokens` is an ordered incremental list: each non-separator entry is a
new token delta unless its text is demonstrably a cumulative whole-line replay
under the rules below. The reducer processes the array from index zero to the
end, once per WebSocket frame. A token with
`translation_status == "translation"` targets the translation accumulator;
every other non-separator token targets the source accumulator.

Each line accumulator stores `text`, optional `sentence_id`, and
`replace_on_next_token`. The reducer applies each target token as follows:

1. Ignore it when `is_final == false` or `text` is empty.
2. Read a non-empty `llm_sentence_id` as the token's sentence ID.
3. If `replace_on_next_token` is set, replace the accumulator with `text`, set
   its sentence ID to the token ID, and clear the flag.
4. Otherwise, if both IDs exist and differ, use the sentence-order registry:
   replace for a newer sentence and reject a token for an older sentence.
5. Otherwise, if `text` strictly extends `accumulator.text` by starting with it
   and being longer, replace the accumulator with `text`; this is a longer
   cumulative replay.
6. Otherwise, append `text` exactly, even when it equals the accumulator or the
   preceding token.
   Repeated words are valid content and must not be deduplicated by suffix.

The reducer additionally remembers the canonical serialized value of the
`final_tokens` array from the immediately preceding desktop `update`. If the
next desktop message is another `update` with the same array, final-token
accumulation is skipped while its `non_final_tokens` snapshot is still
processed. Any intervening desktop message, including `refine_result` or
`clear`, breaks this replay window. The memory is one message deep so a later
legitimate identical utterance is not suppressed. Array identity includes
token order, line kind, text, sentence ID, finality, and separators.

A separator sets `replace_on_next_token` on both accumulators. It does not clear
either visible line. Because the two flags are consumed independently, a new
source can replace the source while the translation row continues to represent
the previous sentence.

### `update.non_final_tokens`

`non_final_tokens` is a snapshot of current in-progress recognition, not an
incremental delta. On every `update`, source and translation drafts are rebuilt
separately from the snapshot.

- A non-empty source draft immediately becomes the visible source line.
- A non-empty translation draft immediately becomes the visible translation
  line.
- Absence of one kind does not clear or delay the other visible line.
- Revisions replace the previous draft rather than append to it.
- When final tokens for a line arrive in the same frame, the finalized value is
  authoritative for that line.

This design does not treat an empty draft snapshot as an instruction to blank a
visible line. Lines persist until replacement, separator-driven rollover, or an
explicit `clear`.

### `refine_result`

A non-empty `refined_translation` replaces the translation line immediately.
When `no_change` is true, the existing translation is retained. If the refined
value is absent and `no_change` is false, a non-empty `original_translation`
may supply the completed translation.

Correlation uses `sentence_id` as the primary identity. Source text is a
compatibility fallback for frames that omit the identifier. Staleness is
measured relative to the sentence currently represented by the translation
line, not the current source sentence. This distinction permits the source line
to advance while a late refinement still completes the translation line for
the preceding sentence. Translation acceptance does not wait for another
`update`.

### `clear`

`clear` resets finalized text, drafts, correlation identity, rollover flags,
and both visible lines. The runtime submits the empty presentation through its
existing idle-hide behavior.

## Presentation Contract

The overlay contains one caption block with two independently replaceable
visual rows:

1. Top row: translation.
2. Bottom row: source/original speech.

The protocol state should use domain names (`source`, `translation`) rather
than positional names. Positional mapping happens once when constructing the
renderer block: `primary_text = translation` and `secondary_text = source`.

Any visible change sets the runtime redraw latch. The event loop handles one
bridge event and drains that latch before reading the next event. Therefore a
translation-only event results in a new submitted texture in the same event
turn.

## Stale and Out-of-order Events

- The reducer assigns a monotonically increasing local ordinal when it first
  observes each non-empty `sentence_id`. IDs remain opaque strings; their text
  is never parsed to infer order.
- The translation line stores the sentence ID and ordinal of the sentence it
  currently represents. A translation token or refinement with the same owner
  updates that line. A known greater ordinal advances it. A known lower ordinal
  is stale and is rejected.
- Advancing the source line alone does not change the translation owner and
  cannot make a result stale.
- Source and translation tokens register IDs in first-observed order. A
  refinement with an unknown ID does not automatically register itself: when a
  translation owner exists it is rejected as uncorrelated; when no owner exists
  it may claim the line only if its normalized source uniquely matches the
  visible source.
- The reducer retains the ID-to-ordinal registry until `clear`, so an old known
  ID cannot become apparently new through eviction. It retains normalized
  source text only for the 256 most recently observed sentences. A legacy
  refinement without an ID is accepted only when
  its normalized source uniquely matches the source associated with the
  translation owner, or, when no translation owner exists, the visible source.
  Ambiguous legacy results are rejected.
- Only an immediately repeated identical `final_tokens` array is deduplicated
  at frame level. Within a non-replayed frame, delta tokens append exactly;
  equality/prefix checks apply only against the whole accumulated line.
- Draft snapshots replace drafts and are never appended as deltas.
- A refinement carrying the current `sentence_id` is accepted regardless of
  harmless source formatting differences.
- A source-only update preserves translation; a translation-only update
  preserves source.

## Diagnostics

Detailed logging records one structured line at each causal boundary:

- received desktop frame sequence and message type;
- reducer outcome, changed line flags, sentence ID, and rejection reason;
- runtime revision and redraw-latch transition;
- frame submission sequence with redacted text fingerprints and lengths.

Basic logging avoids subtitle content. Detailed logging may include bounded,
escaped previews consistent with the existing logging-mode contract. Logging
must not alter state transitions or block frame submission.

## Test Strategy

### Production-frame fixtures

Store a small, hand-reviewed JSONL fixture under `vr_overlay/tests/fixtures/`.
It uses the exact field names and snapshot/delta semantics emitted by both
desktop providers. The decisive sequence ends after the first refinement; it
contains no next sentence.

### Reducer tests

Tests cover:

- live source from `non_final_tokens` appears immediately;
- live source revision replaces instead of appends;
- live translation updates independently;
- final tokens override drafts for the corresponding line;
- two identical consecutive final-token frames are idempotent;
- repeated equal token deltas inside one frame remain repeated text;
- a longer whole-line replay replaces rather than appends;
- separator rollover is independent per line;
- current-ID refinement is accepted immediately;
- a refinement for the translation owner's sentence remains accepted after the
  source line advances;
- a refinement older than the translation owner is rejected;
- legacy source fallback remains safe;
- duplicate frames are no-ops;
- `clear` resets all protocol state.

The named regression `refine_for_translation_a_is_accepted_after_source_b`
must execute these states explicitly:

| Step | Event | Visible translation owner/text | Visible source owner/text |
| --- | --- | --- | --- |
| 1 | source A arrives | none / empty | A / source A |
| 2 | translation A arrives | A / draft translation A | A / source A |
| 3 | source B arrives | A / draft translation A | B / source B |
| 4 | refined translation A arrives | A / refined translation A | B / source B |

Step 4 must produce `CaptionChange { translation: Some(refined A), source:
None }`. A test that only inspects bridge parsing, or that introduces
translation B before step 4, does not cover this regression.

### End-to-end runtime regression

A scripted local WebSocket replays the production fixture through
`BridgeClient`, `OverlayRuntime`, the renderer, and a recording submitter. The
test asserts that a submitted frame contains top-row translation and bottom-row
source before the server sends a next sentence or closes the connection.

The regression follows red-green verification: it must fail on the current
implementation for the expected missing/late source behavior, pass after the
minimal fix, fail again when the fix is temporarily reverted, and pass when
restored.

### HMD test

Automated tests stop at the OpenVR submission interface. Final review still
requires one physical HMD smoke test that speaks a single sentence, waits in
silence, and confirms that both rows remain visible without a second sentence.

## AI Coding Infrastructure

Add `vr_overlay/scripts/verify.ps1` as the single local verification entrypoint.
It performs environment preflight, formatting validation, Rust tests, release
build, diff whitespace validation, and the changed-path boundary check. It
fails if any changed path is outside `vr_overlay/`.

Keep the protocol invariants and verification command documented in
`vr_overlay/AGENTS.md`, including the production meanings of `final_tokens` and
`non_final_tokens`, row order, and the physical-HMD limitation. Update the
README with the same user-visible contract and troubleshooting entrypoint.

Because the PR boundary excludes `.github/`, repository CI configuration is not
changed in this work. The verification script is suitable for a later CI call
once expanding the boundary is explicitly approved.

The preflight reports missing or inaccessible MSVC, Windows SDK, CMake, Cargo,
and OpenVR prerequisites before starting a long build. This prevents an
environment failure from being confused with a subtitle regression.

## Acceptance Criteria

- Given an `update` containing only source `non_final_tokens`, the next
  submitted frame displays that source on the bottom row.
- Given a refinement for that sentence and no later source sentence, the next
  submitted frame displays the translation on the top row and preserves the
  source on the bottom row.
- Either line can change without clearing, delaying, or replacing the other.
- A stale refinement cannot roll back the current translation.
- The production-frame reducer and end-to-end regression tests pass.
- `vr_overlay/scripts/verify.ps1` distinguishes environment-preflight failures
  from test failures.
- The repository completion gate passes and `git diff --name-only
  origin/main...HEAD` contains only `vr_overlay/`.
- A physical HMD smoke test remains explicitly reported as manual evidence.

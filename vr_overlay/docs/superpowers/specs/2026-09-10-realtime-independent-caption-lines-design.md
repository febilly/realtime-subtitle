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

## Protocol Semantics

### `update.final_tokens`

`final_tokens` is an incremental list. Non-separator tokens with
`translation_status == "translation"` append to the finalized translation;
other non-separator tokens append to the finalized source. Duplicate replay and
whole-line replay remain tolerated.

A separator closes the current sentence pair. It does not clear either visible
line. The next incoming content for each line replaces that line independently.

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
compatibility fallback for frames that omit the identifier. A refinement for a
sentence older than the current source sentence is ignored and records a
diagnostic reason. Translation acceptance does not wait for another `update`.

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

- Duplicate token replay is idempotent.
- Draft snapshots replace drafts and are never appended as deltas.
- A refinement carrying the current `sentence_id` is accepted regardless of
  harmless source formatting differences.
- A refinement carrying an older known `sentence_id` is rejected.
- For legacy refinements without an ID, normalized source comparison is used;
  ambiguous legacy results are rejected instead of rolling back visible text.
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
- separator rollover is independent per line;
- current-ID refinement is accepted immediately;
- stale-ID refinement is rejected;
- legacy source fallback remains safe;
- duplicate frames are no-ops;
- `clear` resets all protocol state.

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

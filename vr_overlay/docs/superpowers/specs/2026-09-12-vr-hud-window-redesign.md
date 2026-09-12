# VR HUD Window Redesign

**Date:** 2026-09-12
**Status:** Proposed for implementation planning
**Scope:** `vr_overlay/` only

## Summary

Rin remains a native Rust SteamVR runtime built as one `RinBridgeOverlay.exe`.
It continues to reuse the existing D3D11, DirectWrite, font fallback, glyph
cache, and OpenVR overlay implementation. The product-facing subtitle flow is
rebuilt around the desktop's raw `/ws` stream.

The new flow has one shared live-input row and three display projections that
directly mirror the desktop connection panel's existing modes:

- `original`: one upper row per Soniox speaker, showing source text;
- `translation`: one upper row per Soniox speaker, showing translated text;
- `both`: one or two strict source/translation sentence pairs, ignoring
  speakers for layout.

The design prioritizes low latency, low steady-state CPU/GPU cost, predictable
information placement, and correct sentence ownership. It does not attempt to
infer which speech is semantically important.

## Goals

1. Make the VR HUD usable in both one-to-one conversations and noisy social
   gatherings without presenting a translation as belonging to the wrong
   source sentence.
2. Show live ASR source text immediately in every display mode so the user can
   tell that somebody is speaking and recognition is active.
3. Keep visible information bounded and predictable in the HMD comfort area.
4. Use Soniox diarization now in single-language projections while providing a
   deterministic anonymous-speaker fallback.
5. Preserve the current head-locked, downward-view placement.
6. Render only when projected visual content changes and avoid a continuous
   60/90 Hz application redraw loop.
7. Produce one embeddable runtime executable. The integrated product has one
   user entry point: `RealtimeSubtitle.exe` launches and owns Rin as a child
   process.
8. Keep the submitted PR clean: every changed path relative to `origin/main`
   remains below `vr_overlay/`.

## Non-goals

- Semantic importance scoring or LLM-based display prioritization.
- Concatenating short acknowledgements into another person's sentence.
- Persistent real-world speaker identity or user-assigned speaker names.
- A scrollable transcript inside the normal VR HUD.
- Per-frame head-motion smoothing or dead-zone tracking.
- Replacing OpenVR with OpenXR in this change.
- Chromium, HTML, DOM, or CEF-based rendering.
- Reworking the desktop application's own scrolling subtitle renderer.
- Shipping user-operated launcher scripts or requiring the user to start Rin
  separately.

## Product Model

### One user-facing application, one child runtime

The final integrated distribution has one user-facing executable:
`RealtimeSubtitle.exe`. The desktop process bundles, extracts, configures,
starts, monitors, and terminates `RinBridgeOverlay.exe`. Rin is an internal
runtime rather than a second application the user manages.

The Rust-only PR delivers the runtime and its integration contract. Desktop
parent-process changes are a separate integration change and are not committed
in this PR. Temporary launchers or harnesses may be used during engineering,
but they must not be present in the final changed-path set.

Rin continues to accept a single startup configuration envelope. The parent
may materialize that envelope as an ephemeral manifest; it is not a shipped or
user-edited artifact. The configuration includes the desktop WebSocket URL,
parent PID, initial view settings, logging location, and overlay instance ID.

### Product protocol

The only caption protocol in the new runtime is the desktop's unauthenticated
local `/ws` stream. Rin consumes these production event families:

- `update` with incremental `final_tokens` and snapshot-like
  `non_final_tokens`;
- `refine_result` correlated by `sentence_id`;
- `clear`, including `preserve_existing` semantics;
- view-setting control events defined by the integration contract;
- unrelated desktop events, which are ignored without mutating caption state.

The `/vr_ws` authenticated snapshot path, snapshot presentation types, and
snapshot compatibility branches are removed. The implementation updates
`vr_overlay/AGENTS.md` and the README so the repository contract matches this
single product path.

## Module Design

The rewrite creates four deep modules with narrow interfaces.

### Desktop protocol adapter

The protocol adapter owns WebSocket framing, JSON validation, raw token replay
rules, and conversion into typed caption events. Callers do not inspect
`serde_json::Value` or know the desktop's token-level wire format.

Conceptual interface:

```rust
enum CaptionEvent {
    SourceLive(LiveSourceSnapshot),
    SourceCommitted(CommittedSource),
    TargetDraft(TargetUpdate),
    TargetCommitted(TargetUpdate),
    RefinedTarget(TargetUpdate),
    Clear { preserve_existing: bool },
    ViewSettingsChanged(VrViewSettings),
    Activity,
}

async fn next_event(&mut self) -> Result<CaptionEvent, BridgeError>;
```

The adapter preserves exact token order. Equal tokens in one frame remain
equal text; only an immediately repeated identical final-token frame is
deduplicated. A longer cumulative prefix replaces the corresponding
accumulator, while a true delta appends.

Final tokens are segmented by `(sentence_id, speaker, track)` where `track` is
source or translation. There is no global source or translation accumulator; a
frame carrying several speakers' tokens must never concatenate them into one
line. A translation draft without a `sentence_id` is emitted with a null id;
the reducer binds it to the same speaker's currently open sentence and discards
it only when that speaker has no open sentence.

For `non_final_tokens` the adapter emits one `SourceLive` per frame (rule 3
above) and one `TargetDraft` per translation-speaking speaker. `TextTrack`
carries its own `phase`, so the phase is part of the track value, not a
separate record field.

### Transcript reducer

The transcript reducer owns sentence identity, source order, source/target
correlation, per-speaker recency, draft-to-final upgrades, and stale-result
filtering. It is pure with respect to rendering and OpenVR.

```rust
struct SentenceKey {
    local_ordinal: u64,
    upstream_id: Option<String>,
}

struct SentenceRecord {
    key: SentenceKey,
    speaker: SpeakerKey,
    source: TextTrack,
    target: TextTrack,
}

enum LiveInputRow {
    Hidden,
    Streaming(LiveSourceSnapshot),
    Settled {
        snapshot: LiveSourceSnapshot,
        closed_at: Instant,
    },
}

struct TranscriptState {
    sentences: VecDeque<SentenceRecord>,
    live_input: LiveInputRow,
    speaker_recency: Vec<SpeakerKey>,
}
```

`SpeakerKey` is a session-scoped Soniox diarization value. It is not treated as
a durable human identity. When diarization is disabled, unavailable, or the
provider does not support it, all tokens use one `Anonymous` speaker key.

An upstream `llm_sentence_id` is authoritative once present. Before it is
available, the reducer assigns a monotonic local ordinal. Later frames bind the
upstream ID to that existing record rather than creating a second sentence.
The reducer retains a bounded recent ledger of 64 records so late target and
refinement events can find their source sentence without unbounded memory.

### Projection module

The projection module converts the complete transcript state into a bounded HUD
content frame. Mode switching does not discard transcript data.

```rust
enum HudRowRole {
    UpperPrimary,
    UpperSecondary,
    LiveSource,
}

enum HudRowState {
    Draft,
    Settled,
}

struct HudRow {
    role: HudRowRole,
    /// Body text only. The speaker label is never concatenated here.
    text: String,
    speaker_label: Option<String>,
    language: Option<String>,
    state: HudRowState,
    sentence: Option<SentenceKey>,
}

/// Five fixed physical slots. Slot 4 is always the universal live row; the
/// projector never emits a dense window that would move it.
struct HudContentFrame {
    slots: [Option<HudRow>; 5],
}

fn project(
    state: &TranscriptState,
    settings: &VrViewSettings,
    now: Instant,
) -> HudContentFrame;
```

There are three explicit projection implementations: original speaker window,
translation speaker window, and bilingual sentence-pair window. They share row
types and layout limits but not scheduling logic.

### Runtime coordinator

The runtime coordinator owns the event loop, projection invalidation, render
submission, visibility timer, reconnect behavior, parent lifetime, and
diagnostics. It does not parse raw desktop tokens or decide sentence ownership.

The coordinator drains all messages already waiting in the WebSocket receive
buffer, reduces them in order, and renders only the final projected state. It
does not wait for a future batching deadline, so coalescing adds no artificial
latency. If the projection is byte-for-byte unchanged, no DirectWrite layout,
D3D draw, or OpenVR texture submission occurs.

## Universal Live-input Row

Every display mode reserves the bottom row for current ASR source input. This
row replaces a generic `Listening` indicator.

Rules:

1. The row appears on the first non-empty source token with no fade-in.
2. `update.non_final_tokens` is a replaceable live snapshot, not an append-only
   stream.
3. Per frame the protocol adapter selects the contiguous source fragment of the
   **last** speaker appearing in `non_final_tokens` and emits exactly one
   `SourceLive` carrying that speaker and fragment. The reducer holds at most
   one live row; there is no per-speaker live fan-out and no reliance on a
   later event happening to overwrite an earlier one.
4. The row shows the newest readable tail when text exceeds its fixed width;
   leading content is replaced by a leading ellipsis.
5. The optional speaker label is carried on the row as its own field. The
   renderer alone composes and measures it; `HudRow.text` is always the body
   only. There is exactly one source of truth. In `both` the label is always
   absent (`None`).
6. The `both` projection ignores speaker identity and speaker labels entirely.
7. The row never closes because `non_final_tokens` became empty. An empty
   translation snapshot or any unrelated empty array must not settle the row.
   Only that same sentence's source commit, or an explicit end event for it,
   transitions `Streaming` to `Settled`.
8. The live-input row is not counted as a speaker result row or a bilingual
   sentence pair.

The row has an explicit state, not a boolean:

```rust
enum LiveInputRow {
    Hidden,
    Streaming(LiveSourceSnapshot),
    Settled {
        snapshot: LiveSourceSnapshot,
        closed_at: Instant,
    },
}
```

```text
Hidden -> Streaming -> Settled -> Hidden
                 \-> Streaming (a newer source sentence replaces immediately)
```

`Settled` lasts at least 1.2 seconds unless a newer source sentence replaces
the row. After that minimum, the row clears only when its information has a
visible handoff destination:

- in `original`, the committed source is visible in its upper speaker row;
- in `translation`, a target draft or final for the same sentence is visible in
  the owning upper speaker row;
- in `both`, the strict pair containing the same source is visible above.

If no valid handoff appears, the settled source remains until new input replaces
it or the global silence controller fades and hides the overlay. This prevents
both an instantaneous disappearance and an indefinitely duplicated source row.

## Display Projections

### Original mode: per-speaker source window

The upper region is an LRU window keyed by `SpeakerKey`.

- One speaker can occupy at most one upper row.
- A committed source result updates that speaker's existing row rather than
  adding another row.
- Updating a speaker moves that row to the newest position at the bottom of the
  upper region while preserving all other speakers' relative order.
- A new speaker enters at the newest position. If the configured capacity is
  full, the least recently updated speaker row is removed from the top.
- A result occupies exactly one physical line. It never wraps. Overflow keeps
  the beginning and uses a trailing ellipsis.
- Live source remains in the universal bottom row and may temporarily coexist
  with that speaker's previous committed upper result. On ASR close, the final
  source is retained through the common handoff lifecycle rather than cleared
  immediately.

The `max_speakers` setting accepts 1 through 3 and defaults to 3. The default
maximum visible load is therefore three settled speaker rows plus one live
input row.

Example after completed activity `A -> B -> C -> A`:

```text
B latest committed source
C latest committed source
A latest committed source
<current live source, only while present>
```

There is one upper row for A, not one row per A sentence.

### Translation mode: per-speaker target window

The upper region uses the same LRU speaker-row policy as original mode, but a
speaker row contains that speaker's latest available target text.

- The first non-empty target draft appears immediately in the speaker's row.
- Later draft text replaces it in place.
- Target commit and `refine_result` upgrade the same row in place.
- Draft and final phases never create separate rows.
- Target arrival order does not define sentence order. Updates first resolve
  through `sentence_id` and source ordinal, then update the owning speaker.
- A stale target for an older sentence must not overwrite a newer target
  already owned by that speaker's row.
- Each upper result occupies one physical line with trailing ellipsis on
  overflow.
- The bottom source row appears during input. On ASR close it remains settled
  until the same sentence has a visible target handoff, the minimum hold has
  elapsed, and then it clears; if no target appears, global silence handles it.

Translation mode shares the `max_speakers` setting with original mode.

### Both mode: strict bilingual sentence-pair window

Both mode does not use speaker identity for selection, ordering, labels, or row
ownership. The upper region contains a source-ordered window of strict
sentence pairs.

- A pair is keyed by `SentenceKey`.
- Its target and source rows must resolve to the same sentence record.
- A pair becomes visible as soon as it has source text and a non-empty target
  draft. It does not wait for target commit.
- Later draft, committed, and refined targets replace the target row in place.
- A translation can never be attached to whichever source happens to be newest.
- Each visible pair uses two physical rows: target above, source below.
- Each track is limited to one physical line. Overflow uses a trailing
  ellipsis.
- Pairs are ordered by source ordinal, never target arrival time.
- When capacity is exceeded, the oldest visible pair is removed.
- The universal live-input row remains below the pair window. It may duplicate
  the current pair's source during the bounded post-close handoff; this is
  intentional and ends only after the strict pair is visibly established.

`bilingual_pair_count` accepts 1 or 2 and defaults to 1. With the default, the
maximum visible load is one two-line pair plus the live row. Selecting two
pairs allows four upper rows and a transient fifth live row.

## View Settings and Desktop Synchronization

The desktop connection panel is the sole owner of user preferences. Rin does
not persist an independent copy or expose a second settings UI.

```rust
enum DisplayMode {
    Both,
    Original,
    Translation,
}

struct VrViewSettings {
    display_mode: DisplayMode,       // existing both/original/translation
    max_speakers: u8,                // 1..=3, default 3
    bilingual_pair_count: u8,        // 1..=2, default 1
    show_speaker_labels: bool,
}
```

The startup envelope carries the initial values. The integrated desktop then
broadcasts one normalized control frame whenever the panel changes:

```json
{
  "type": "vr_view_settings",
  "display_mode": "both",
  "max_speakers": 3,
  "bilingual_pair_count": 1,
  "show_speaker_labels": true
}
```

Rin validates ranges, applies the complete settings object atomically, and
reprojects the existing transcript state once. Invalid control frames are
logged and ignored. The desktop's current display setting is frontend-local;
promoting it to shared backend state and broadcasting the control frame belongs
to the later desktop integration change, outside this Rust-only PR.

## Layout and Visual Style

The HUD keeps the established minimalist subtitle appearance:

- transparent overlay background by default;
- pure white text with a scale-aware black outline;
- draft target text uses reduced opacity but retains the outline;
- no cards, colored badges, animated listening dots, or opaque black panel;
- the overlay surface remains centered in the existing downward comfort area;
- rows are left-aligned inside a fixed-width text region so streaming growth
  does not move previously drawn glyphs horizontally.

The renderer reserves five fixed physical slots, `[Option<HudRow>; 5]`, enough
for two bilingual pairs plus the universal live row. Empty slots remain
transparent and therefore invisible. Fewer visible slots do not resize or
reposition the OpenVR overlay, and the live row never moves:

- the universal live row always occupies slot 4;
- in `original` and `translation`, 1 to 3 speaker rows occupy slots 1 to 3,
  bottom-aligned (one speaker uses slot 3, two use slots 2 and 3, three use
  slots 1, 2, and 3);
- in `both`, one pair occupies slots 2 (target) and 3 (source); two pairs occupy
  slots 0/1 (older pair) and 2/3 (newer pair);
- new content scrolls upward through the fixed slots; the live row itself never
  moves.

The frame interface is therefore a fixed `[Option<HudRow>; 5]`, not a dense
`Vec<HudRow>` whose length changes the live row's position.

Each `HudRow` carries its own `HudRowRole`, `HudRowState` (`Draft`/`Settled`,
for draft dimming and in-place final upgrade), `language: Option<String>` (so
existing font fallback and cache keys keep working), an optional
`speaker_label`, and the owning sentence. Slot placement is decided by the slot
index, never by the row's role.

The existing 4096 by 1056 render target and base text scale remain the starting
point. Upper single-language results use the current primary scale. Bilingual
target rows use the primary scale, source rows use the current secondary scale,
and the universal live row uses the secondary scale. The runtime's configured
text scale multiplies all role sizes together.

The layout engine measures real DirectWrite glyph advances. It applies a small
CJK prohibition set so opening punctuation does not end a line and closing
punctuation does not begin one. The runtime does not add a browser or generic
Unicode line-breaking subsystem.

## Spatial Geometry

The rewrite preserves the current proven placement instead of changing visual
geometry at the same time as caption semantics:

- OpenVR compositor overlay;
- head-locked with `SetOverlayTransformTrackedDeviceRelative`;
- current downward offset and distance defaults;
- current overlay width and text-scale behavior;
- no per-frame IPC or application-side head-pose smoothing.

Configuration can still adjust offset, distance, and scale. A separate headset
calibration study may change those defaults later, but this rewrite does not
claim that one focal distance is optimal for every HMD.

## Rendering and Performance

1. Keep the existing D3D11 device, DirectWrite shaping, font fallback, glyph
   caches, and reusable texture.
2. Replace center-based row layout with fixed left origins for streaming rows.
3. Cache each upper result row by text, role, language, font, and scale.
4. Treat `non_final_tokens` as last-value-wins state. If several frames are
   already queued, reduce all of them and draw only the final projection.
5. Do not create a periodic 60/90 Hz subtitle redraw timer.
6. Do not resubmit the overlay texture when the projected frame is unchanged.
7. Keep basic logging free of subtitle text. Detailed mode may record event
   type, speaker key hash, sentence ordinal, dirty rows, render duration, and
   submission sequence.

The SteamVR compositor retains and places the submitted overlay texture. HMD
tracking therefore does not require Rin to rerender unchanged text every
display frame.

## Silence and Visibility

Visibility timing is not part of the transcript reducer. A separate runtime
controller uses a monotonic clock.

```text
any live source token -> alpha 100% immediately; cancel silence timers
any new visible target/refinement -> alpha 100% immediately; cancel timers
no new activity for 4.0 s -> begin fade
fade for 1.2 s -> alpha 0%; hide overlay
new visible caption event during fade/hidden -> alpha 100%; show overlay
```

The fade uses compositor overlay alpha rather than redrawing text opacity into
the D3D texture. This requires a small extension to the existing OpenVR adapter
for `SetOverlayAlpha`; it does not replace the OpenVR implementation. Fade
updates exist only during the 1.2-second transition.

The universal live row does not disappear when the raw non-final source snapshot
first becomes empty. It transitions `Streaming -> Settled` only on the owning
sentence's source commit or an explicit end event, then follows the
mode-specific handoff rules above. That row lifecycle is distinct from fading
all settled HUD content after global silence.

## Reconnect, Clear, and Parent Lifetime

- Startup retries the local desktop connection until the configured startup
  deadline while the parent PID remains alive.
- After readiness, an unexpected disconnect clears live input and hides the
  overlay before reconnecting with bounded exponential backoff.
- Because raw `/ws` has no initial snapshot, a reconnect clears upper state to
  prevent stale speech from being presented as current.
- `clear { preserve_existing: false }` clears sentences, speaker rows, pairs,
  live input, and visibility state.
- `clear { preserve_existing: true }` preserves settled upper results and the
  sentence IDs required to apply a late refinement to those visible results.
  It clears live input, incomplete drafts, accumulators, and correlation records
  that do not own preserved visible content.
- Rin polls parent liveness at a low frequency and exits cleanly when the
  desktop parent is gone.
- A clean desktop shutdown may also send a shutdown control event; parent death
  remains the final safety net.

## Single-runtime Executable

The publishable Rust artifact is `RinBridgeOverlay.exe`. No launcher script,
manifest template, font bundle, or separately shipped helper executable is
required beside it.

SteamVR remains an environmental dependency. The `openvr_sys` 2.1.3 dependency
statically links OpenVR's client binding (`openvr_api64`); that static loader
reads the registered `openvrpaths.vrpath` and loads the installed SteamVR
`vrclient_x64.dll`. Rin therefore requires no `openvr_api.dll` sidecar and must
not implement a second, manual OpenVR loader. All higher-level overlay calls
remain behind the current OpenVR adapter.

The release directory may contain compiler intermediates and optional PDBs,
but packaging selects only `RinBridgeOverlay.exe` as the runtime artifact. A
copy of the release executable placed in an otherwise empty directory must
reach `main` and answer `--check-startup-contract` without any sidecar DLL.

## Error Handling

- Malformed or unrelated WebSocket frames are ignored with bounded diagnostic
  logging; they do not clear valid HUD state.
- Missing required fields in a caption-bearing token prevent only that token
  from entering state.
- Invalid setting values leave the last valid settings active.
- Failure to initialize SteamVR, find an HMD, create the overlay, render a
  frame, or submit a texture emits one machine-readable startup/runtime event
  and a stable nonzero exit code.
- A render or OpenVR failure is fatal; silently continuing with a frozen HUD is
  unsafe because the user may trust stale text.
- Basic logs never contain recognized or translated text.

## Test Strategy

### Protocol tests

- Production-shaped raw `/ws` fixtures for source live, source commit, target
  draft, target commit, refinement, clear, repeated frames, cumulative prefix,
  true delta, and unrelated events.
- Multiple-speaker Soniox fixtures and anonymous-provider fallback fixtures.
- Out-of-order and late target delivery fixtures.

### Reducer tests

- Local sentence identity later binds to `llm_sentence_id` without duplication.
- A target update can modify only its owning sentence.
- A stale target cannot overwrite a newer sentence for the same speaker.
- The recent ledger remains bounded.
- Clear and reconnect transitions obey their preservation rules.

### Projection tests

- Original sequence `A -> B -> C -> A` yields one row each for B, C, and A,
  with A updated and moved rather than duplicated.
- Original and translation capacities from 1 through 3 evict the least recently
  updated speaker.
- Translation drafts appear immediately and final/refined text upgrades the
  same speaker row.
- Both mode ignores speaker identity, orders by source ordinal, and never
  crosses sentence IDs.
- Both mode shows a draft pair immediately and upgrades it in place.
- Pair capacity 1 or 2 evicts only the oldest pair.
- The live-input row appears in every mode, follows the latest live source, and
  enters `Settled` (not `Hidden`) on ASR close.
- The held source clears only after the 1.2-second minimum and a visible
  mode-specific handoff, while new live input can replace it immediately.
- An empty translation snapshot or an unrelated empty non-final array does not
  settle the live row; only the owning sentence's source commit or an explicit
  end event does.
- A per-frame multi-speaker source snapshot yields exactly one live row for the
  last speaker, with no per-speaker fan-out.
- A missing translation keeps the settled source visible until replacement or
  global silence instead of leaving a blank HUD.
- Long settled rows use trailing ellipsis; a long live row keeps the newest tail
  with leading ellipsis.

### Runtime and renderer tests

- Queued input events coalesce into one render without delaying the first
  available update.
- Unchanged projections do not submit a texture.
- Fixed row bounds do not move as text grows.
- CJK punctuation is not stranded at prohibited line positions.
- Draft and final styles have identical geometry.
- Silence fade and instant wake use compositor alpha without text redraw.
- Parent death, clean shutdown, reconnect, and startup deadline are covered
  with deterministic clocks and fake adapters.
- A copy of the release executable in an otherwise empty temporary directory
  reaches `main` and answers `--check-startup-contract` without an
  `openvr_api.dll` sidecar.
- The release artifact selection contains exactly one `RinBridgeOverlay.exe`.

### Physical HMD acceptance

Automated tests stop at the OpenVR adapter. A real headset test must confirm:

1. the existing downward viewing position is preserved;
2. the first live source appears with no perceptible delay;
3. streaming text does not shift horizontally or move the overlay;
4. each mode matches the connection-panel setting;
5. speaker rows do not duplicate one speaker;
6. both mode never displays a target against the wrong source;
7. idle CPU/GPU use remains negligible and rapid token input does not produce
   visible frame drops;
8. fade, instant wake, desktop exit, and reconnect behave correctly.

## Completion and PR Gate

Before review, run:

```powershell
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
```

The changed-path output must contain only `vr_overlay/`. The packaged runtime
selection must contain one `RinBridgeOverlay.exe`; temporary engineering
launchers and desktop-side probes must be removed before review. Automated
verification does not replace the physical SteamVR/HMD acceptance test.

## Accepted Design Decisions

- Parent-managed architecture A is the integration model.
- Raw `/ws` is the sole caption product path.
- The bottom row is universal live ASR source input in all modes.
- ASR close freezes the bottom source for a minimum 1.2-second handoff; an empty
  non-final snapshot never clears it immediately.
- Original and translation modes allocate one upper row per speaker, not per
  sentence.
- The upper speaker capacity is configurable from 1 through 3.
- Both mode ignores speakers and displays one or two strict sentence pairs.
- Both mode displays a target draft immediately and upgrades it in place.
- Display settings are owned by and synchronized from the desktop connection
  panel.
- The normal HUD does not infer importance and does not scroll an unbounded
  transcript.
- The HUD frame is five fixed physical slots and the live row always occupies
  slot 4; single-language speaker rows are bottom-aligned in slots 1 to 3, and
  bilingual pairs use slots 2 to 3 (one pair) or 0 to 3 (two pairs).
- The live row is explicit state (`Hidden` / `Streaming` / `Settled`) and only
  the owning sentence's source commit or an explicit end settles it.
- Final tokens accumulate per `(sentence_id, speaker, track)`; there is no
  global source/translation accumulator.
- The renderer is the sole composer of the speaker label; `HudRow.text` never
  contains it.
- OpenVR is statically linked through `openvr_sys`; no `openvr_api.dll` sidecar
  is shipped or required.
- The existing native renderer and current downward placement are retained.

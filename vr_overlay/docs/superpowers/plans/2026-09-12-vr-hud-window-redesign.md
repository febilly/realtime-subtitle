# VR HUD Window Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Rin VR runtime around the desktop's raw `/ws` stream with three
speaker-aware/bilingual HUD projections, a fixed five-slot frame whose slot 4 is always the
universal live row, a change-only render path, and compositor-alpha silence fading.

**Architecture:** Four deep modules with narrow interfaces — a pure transcript reducer
(`transcript.rs`, the domain types), a desktop protocol adapter (`protocol.rs`), a projection
module (`projection.rs`), and a runtime coordinator (`runtime.rs`). Task order follows the
dependency chain: **domain types + reducer -> protocol adapter -> projection -> renderer ->
coordinator**. The adapter owns WebSocket framing and raw token replay; the reducer owns
sentence identity/correlation; the projection owns the three display windows; the coordinator
owns the event loop, invalidation, visibility, reconnect, and parent lifetime. The existing
D3D11/DirectWrite/OpenVR implementation is reused; only its row layout and one mandatory alpha
call are extended. The authenticated `/vr_ws` snapshot protocol and all snapshot presentation
types are removed.

**Tech Stack:** Rust 2021, `tokio`, `tokio-tungstenite`, `serde`/`serde_json`, `thiserror`,
existing DirectWrite renderer, `openvr_sys` 2.1.3 (statically linked client binding), PowerShell
verification.

**Spec:** `vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md`

## Baseline (verified before planning)

- `cargo 1.97.1`, `rustc 1.97.1`; `cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check`
  clean; `target/release/RinBridgeOverlay.exe` already builds.
- Branch `pr/vr-subtitle-rust-only`, HEAD `cbc2180`.
- Rust-only changed-path boundary holds against `origin/main`.
- `openvr_sys` 2.1.3 `build.rs` does `cargo:rustc-link-lib=static=openvr_api64`; the static
  loader reads `openvrpaths.vrpath` and loads `vrclient_x64.dll`. No `openvr_api.dll` sidecar is
  required. `vr_overlay/vendor/openvr_api.dll` and the `scripts/verify.ps1` precheck for it are
  obsolete.

## Global Constraints

- Every changed path relative to `origin/main` stays under `vr_overlay/`.
- Product caption protocol is the unauthenticated desktop `/ws` stream only. `/vr_ws`, the
  snapshot presentation types, and the snapshot compatibility branches are removed.
- `update.final_tokens` accumulate per `(sentence_id, speaker, track)`; no global source or
  translation accumulator. Whole-array replay is deduplicated, a strictly longer cumulative
  prefix replaces its own accumulator, a true delta appends.
- `update.non_final_tokens` is a replaceable last-value-wins snapshot. Exactly one live row is
  emitted per frame for the last speaker in the source order.
- The live row never settles because a non-final array became empty; only the owning sentence's
  source commit or an explicit end event transitions it.
- The HUD frame is `[Option<HudRow>; 5]`; the live row is always slot 4.
- `HudRow.text` is body-only; the renderer alone composes the speaker label.
- Rendering happens only when the projected frame changes; no periodic redraw timer; no texture
  resubmission for unchanged text.
- Basic logs never contain recognized or translated text.
- Existing native renderer and current head-locked downward placement are preserved.
- Automated tests do not replace the physical SteamVR/HMD acceptance test.

## Resolved Decisions

1. **Manifest contract `v7`.** `EXPECTED_CONTRACT_VERSION = 7`. `session_token` is removed; add
   `view_settings: VrViewSettings` and `calibration: OverlayCalibration`, both
   `#[serde(default)]`. No v6 compatibility branch. The desktop must produce v7 before the
   packaged integration run.
2. **Exit code 12 removed.** `StartupError::BridgeAuth` and `BridgeError::Auth` go with the
   authenticated path. The rest of the exit-code table is unchanged.
3. **Fixed five slots.** Slot 4 is always the live row. Single-language speaker rows are
   bottom-aligned in slots 1..=3 (1 speaker -> slot 3; 2 -> slots 2,3; 3 -> slots 1,2,3). `both`
   one pair -> slots 2 (target),3 (source); two pairs -> slots 0,1 (older) and 2,3 (newer). New
   content scrolls upward; the live row never moves. The frame is `[Option<HudRow>; 5]`.
4. **Row roles + state + language.** `HudRowRole { UpperPrimary, UpperSecondary, LiveSource }`
   determines scale; `HudRowState { Draft, Settled }` drives draft dimming and in-place final
   upgrade; `language: Option<String>` preserves font fallback and cache keys.
5. **Speaker labels.** `HudRow.text` holds the body only; `speaker_label` is a separate field
   composed and measured solely by the renderer. `both` always emits `None`.
6. **Explicit live-row state.** `LiveInputRow { Hidden, Streaming(snapshot), Settled { snapshot, closed_at } }`.
7. **Static OpenVR linkage.** Delete `vr_overlay/vendor/openvr_api.dll`, `vr_overlay/vendor/README.md`,
   and the `verify.ps1` DLL precheck. Add a no-sidecar release-exe contract test.

## Blocking Corrections Baked Into This Plan

- Reducer keeps text to freeze: `LiveInputRow::Settled` retains `snapshot`; it is never cleared
  on settle.
- No settle on empty `non_final_tokens` (translation frames also carry empty arrays).
- Domain types/reducer precede the protocol adapter; no "pick either order".
- Final tokens segmented by `(sentence_id, speaker, track)`.
- One live row per frame, chosen from the last speaker; no per-speaker fan-out.
- A target draft without `sentence_id` binds to the same speaker's open sentence; dropped only
  when no open sentence exists.
- No `/* ... */` placeholders: every test step below gives concrete input and assertions.
- `set_overlay_alpha` has no default body; every `OverlayFrameSubmitter` implementor must
  implement it.

---

### Task 1: View settings and startup envelope

**Files:**
- Create: `vr_overlay/src/views.rs`
- Modify: `vr_overlay/src/lib.rs`
- Modify: `vr_overlay/src/manifest.rs`
- Modify: `vr_overlay/src/runtime.rs` (`default_manifest` only)
- Modify: `vr_overlay/tests/runtime.rs` (`test_manifest` only)
- Test: `vr_overlay/src/views.rs`

**Interfaces:**

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DisplayMode { Both, Original, Translation }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VrViewSettings {
    pub display_mode: DisplayMode,
    pub max_speakers: u8,         // 1..=3
    pub bilingual_pair_count: u8, // 1..=2
    pub show_speaker_labels: bool,
}
```

- `Default` = `Both, 3, 1, true`.
- `VrViewSettings::validate(&self) -> Result<(), SettingsError>`:
  `max_speakers` in `1..=3`, `bilingual_pair_count` in `1..=2`, else `Err`.
- `OverlayManifest` fields become: `contract_version, app_version, overlay_instance_id,
  bridge_url, parent_pid, startup_deadline_ms, log_dir, log_level, locale, logging_mode,
  view_settings, calibration`. `session_token` is removed.

- [ ] **Step 1: Write failing view-settings tests**

```rust
#[test]
fn defaults_match_the_product_contract() {
    let s = VrViewSettings::default();
    assert_eq!(s.display_mode, DisplayMode::Both);
    assert_eq!(s.max_speakers, 3);
    assert_eq!(s.bilingual_pair_count, 1);
    assert!(s.show_speaker_labels);
}

#[test]
fn capacities_are_rejected_not_clamped() {
    let bad = [
        VrViewSettings { max_speakers: 0, ..Default::default() },
        VrViewSettings { max_speakers: 4, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 0, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 3, ..Default::default() },
    ];
    for s in bad {
        assert!(s.validate().is_err(), "{s:?} must be rejected");
    }
    assert!(VrViewSettings { max_speakers: 3, bilingual_pair_count: 2, ..Default::default() }
        .validate()
        .is_ok());
}

#[test]
fn unknown_control_fields_are_rejected() {
    let raw = r#"{"display_mode":"both","max_speakers":3,"bilingual_pair_count":1,
                  "show_speaker_labels":true,"extra":1}"#;
    assert!(serde_json::from_str::<VrViewSettings>(raw).is_err());
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml views -- --nocapture`

- [ ] **Step 3: Implement `views.rs` and extend the manifest envelope**

`manifest.rs`: `pub const EXPECTED_CONTRACT_VERSION: u32 = 7;`, remove `session_token`, add
`#[serde(default)] pub view_settings: VrViewSettings` and
`#[serde(default)] pub calibration: OverlayCalibration` to both `OverlayManifest` and
`OverlayManifestSerde`. Update `default_manifest()` and `test_manifest()` to the new field set.
Export `DisplayMode`, `VrViewSettings`, `SettingsError` from `lib.rs`.

- [ ] **Step 4: Run and verify GREEN, then commit**

`cargo test --manifest-path vr_overlay/Cargo.toml views`

```powershell
git add vr_overlay/src/views.rs vr_overlay/src/lib.rs vr_overlay/src/manifest.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): add HUD view settings and v7 startup envelope"
```

---

### Task 2: Domain types and transcript reducer

> This task must land before Task 3: the adapter consumes `SpeakerKey`, `LiveSourceSnapshot`,
> `CommittedSource`, and `TargetUpdate`, which are defined here.

**Files:**
- Create: `vr_overlay/src/transcript.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/transcript.rs`

**Interfaces:**

```rust
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum SpeakerKey { Diarized(String), Anonymous }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackPhase { Empty, Draft, Committed, Refined }

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TextTrack { pub text: String, pub phase: TrackPhase }

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SentenceKey { pub local_ordinal: u64, pub upstream_id: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SentenceRecord {
    pub key: SentenceKey,
    pub speaker: SpeakerKey,
    pub source: TextTrack,
    pub target: TextTrack,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LiveSourceSnapshot {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedSource {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetUpdate {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LiveInputRow {
    Hidden,
    Streaming(LiveSourceSnapshot),
    Settled { snapshot: LiveSourceSnapshot, closed_at: Instant },
}

// The reducer's input surface. It lives here (not in protocol.rs) so that the
// domain module has no dependency on the transport module; `protocol.rs`
// produces these values and merely imports them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptionEvent {
    SourceLive(LiveSourceSnapshot),
    SourceCommitted(CommittedSource),
    TargetDraft(TargetUpdate),
    TargetCommitted(TargetUpdate),
    RefinedTarget(TargetUpdate),
    Clear { preserve_existing: bool },
    ViewSettingsChanged(VrViewSettings),
    Activity,
}

pub struct TranscriptState {
    pub sentences: VecDeque<SentenceRecord>, // bounded at 64
    pub live_input: LiveInputRow,
    // private: speaker_recency, next_local_ordinal, upstream_index
}

impl TranscriptState {
    pub fn apply(&mut self, event: &CaptionEvent, now: Instant);
    pub fn clear(&mut self, preserve_existing: bool);
    pub fn latest_speaker(&self) -> Option<&SpeakerKey>;
    pub fn sentence_by_key(&self, key: &SentenceKey) -> Option<&SentenceRecord>;
}
```

`CaptionEvent` is defined here, in the domain module. `protocol.rs` (Task 3) imports it; the
reducer never imports the transport. This is the single authoritative ordering: **Task 2
defines the domain types and `CaptionEvent`, Task 3 produces them, Task 4 consumes the reducer
state, Task 7 consumes both.**

Rules:
- `SourceLive` non-empty sets `Streaming` and updates speaker recency. `SourceLive` empty is a
  no-op and never settles.
- A source commit for the sentence currently `Streaming` binds its `sentence_id` (if any) and
  sets `Settled { snapshot: last_streaming_snapshot, closed_at: now }`. If no `Streaming`
  snapshot exists, the commit alone creates/updates the sentence record and does not touch
  `live_input`.
- A matching `sentence_id` updates its record in place. A new `sentence_id` binds to the
  speaker's current unbound record if one exists, otherwise appends a record with a fresh
  monotonic `local_ordinal`.
- `TargetDraft`/`TargetCommitted`/`RefinedTarget` set `record.target` (`Draft`/`Committed`/
  `Refined`). A target with `sentence_id: None` binds to the same speaker's open sentence (the
  most recent record for that speaker whose source is not `Committed`, else the most recent
  record for that speaker). It is dropped only when the speaker has no record at all.
- `Anonymous` is used when the provider supplies no speaker value.
- Records evict oldest-first beyond 64.
- `clear(false)` resets sentences, `live_input`, recency, and ordinals. `clear(true)` retains
  records whose source is `Committed`/`Refined` or whose target is non-`Empty`, and resets
  `live_input` to `Hidden`.

- [ ] **Step 1: Write failing reducer tests (complete bodies)**

```rust
fn src_live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: None,
        text: text.into(),
    })
}

fn src_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
    })
}

fn target_draft(speaker: &str, id: Option<&str>, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: id.map(str::to_owned),
        text: text.into(),
    })
}

#[test]
fn asr_close_retains_the_frozen_snapshot() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&src_live("1", "hello wor"), t0);
    s.apply(&src_commit("1", "A", "hello world"), t0);
    match &s.live_input {
        LiveInputRow::Settled { snapshot, .. } => assert_eq!(snapshot.text, "hello wor"),
        other => panic!("expected Settled, got {other:?}"),
    }
}

#[test]
fn empty_non_final_does_not_settle_the_live_row() {
    let mut s = TranscriptState::default();
    s.apply(&src_live("1", "hello"), Instant::now());
    s.apply(&src_live("1", ""), Instant::now());
    assert!(matches!(s.live_input, LiveInputRow::Streaming(ref snap) if snap.text == "hello"));
}

#[test]
fn translation_draft_without_sentence_id_binds_to_open_sentence() {
    let mut s = TranscriptState::default();
    s.apply(&src_live("1", "bonjour"), Instant::now());
    s.apply(&target_draft("1", None, "hello"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].target.text, "hello");
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Draft);
}

#[test]
fn stale_target_cannot_overwrite_newer_sentence_for_same_speaker() {
    let mut s = TranscriptState::default();
    s.apply(&src_commit("1", "A", "a"), Instant::now());
    s.apply(&src_commit("1", "B", "b"), Instant::now());
    s.apply(&target_draft("1", Some("B"), "bee"), Instant::now());
    s.apply(&target_draft("1", Some("A"), "ay"), Instant::now());
    let a = s.sentences.iter().find(|r| r.key.upstream_id.as_deref() == Some("A")).unwrap();
    let b = s.sentences.iter().find(|r| r.key.upstream_id.as_deref() == Some("B")).unwrap();
    assert_eq!(a.target.phase, TrackPhase::Empty, "stale target A must be rejected");
    assert_eq!(a.target.text, "");
    assert_eq!(b.target.text, "bee");
}

#[test]
fn local_identity_binds_to_upstream_id_without_duplication() {
    let mut s = TranscriptState::default();
    s.apply(&src_live("1", "hel"), Instant::now());
    s.apply(&src_commit("1", "A", "hello"), Instant::now());
    s.apply(&target_draft("1", Some("A"), "bonjour"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
}

#[test]
fn recent_ledger_stays_bounded_at_64() {
    let mut s = TranscriptState::default();
    for i in 0..80u64 {
        s.apply(&src_commit("1", &format!("{i}"), "x"), Instant::now());
    }
    assert_eq!(s.sentences.len(), 64);
}

#[test]
fn anonymous_provider_uses_one_speaker_key() {
    let mut s = TranscriptState::default();
    for i in 0..3u64 {
        s.apply(&CaptionEvent::SourceCommitted(CommittedSource {
            speaker: SpeakerKey::Anonymous,
            sentence_id: Some(format!("{i}")),
            text: "x".into(),
        }), Instant::now());
    }
    assert!(s.sentences.iter().all(|r| r.speaker == SpeakerKey::Anonymous));
    assert_eq!(s.sentences.len(), 3);
}

#[test]
fn clear_false_resets_and_clear_true_keeps_settled_upper_results() {
    let mut s = TranscriptState::default();
    s.apply(&src_commit("1", "A", "a"), Instant::now());
    s.apply(&target_draft("1", Some("A"), "ay"), Instant::now());
    s.apply(&src_live("1", "next"), Instant::now());
    s.clear(true);
    assert_eq!(s.sentences.len(), 1);
    assert!(matches!(s.live_input, LiveInputRow::Hidden));
    s.clear(false);
    assert!(s.sentences.is_empty());
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml transcript -- --nocapture`

- [ ] **Step 3: Implement `transcript.rs`**

No async, no OpenVR, no rendering. `Instant` is carried only for `closed_at`.

- [ ] **Step 4: GREEN, then commit**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml transcript
git add vr_overlay/src/transcript.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add sentence-aware transcript reducer and live-row state"
```

---

### Task 3: Desktop protocol adapter

**Files:**
- Create: `vr_overlay/src/protocol.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/protocol.rs`

**Interfaces:**

```rust
use crate::transcript::CaptionEvent; // defined in Task 2, not here

pub struct DesktopProtocol { /* stream, per-key accumulators, separator flags, fingerprint */ }

impl DesktopProtocol {
    pub async fn connect(manifest: &OverlayManifest) -> Result<Self, BridgeError>;
    pub async fn next_event(&mut self) -> Result<CaptionEvent, BridgeError>;
    /// Non-blocking: `Some(event)` when a frame is already buffered, `None` otherwise.
    pub fn try_next_event(&mut self) -> Option<Result<CaptionEvent, BridgeError>>;
}
```

- [ ] **Step 1: Write failing adapter tests with concrete inputs**

In-process WebSocket helper (port the scripted server from `tests/runtime.rs`; each test sends
an explicit frame then closes):

```rust
async fn run_frames_through_adapter(frames: &[&str]) -> Result<Vec<CaptionEvent>, BridgeError> {
    let (url, server) = spawn_scripted_server(frames);
    let manifest = test_manifest_with_url(&url);
    let mut p = DesktopProtocol::connect(&manifest).await?;
    let mut out = Vec::new();
    loop {
        match p.next_event().await {
            Ok(ev) => out.push(ev),
            Err(BridgeError::Disconnected) => break,
            Err(e) => return Err(e),
        }
    }
    server.await.unwrap();
    Ok(out)
}

#[tokio::test]
async fn final_source_true_delta_appends_and_replay_dedupes() {
    let frames = [
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
    ];
    let events = run_frames_through_adapter(&frames).await.unwrap();
    let committed: Vec<_> = events
        .iter()
        .filter_map(|e| match e {
            CaptionEvent::SourceCommitted(c) => Some(c.text.clone()),
            _ => None,
        })
        .collect();
    // frame 1 -> "very"; frame 2 identical -> deduped, no event; frame 3 two deltas -> "veryvery"
    assert_eq!(committed, vec!["very".to_string(), "veryvery".to_string()]);
}

#[tokio::test]
async fn final_tokens_segment_by_sentence_and_speaker() {
    let frames = [r#"{"type":"update","final_tokens":[
        {"text":"hello ","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
        {"text":"bonjour ","speaker":"2","translation_status":"original","llm_sentence_id":"B","is_final":true}
    ],"non_final_tokens":[]}"#];
    let events = run_frames_through_adapter(&frames).await.unwrap();
    let mut by_id: std::collections::BTreeMap<String, String> = Default::default();
    for e in &events {
        if let CaptionEvent::SourceCommitted(c) = e {
            by_id.insert(c.sentence_id.clone().unwrap(), c.text.clone());
        }
    }
    assert_eq!(by_id.get("A").map(String::as_str), Some("hello "));
    assert_eq!(by_id.get("B").map(String::as_str), Some("bonjour "));
}

#[tokio::test]
async fn multi_speaker_non_final_emits_one_live_event_for_the_last_speaker() {
    let frames = [r#"{"type":"update","final_tokens":[],"non_final_tokens":[
        {"text":"from one","speaker":"1","translation_status":"original","is_final":false},
        {"text":"from two","speaker":"2","translation_status":"original","is_final":false}
    ]}"#];
    let events = run_frames_through_adapter(&frames).await.unwrap();
    let live: Vec<_> = events
        .iter()
        .filter_map(|e| match e {
            CaptionEvent::SourceLive(s) => Some(s.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(live.len(), 1);
    assert_eq!(live[0].speaker, SpeakerKey::Diarized("2".into()));
    assert_eq!(live[0].text, "from two");
}

#[tokio::test]
async fn refine_and_clear_and_view_settings_map_to_typed_events() {
    let frames = [
        r#"{"type":"update","final_tokens":[{"text":"src","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"refine_result","sentence_id":"A","source":"src","original_translation":"draft","refined_translation":"refined","no_change":false}"#,
        r#"{"type":"clear","preserve_existing":true}"#,
        r#"{"type":"vr_view_settings","display_mode":"original","max_speakers":2,"bilingual_pair_count":1,"show_speaker_labels":false}"#,
        r#"{"type":"heartbeat"}"#,
    ];
    let events = run_frames_through_adapter(&frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::RefinedTarget(t) if t.text == "refined" && t.sentence_id.as_deref() == Some("A"))));
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::Clear { preserve_existing: true })));
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::ViewSettingsChanged(s) if s.display_mode == DisplayMode::Original && s.max_speakers == 2)));
    assert!(events.iter().any(|e| matches!(e, CaptionEvent::Activity)));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml protocol -- --nocapture`

- [ ] **Step 3: Implement framing, segmentation, and replay rules**

- Port the transport from `bridge.rs` (`connect_async`, tungstenite `Message` handling,
  `BridgeError`). Delete the `/ws` vs `/vr_ws` branch: this module always speaks raw `/ws`.
- Accumulators: `BTreeMap<(Option<String> /*sentence_id*/, SpeakerKey, Track), String>` plus a
  per-track `replace_on_next` flag armed by `is_separator`.
- Whole-frame final-token fingerprint: skip the whole final-token pass when the array equals the
  immediately previous `update` frame; still process `non_final_tokens`.
- Per token: bind key; if `replace_on_next`, replace; else if `accumulator.starts_with(old)` and
  longer, replace; else append the delta exactly. Never `ends_with`-dedupe.
- Emit one `SourceCommitted`/`TargetCommitted` per changed key with the resulting full text.
- `non_final_tokens`: group source tokens by speaker in order; emit exactly one `SourceLive` for
  the last speaker group (concatenate that speaker's contiguous tokens). Emit one `TargetDraft`
  per translation speaker group (adapter does not dedupe translation drafts; the reducer binds
  `None` ids).
- `refine_result` → `RefinedTarget` (`refined_translation` if non-empty else
  `original_translation`).
- `clear` → `Clear { preserve_existing: map["preserve_existing"] == true }`.
- `vr_view_settings` → validate; invalid is logged and ignored (return `Activity`).
- Unknown/heartbeat → `Activity`. Malformed frames → `Err(BridgeError::Protocol)` without
  mutating state.

- [ ] **Step 4: GREEN, then commit**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml protocol
git add vr_overlay/src/protocol.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add typed /ws protocol adapter with segmented accumulation"
```

---

### Task 4: Projection module

**Files:**
- Create: `vr_overlay/src/projection.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/projection.rs`

**Interfaces:**

```rust
pub enum HudRowRole { UpperPrimary, UpperSecondary, LiveSource }
pub enum HudRowState { Draft, Settled }
pub struct HudRow {
    pub role: HudRowRole,
    pub text: String,                    // body only
    pub speaker_label: Option<String>,   // renderer composes it
    pub language: Option<String>,
    pub state: HudRowState,
    pub sentence: Option<SentenceKey>,
}
pub struct HudContentFrame { pub slots: [Option<HudRow>; 5] }
pub const LIVE_SOURCE_HOLD: Duration = Duration::from_millis(1200);
pub fn project(state: &TranscriptState, settings: &VrViewSettings, now: Instant) -> HudContentFrame;
```

Slot placement is decided by the projector:
- single-language: speaker rows bottom-aligned in slots 1..=3;
- `both`: one pair -> slots 2,3; two pairs -> slots 0,1 and 2,3;
- live row: slot 4 when present.

- [ ] **Step 1: Write failing projection tests (complete bodies)**

Helpers build reducer state via the `CaptionEvent` constructors from Task 2, then assert slot
occupancy.

```rust
fn state_with_committed_sources(entries: &[(&str, &str)]) -> TranscriptState {
    let mut s = TranscriptState::default();
    for (speaker, text) in entries {
        s.apply(&CaptionEvent::SourceCommitted(CommittedSource {
            speaker: SpeakerKey::Diarized((*speaker).into()),
            sentence_id: Some(text.to_string()),
            text: (*text).into(),
        }), Instant::now());
    }
    s
}

fn slots_text(frame: &HudContentFrame) -> Vec<Option<String>> {
    frame.slots.iter().map(|s| s.as_ref().map(|r| r.text.clone())).collect()
}

#[test]
fn original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered() {
    let mut s = TranscriptState::default();
    for (sp, txt) in [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2")] {
        s.apply(&CaptionEvent::SourceCommitted(CommittedSource {
            speaker: SpeakerKey::Diarized(sp.into()),
            sentence_id: Some(format!("{sp}-{txt}")),
            text: txt.into(),
        }), Instant::now());
    }
    let settings = VrViewSettings { max_speakers: 3, ..Default::default() };
    let frame = project(&s, &settings, Instant::now());
    assert_eq!(
        slots_text(&frame),
        vec![
            None,
            Some("b1".into()),
            Some("c1".into()),
            Some("a2".into()),
            None,
        ]
    );
}

#[test]
fn both_mode_one_pair_occupies_slots_two_and_three() {
    let mut s = TranscriptState::default();
    s.apply(&src_commit("1", "A", "hello"), Instant::now());
    s.apply(&target_draft("1", Some("A"), "bonjour"), Instant::now());
    let settings = VrViewSettings { bilingual_pair_count: 1, ..Default::default() };
    let frame = project(&s, &settings, Instant::now());
    assert_eq!(frame.slots[0], None);
    assert_eq!(frame.slots[1], None);
    assert_eq!(frame.slots[2].as_ref().map(|r| (r.text.as_str(), r.role)), Some(("bonjour", HudRowRole::UpperPrimary)));
    assert_eq!(frame.slots[3].as_ref().map(|r| (r.text.as_str(), r.role)), Some(("hello", HudRowRole::UpperSecondary)));
    assert_eq!(frame.slots[4], None);
}

#[test]
fn both_mode_two_pairs_occupy_slots_zero_through_three() {
    let mut s = TranscriptState::default();
    for id in ["A", "B"] {
        s.apply(&src_commit("1", id, id), Instant::now());
        s.apply(&target_draft("1", Some(id), &format!("t{id}")), Instant::now());
    }
    let settings = VrViewSettings { bilingual_pair_count: 2, ..Default::default() };
    let frame = project(&s, &settings, Instant::now());
    assert_eq!(frame.slots[0].as_ref().map(|r| r.text.as_str()), Some("tA"));
    assert_eq!(frame.slots[1].as_ref().map(|r| r.text.as_str()), Some("A"));
    assert_eq!(frame.slots[2].as_ref().map(|r| r.text.as_str()), Some("tB"));
    assert_eq!(frame.slots[3].as_ref().map(|r| r.text.as_str()), Some("B"));
}

#[test]
fn live_row_always_occupies_slot_four() {
    let mut s = TranscriptState::default();
    s.apply(&src_live("1", "now speaking"), Instant::now());
    for mode in [DisplayMode::Original, DisplayMode::Translation, DisplayMode::Both] {
        let settings = VrViewSettings { display_mode: mode, ..Default::default() };
        let frame = project(&s, &settings, Instant::now());
        let live = frame.slots[4].as_ref().expect("live row present");
        assert_eq!(live.role, HudRowRole::LiveSource);
        assert_eq!(live.text, "now speaking");
    }
    assert!(frame_slot4_is_only_slot(&frame_for(DisplayMode::Original, &s)));
}

fn frame_for(mode: DisplayMode, s: &TranscriptState) -> HudContentFrame {
    project(s, &VrViewSettings { display_mode: mode, ..Default::default() }, Instant::now())
}
fn frame_slot4_is_only_slot(frame: &HudContentFrame) -> bool {
    frame.slots[0..4].iter().all(Option::is_none) && frame.slots[4].is_some()
}

#[test]
fn settled_source_clears_only_after_hold_and_visible_handoff() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&src_live("1", "hello wor"), t0);
    s.apply(&src_commit("1", "A", "hello world"), t0);
    // translation handoff not yet visible -> keep frozen live row even after the hold
    let late = t0 + LIVE_SOURCE_HOLD + Duration::from_millis(500);
    let settings = VrViewSettings { display_mode: DisplayMode::Translation, ..Default::default() };
    let frame = project(&s, &settings, late);
    assert_eq!(frame.slots[4].as_ref().map(|r| r.text.as_str()), Some("hello wor"));
    // once the target is visible, the next projection after the hold drops the live row
    s.apply(&target_draft("1", Some("A"), "bonjour"), late);
    let frame = project(&s, &settings, late);
    assert_eq!(frame.slots[4], None);
}

#[test]
fn new_live_input_replaces_settled_row_immediately() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&src_live("1", "first"), t0);
    s.apply(&src_commit("1", "A", "first"), t0);
    s.apply(&src_live("1", "second"), t0 + Duration::from_millis(50));
    let frame = project(&s, &VrViewSettings::default(), t0 + Duration::from_millis(60));
    assert_eq!(frame.slots[4].as_ref().map(|r| r.text.as_str()), Some("second"));
}

#[test]
fn speaker_labels_are_separate_fields_and_both_mode_has_none() {
    let mut s = TranscriptState::default();
    s.apply(&src_commit("1", "A", "hello"), Instant::now());
    let labeled = project(&s, &VrViewSettings { display_mode: DisplayMode::Original, show_speaker_labels: true, ..Default::default() }, Instant::now());
    let row = labeled.slots.iter().flatten().find(|r| r.role == HudRowRole::UpperPrimary).unwrap();
    assert_eq!(row.text, "hello");
    assert_eq!(row.speaker_label.as_deref(), Some("1"));
    let both = project(&s, &VrViewSettings { display_mode: DisplayMode::Both, show_speaker_labels: true, ..Default::default() }, Instant::now());
    assert!(both.slots.iter().flatten().all(|r| r.speaker_label.is_none()));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml projection -- --nocapture`

- [ ] **Step 3: Implement the three projections and the live-row builder**

`project_original`, `project_translation`, `project_bilingual`, shared `place_live_row`. Set
`HudRowState::Draft` for uncommitted target text, `Settled` otherwise. Set `language` from the
sentence record when available.

- [ ] **Step 4: GREEN, then commit**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml projection
git add vr_overlay/src/projection.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add fixed-slot HUD projections"
```

---

### Task 5: Fixed-slot HUD rendering

**Files:**
- Modify: `vr_overlay/src/renderer/types.rs`
- Modify: `vr_overlay/src/renderer/layout.rs`
- Modify: `vr_overlay/src/renderer/backend.rs`
- Modify: `vr_overlay/src/renderer/mod.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/tests/renderer.rs` (new `hud_*` group)

**Interfaces:**
- `CaptionRenderer::render_hud_frame(&self, slots: &[Option<RendererRow>; 5], presentation: &CaptionPresentation) -> Result<RenderedFrame, CaptionRenderError>`.
- `RendererRow { role: RendererRowRole, text: String, speaker_label: Option<String>, language: Option<String>, state: RendererRowState }`
  (renderer-local mirror; the coordinator converts one-to-one so the renderer does not depend on
  `projection.rs`).
- Slot index → fixed top: `HUD_SLOT_TOP_PX(index) = HUD_FIRST_SLOT_TOP_PX + index as f32 * HUD_SLOT_STRIDE_PX`.
- Left origin: `HUD_TEXT_LEFT_PX`. Content width: `HUD_CONTENT_WIDTH_PX`.
- Role → font size: `UpperPrimary` = primary scale; `UpperSecondary`/`LiveSource` = secondary
  scale; all multiplied by `presentation.text_scale`.
- The renderer composes `speaker_label` into the measured line (label prefix + body). It is the
  only composer.
- One physical line per slot. Overflow uses a trailing ellipsis except `LiveSource`, which keeps
  the newest tail with a leading ellipsis.
- Per-slot cache keyed by `(text, role, state, language, style, font size, content width,
  text_scale)`. Extend `LineCacheKey`.
- CJK prohibition: opening punctuation must not end a line; closing punctuation must not begin.
- Truncation is a pure, exported helper
  `fit_row_text(text: &str, max_advance: f32, direction: Truncation, advance: &impl Fn(char) -> f32) -> String`
  with `Truncation { Trailing, Leading }`, so punctuation-boundary behavior is deterministic in
  tests without depending on real glyph metrics. `render_hud_frame` calls it with the real
  DirectWrite advance function.

Suggested constants: `HUD_SLOT_COUNT: usize = 5`, `HUD_FIRST_SLOT_TOP_PX`, `HUD_SLOT_STRIDE_PX`,
`HUD_TEXT_LEFT_PX`, `HUD_CONTENT_WIDTH_PX`.

- [ ] **Step 1: Write failing renderer tests (complete bodies)**

```rust
fn row(role: RendererRowRole, text: &str) -> RendererRow {
    RendererRow { role, text: text.into(), speaker_label: None, language: None, state: RendererRowState::Settled }
}

fn slots_with(entries: &[(usize, RendererRow)]) -> [Option<RendererRow>; 5] {
    let mut slots: [Option<RendererRow>; 5] = Default::default();
    for (i, r) in entries {
        slots[*i] = Some(r.clone());
    }
    slots
}

#[test]
fn hud_rows_use_fixed_left_origins_and_do_not_move_when_text_grows() {
    let renderer = CaptionRenderer::new().unwrap();
    let short = renderer.render_hud_frame(&slots_with(&[(3, row(RendererRowRole::UpperPrimary, "hi"))]), &CaptionPresentation::default()).unwrap();
    let long = renderer.render_hud_frame(&slots_with(&[(3, row(RendererRowRole::UpperPrimary, "hi there everyone"))]), &CaptionPresentation::default()).unwrap();
    let a = short.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    let b = long.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    assert_close(a.bounds.left_px, b.bounds.left_px);
    assert_close(a.bounds.left_px, HUD_TEXT_LEFT_PX);
    assert_close(a.bounds.top_px, b.bounds.top_px);
}

#[test]
fn live_row_keeps_newest_tail_with_leading_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "α".repeat(400) + "NEWESTTAIL";
    let frame = renderer.render_hud_frame(&slots_with(&[(4, row(RendererRowRole::LiveSource, &text))]), &CaptionPresentation::default()).unwrap();
    let visible = frame.layout().visible_blocks.iter().find(|b| b.id == "slot-4").unwrap();
    let line = &visible.primary_lines[0].text;
    assert!(line.starts_with('…'));
    assert!(line.ends_with("NEWESTTAIL"));
}

#[test]
fn single_language_row_overflow_uses_trailing_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "BEGINNING".to_string() + &"x".repeat(400);
    let frame = renderer.render_hud_frame(&slots_with(&[(3, row(RendererRowRole::UpperPrimary, &text))]), &CaptionPresentation::default()).unwrap();
    let line = &frame.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap().primary_lines[0].text;
    assert!(line.starts_with("BEGINNING"));
    assert!(line.ends_with('…'));
}

#[test]
fn renderer_is_the_only_speaker_label_composer() {
    let renderer = CaptionRenderer::new().unwrap();
    let mut r = row(RendererRowRole::UpperPrimary, "hello");
    r.speaker_label = Some("S1".into());
    let frame = renderer.render_hud_frame(&slots_with(&[(3, r)]), &CaptionPresentation::default()).unwrap();
    let line = &frame.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap().primary_lines[0].text;
    assert!(line.starts_with("S1"));
    assert!(line.contains("hello"));
}

#[test]
fn cjk_prohibition_never_strands_punctuation_at_a_truncated_edge() {
    // Fixed 1.0 advance per char makes the boundary deterministic.
    let advance = |_ch: char| 1.0f32;
    // trailing truncation to 3 chars would end on '（' (opening) -> drop it.
    assert_eq!(
        fit_row_text("你好（世界", 3.0, Truncation::Trailing, &advance),
        "你好…"
    );
    // leading truncation to 3 chars would begin on '）' (closing) -> drop it.
    assert_eq!(
        fit_row_text("世界）好你", 3.0, Truncation::Leading, &advance),
        "…好你"
    );
}

#[test]
fn empty_slots_are_transparent_and_five_slots_are_reserved() {
    let renderer = CaptionRenderer::new().unwrap();
    let frame = renderer.render_hud_frame(&slots_with(&[(4, row(RendererRowRole::LiveSource, "x"))]), &CaptionPresentation::default()).unwrap();
    assert_eq!(frame.layout().visible_blocks.len(), 1);
    assert_eq!(frame.layout().visible_blocks[0].id, "slot-4");
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml hud -- --nocapture`

- [ ] **Step 3: Implement the slot layout path**

Add the renderer-local row types and `render_hud_frame`. Keep the old `render_blocks` only until
Task 8 removes its users.

- [ ] **Step 4: GREEN, then commit**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml hud
git add vr_overlay/src/renderer vr_overlay/src/lib.rs vr_overlay/tests/renderer.rs
git commit -m "feat(vr): render fixed five-slot HUD frames"
```

---

### Task 6: OpenVR compositor alpha (mandatory implementation)

**Files:**
- Modify: `vr_overlay/src/openvr.rs`
- Modify: `vr_overlay/src/runtime.rs` (`ShellSubmitter`)
- Modify: `vr_overlay/tests/runtime.rs` (`RecordingSubmitter`)
- Test: `vr_overlay/tests/runtime.rs`

**Interfaces:**
- Add to `OverlayFrameSubmitter` with **no default body**:

```rust
fn set_overlay_alpha(&mut self, alpha: f32) -> Result<(), OpenVrError>;
```

- Every implementor must add it explicitly: `OpenVrOverlay` (delegates to backend),
  `FakeOpenVr`, `WindowsOpenVrOverlay`/backend, `ShellSubmitter`, `RecordingSubmitter`.
  Deleting the default makes a missed real implementation a compile error, not a silent no-op.
- Clamp `alpha` to `0.0..=1.0`. `WindowsOpenVrOverlay` calls `IVROverlay::SetOverlayAlpha`.
- `FakeOpenVr` records `last_alpha`.

- [ ] **Step 1: Write a failing test**

```rust
#[test]
fn overlay_alpha_reaches_the_submitter() {
    let mut fake = FakeOpenVr::default();
    fake.set_overlay_alpha(0.5).unwrap();
    assert_eq!(fake.last_alpha(), Some(0.5));
    fake.set_overlay_alpha(2.0).unwrap();
    assert_eq!(fake.last_alpha(), Some(1.0));
    fake.set_overlay_alpha(-1.0).unwrap();
    assert_eq!(fake.last_alpha(), Some(0.0));
}
```

- [ ] **Step 2: RED, then implement all implementors**

Grep `impl OverlayFrameSubmitter` / `impl crate::openvr::OverlayFrameSubmitter` after editing to
confirm every implementor is covered.

- [ ] **Step 3: GREEN, then commit**

```powershell
git add vr_overlay/src/openvr.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): require explicit compositor overlay alpha"
```

---

### Task 7: Runtime coordinator

**Files:**
- Rewrite: `vr_overlay/src/runtime.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/tests/runtime.rs` (new `coordinator` group)

**Interfaces:**
- `Clock { fn now(&self) -> Instant; }`, `SystemClock`, `FakeClock`.
- `ParentLiveness { fn alive(&self) -> bool; }`, `ProcessLiveness` (Windows `OpenProcess` +
  zero-timeout wait on `parent_pid`), `FakeParent`.
- `RuntimeCoordinator` owns `TranscriptState`, `VrViewSettings`, last `HudContentFrame`,
  `VisibilityController`, `last_submitted: Option<[Option<RendererRow>; 5]>`, counters.
- `VisibilityController` with `on_activity(now: Instant)`, `tick(now: Instant) -> Option<VisibilityAction>`:
  `SILENCE_BEFORE_FADE = 4.0s`, `FADE_DURATION = 1.2s`.

Event-loop rules:
- After the first event, drain with `try_next_event()` until it returns `None`, apply all events
  to the reducer, project once, then compare to the last submitted frame; submit only on change.
- Never sleep for a batching window.
- `SourceLive` with non-empty text or a new visible target/refinement calls `on_activity`;
  `Activity`/heartbeats never do.
- `ViewSettingsChanged` validates and applies atomically; invalid frames are logged and ignored.
- Startup: retry connect until `startup_deadline_ms` while `parent.alive()`. After readiness, a
  disconnect clears live input, hides the overlay, and reconnects with `250ms` doubling to `5s`.
  Reconnect resets transcript state because raw `/ws` has no snapshot.
- Poll parent liveness at a fixed interval; exit cleanly on parent death.
- `clear` semantics delegate to `TranscriptState::clear`.
- Keep the ready gate: `overlay_ready` fires after the first successful submit.

- [ ] **Step 1: Write failing coordinator tests**

Use `FakeClock`, `FakeParent`, the real renderer, and `RecordingSubmitter` (extended in Task 6).

```rust
#[test]
fn queued_events_coalesce_into_one_render_without_delaying_the_first_update() {
    let mut c = RuntimeCoordinator::for_test();
    c.apply_for_test(&[src_live("1", "hello"), src_live("1", "hello wor")]);
    assert_eq!(c.submitted_rows_for_test().len(), 1);
    assert_eq!(c.submitted_rows_for_test()[0][4].as_ref().unwrap().text, "hello wor");
}

#[test]
fn unchanged_projection_does_not_submit_a_texture() {
    let mut c = RuntimeCoordinator::for_test();
    c.apply_for_test(&[src_live("1", "same"), src_live("1", "same")]);
    assert_eq!(c.submit_count_for_test(), 1);
}

#[test]
fn invalid_settings_frame_leaves_last_valid_settings_active() {
    let mut c = RuntimeCoordinator::for_test();
    c.apply_settings_for_test(VrViewSettings { display_mode: DisplayMode::Translation, max_speakers: 3, bilingual_pair_count: 1, show_speaker_labels: true });
    c.apply_settings_for_test(VrViewSettings { max_speakers: 9, ..Default::default() });
    assert_eq!(c.settings_for_test().display_mode, DisplayMode::Translation);
    assert_eq!(c.settings_for_test().max_speakers, 3);
}

#[test]
fn silence_fade_uses_compositor_alpha_and_wakes_instantly() {
    let mut v = VisibilityController::new(Instant::now());
    let t = Instant::now();
    v.on_activity(t);
    assert_eq!(v.tick(t), None);
    let fade_start = t + Duration::from_millis(4000);
    assert_eq!(v.tick(fade_start), Some(VisibilityAction::SetAlpha(1.0)));
    assert_eq!(v.tick(fade_start + Duration::from_millis(600)), Some(VisibilityAction::SetAlpha(0.5)));
    assert_eq!(v.tick(fade_start + Duration::from_millis(1200)), Some(VisibilityAction::Hide));
    v.on_activity(fade_start + Duration::from_millis(1300));
    assert_eq!(v.tick(fade_start + Duration::from_millis(1300)), Some(VisibilityAction::SetAlpha(1.0)));
}

#[test]
fn parent_death_exits_cleanly() {
    let mut c = RuntimeCoordinator::for_test_with_parent(false);
    c.poll_parent_for_test(Instant::now());
    assert!(c.should_exit_for_test());
}

#[test]
fn reconnect_clears_upper_state() {
    let mut c = RuntimeCoordinator::for_test();
    c.apply_for_test(&[src_commit("1", "A", "hello"), target_draft("1", Some("A"), "bonjour")]);
    c.on_disconnect_for_test();
    assert!(c.transcript_for_test().sentences.is_empty());
}

#[test]
fn startup_deadline_expires_without_desktop() {
    let mut c = RuntimeCoordinator::for_test_with_parent(true);
    c.set_deadline_for_test(Duration::from_millis(3000));
    assert!(c
        .startup_tick_for_test(Duration::from_millis(3001))
        .is_err());
}
```

- [ ] **Step 2: RED**

`cargo test --manifest-path vr_overlay/Cargo.toml coordinator -- --nocapture`

- [ ] **Step 3: Implement the coordinator, clock, parent liveness, visibility controller.**

Keep the existing `StartupError` exit-code table minus `BridgeAuth`.

- [ ] **Step 4: GREEN, then commit**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml coordinator
git add vr_overlay/src/runtime.rs vr_overlay/src/lib.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): add projection-driven runtime coordinator"
```

---

### Task 8: Remove snapshot protocol, legacy state, and the obsolete DLL sidecar

**Files:**
- Delete: `vr_overlay/src/bridge.rs`, `vr_overlay/src/desktop_caption.rs`
- Delete: `vr_overlay/tests/state.rs`
- Delete: `vr_overlay/vendor/openvr_api.dll`, `vr_overlay/vendor/README.md` (and the now-empty
  `vr_overlay/vendor/` directory)
- Delete snapshot types from `vr_overlay/src/state.rs`; keep `OverlayCalibration` and its
  non-zero defaults
- Modify: `vr_overlay/src/openvr.rs` (replace `OverlayPresentationCalibration::default()` with
  `OverlayCalibration::default()`)
- Modify: `vr_overlay/src/lib.rs` (prune exports)
- Modify: `vr_overlay/tests/renderer.rs`, `vr_overlay/tests/runtime.rs` (drop snapshot imports
  and the recording submitter's snapshot fixtures)
- Modify: `vr_overlay/scripts/verify.ps1` (delete the
  `vr_overlay/vendor/openvr_api.dll` existence precheck and any vendored-DLL mention)

Removed names: `OverlayPresentationSnapshot`, `OverlayPresentationBlock`,
`OverlayPresentationBlockVariant`, `OverlayPresentationCalibration`, `OverlayState`,
`OverlayStateScene`, `OverlayStateSlot`, `PresentationScene`, `PresentationSlot`, `RuntimeState`,
`VISIBLE_SLOT_CAP`, `SLOT_ROW_STRIDE_PX`, `FIRST_SLOT_TOP_PX`, `CaptionUpdate`,
`OverlayBridgeEvent`, `BridgeControl`, and the authenticated branch of `BridgeClient`.

- [ ] **Step 1: Delete modules, types, vendored DLL, and the verify precheck.**
- [ ] **Step 2: Fix compile errors by migrating remaining call sites; keep `render_blocks` only
  if a live user remains, otherwise delete it and its tests.**
- [ ] **Step 3: Run the full suite**

`cargo test --manifest-path vr_overlay/Cargo.toml`

- [ ] **Step 4: Commit**

```powershell
git add -A vr_overlay
git commit -m "refactor(vr): remove /vr_ws snapshot protocol and openvr_api.dll sidecar"
```

---

### Task 9: Diagnostics and contract documentation

**Files:**
- Modify: `vr_overlay/src/logging.rs`
- Modify: `vr_overlay/src/runtime.rs`
- Modify: `vr_overlay/AGENTS.md`
- Modify: `vr_overlay/README.md`
- Modify: `vr_overlay/scripts/verify.ps1` (only if boundary/env preflight text needs updating)

Basic mode records event type, speaker-key hash, sentence ordinal, dirty-slot mask, render
duration, and submission sequence — never caption text. Detailed mode caps escaped previews at
80 Unicode scalars.

`AGENTS.md` documents: single raw `/ws` product path; the four modules; the `[Option<HudRow>; 5]`
fixed-slot frame with live at slot 4; the explicit `LiveInputRow` state machine; segmented final
token accumulation; renderer-only label composition; alpha fade; parent lifetime; and that
`openvr_sys` is statically linked with no sidecar DLL.

`README.md` documents the v7 envelope (no `session_token`), the single `RinBridgeOverlay.exe`
artifact, and the no-sidecar OpenVR linkage (the build section was already corrected during
planning).

- [ ] **Step 1: Add failing diagnostic-assertion tests; RED.**
- [ ] **Step 2: Implement logging changes; GREEN.**
- [ ] **Step 3: Rewrite `AGENTS.md` and finish `README.md`.**
- [ ] **Step 4: Commit**

```powershell
git add vr_overlay/src/logging.rs vr_overlay/src/runtime.rs vr_overlay/AGENTS.md vr_overlay/README.md vr_overlay/scripts/verify.ps1
git commit -m "chore(vr): add HUD diagnostics and update the runtime contract"
```

---

### Task 10: Completion gate, no-sidecar packaging test, and HMD acceptance

**Files:** none for code; the packaging test may live in `vr_overlay/scripts/verify.ps1`.

- [ ] **Step 1: Add the no-sidecar packaging check to `verify.ps1`**

After `cargo build --release`:

```powershell
$exe = Join-Path $repoRoot 'vr_overlay\target\release\RinBridgeOverlay.exe'
$probeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rin-nosidecar-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $probeDir | Out-Null
$probeExe = Join-Path $probeDir 'RinBridgeOverlay.exe'
Copy-Item -LiteralPath $exe -Destination $probeExe
$contract = & $probeExe --check-startup-contract
if ($LASTEXITCODE -ne 0 -or $contract.Trim() -ne '{"contract_version":7}') {
    throw "no-sidecar contract probe failed: exit=$LASTEXITCODE output=$contract"
}
Remove-Item -LiteralPath $probeDir -Recurse -Force
$packaged = Get-ChildItem -LiteralPath (Split-Path $exe) -Filter 'RinBridgeOverlay.exe'
if (@($packaged).Count -ne 1) { throw 'release selection must contain exactly one RinBridgeOverlay.exe' }
```

- [ ] **Step 2: Run the Rust-only completion gate**

```powershell
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
powershell -ExecutionPolicy Bypass -File .\vr_overlay\scripts\verify.ps1
```

Changed paths must contain only `vr_overlay/`. No launcher script, manifest template, or helper
executable is present.

- [ ] **Step 3: Record the physical HMD acceptance checklist as pending** until a headset run is
  actually performed:
  1. downward viewing position preserved;
  2. first live source appears with no perceptible delay;
  3. streaming text never shifts horizontally, and the live row never moves slots;
  4. each mode matches the connection-panel setting;
  5. speaker rows never duplicate one speaker;
  6. both mode never shows a target against the wrong source;
  7. idle CPU/GPU negligible and no visible drops under rapid tokens;
  8. fade, instant wake, desktop exit, and reconnect behave correctly.

---

## Verification Matrix

| Requirement | Test |
| --- | --- |
| Exact token order, per-key replay dedup, prefix replace, delta append | Task 3 adapter tests |
| Final tokens segmented by `(sentence_id, speaker, track)` | `final_tokens_segment_by_sentence_and_speaker` |
| One live row from the last speaker per frame | `multi_speaker_non_final_emits_one_live_event_for_the_last_speaker` |
| Live row retains frozen snapshot on close | `asr_close_retains_the_frozen_snapshot` |
| Empty non-finals do not settle | `empty_non_final_does_not_settle_the_live_row` |
| Draft without `sentence_id` binds to open sentence | `translation_draft_without_sentence_id_binds_to_open_sentence` |
| Local ordinal binds to `llm_sentence_id` | `local_identity_binds_to_upstream_id_without_duplication` |
| Stale target cannot overwrite newer same-speaker row | `stale_target_cannot_overwrite_newer_sentence_for_same_speaker` |
| Original `A→B→C→A` = one bottom-aligned row per speaker | `original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered` |
| Both mode fixed slots | `both_mode_one_pair_occupies_slots_two_and_three`, `both_mode_two_pairs_occupy_slots_zero_through_three` |
| Live row always slot 4 | `live_row_always_occupies_slot_four` |
| Settle clears only after hold + visible handoff | `settled_source_clears_only_after_hold_and_visible_handoff` |
| Label is a separate field; both mode none | `speaker_labels_are_separate_fields_and_both_mode_has_none` |
| Change-only render / no resubmit | `unchanged_projection_does_not_submit_a_texture` |
| Fixed left origins, ellipsis, CJK prohibition | Task 5 renderer tests |
| Mandatory compositor alpha | `overlay_alpha_reaches_the_submitter` |
| Coalescing adds no latency | `queued_events_coalesce_into_one_render_without_delaying_the_first_update` |
| Parent death / reconnect / deadline | Task 7 coordinator tests |
| No-sidecar single EXE | Task 10 packaging probe |
| Rust-only PR boundary | Task 10 gate |
| Physical HMD behavior | Task 10 Step 3 (pending) |

## Open Risks

- **Renderer churn.** Replacing center-based two-line block layout touches `layout.rs`,
  `backend.rs`, and `tests/renderer.rs` heavily. Land Task 5 as an additive `render_hud_frame`
  path and remove the old block path only in Task 8.
- **`try_next_event` non-blocking drain.** `tokio-tungstenite` does not expose a poll API
  directly; use `futures_util::future::poll_immediate` (or `tokio::select! { biased; _ =
  std::future::ready(()) => None, event = next_event() => Some(event) }`) and test with several
  frames already queued.
- **v7 manifest handshake.** The desktop must produce v7 before a packaged end-to-end run; the
  Rust suite builds manifests directly.
- **Non-Windows CI.** `renderer/layout.rs` is Windows-gated. Ensure new `hud_*` tests compile and
  pass on the non-Windows heuristic path, matching the existing renderer test strategy.
- **Vendored DLL deletion.** Confirm nothing else references `vr_overlay/vendor/` (the PyInstaller
  spec lives outside the Rust-only boundary; that desktop-side packaging change belongs to the
  integration PR).

## Documentation-Only Bootstrap Commit

Before any code task starts, commit the revised spec, this plan, and the corrected README build
section alone, so the first implementation commit is separated from documentation review:

```powershell
git add vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md vr_overlay/docs/superpowers/plans/2026-09-12-vr-hud-window-redesign.md vr_overlay/README.md
git commit -m "docs(vr): correct HUD redesign spec and add implementation plan"
```

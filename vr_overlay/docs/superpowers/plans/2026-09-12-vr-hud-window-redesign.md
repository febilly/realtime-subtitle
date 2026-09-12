# VR HUD Window Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Rin VR runtime around the desktop's raw `/ws` stream with three
speaker-aware/bilingual HUD projections, a shared fixed five-slot frame whose slot 4 is always
the universal live row, a change-only render path, and compositor-alpha silence fading.

**Architecture:** Pure domain modules plus a coordinator. Task order follows the dependency
chain exactly: **view settings/envelope -> domain types + reducer + shared HUD frame -> protocol
adapter -> projection -> renderer -> OpenVR alpha -> coordinator -> removal -> docs -> gate.**
`src/hud.rs` is the single shared frame value type; projection and renderer both consume it, so
there is no mirror type. The existing D3D11/DirectWrite implementation is reused; only row
layout and one mandatory alpha call are extended. The authenticated `/vr_ws` snapshot protocol
and all snapshot presentation types are removed.

**Tech Stack:** Rust 2021, `tokio`, `tokio-tungstenite`, `serde`/`serde_json`, `thiserror`,
existing DirectWrite renderer, `openvr_sys` 2.1.3 (statically linked client binding), PowerShell
verification.

**Spec:** `vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md`
**Authoritative protocol contract:** `vr_overlay/AGENTS.md` (updated by this plan's bootstrap
commit before any code task starts).

## Baseline (verified before planning)

- `cargo 1.97.1`, `rustc 1.97.1`; `cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check`
  clean; `target/release/RinBridgeOverlay.exe` already builds.
- Branch `pr/vr-subtitle-rust-only`, HEAD `cbc2180` for the first docs commit.
- Rust-only changed-path boundary holds against `origin/main`.
- `openvr_sys` 2.1.3 `build.rs` does `cargo:rustc-link-lib=static=openvr_api64`; the static
  loader reads `openvrpaths.vrpath` and loads `vrclient_x64.dll`. No `openvr_api.dll` sidecar is
  required. `vr_overlay/vendor/openvr_api.dll` and the `scripts/verify.ps1` precheck for it are
  obsolete.

## Global Constraints

- Every changed path relative to `origin/main` stays under `vr_overlay/`.
- Product caption protocol is the unauthenticated desktop `/ws` stream only. A manifest path
  other than `/ws` is rejected at startup.
- `update.final_tokens` accumulate per `(sentence_id, speaker, track)`; no global accumulator.
- `update.non_final_tokens` is a replaceable snapshot; exactly one live row per frame, from the
  last speaker in source order.
- The live row is `Hidden | Streaming(snapshot) | Settled { snapshot, closed_at }`; it settles
  only on the owning sentence's commit or an explicit `SourceEnd`, and it retains the committed
  full source text.
- The HUD frame is `[Option<HudRow>; 5]`; slot 4 is always live.
- `HudRow.text` is body-only; the renderer alone composes `speaker_label`.
- Render only when the projected frame changes.
- Basic logs never contain recognized or translated text.
- Existing native renderer and current head-locked downward placement are preserved.
- Automated tests do not replace the physical SteamVR/HMD acceptance test.

## Resolved Decisions

1. **Manifest contract `v7`.** `EXPECTED_CONTRACT_VERSION = 7`. `session_token` removed; add
   `view_settings` and `calibration`. No v6 compatibility branch.
2. **Exit code 12 removed** with `StartupError::BridgeAuth` / `BridgeError::Auth`.
3. **Fixed five slots.** Slot 4 is live. Single-language speaker rows bottom-align in slots
   1..=3. `both` one pair -> slots 2,3; two pairs -> slots 0,1 and 2,3.
4. **Shared frame types in `src/hud.rs`**, consumed by projection and renderer.
5. **Explicit `SourceEnd` event** for the explicit-end transition.
6. **Reducer keeps older-sentence targets**; projection prevents overwrite.
7. **Static OpenVR linkage**; delete `vendor/openvr_api.dll` and the verify precheck.

---

### Task 1: View settings and v7 startup envelope

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
    pub max_speakers: u8,
    pub bilingual_pair_count: u8,
    pub show_speaker_labels: bool,
}
```

- `impl Default for VrViewSettings` = `Both, 3, 1, true`.
- `pub enum SettingsError { MaxSpeakers(u8), BilingualPairCount(u8) }` (thiserror).
- `VrViewSettings::validate(&self) -> Result<(), SettingsError>`.
- `OverlayManifest` fields: `contract_version, app_version, overlay_instance_id, bridge_url,
  parent_pid, startup_deadline_ms, log_dir, log_level, locale, logging_mode, view_settings,
  calibration`; `session_token` removed.

- [ ] **Step 1: Write failing view-settings tests**

```rust
use super::*;

#[test]
fn defaults_match_the_product_contract() {
    let s = VrViewSettings::default();
    assert_eq!(s.display_mode, DisplayMode::Both);
    assert_eq!(s.max_speakers, 3);
    assert_eq!(s.bilingual_pair_count, 1);
    assert!(s.show_speaker_labels);
    assert!(s.validate().is_ok());
}

#[test]
fn capacities_are_rejected_not_clamped() {
    let cases = [
        VrViewSettings { max_speakers: 0, ..Default::default() },
        VrViewSettings { max_speakers: 4, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 0, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 3, ..Default::default() },
    ];
    for s in cases {
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

#[test]
fn missing_control_fields_are_rejected() {
    let raw = r#"{"display_mode":"both","max_speakers":3}"#;
    assert!(serde_json::from_str::<VrViewSettings>(raw).is_err());
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml views -- --nocapture`

Expected: `VrViewSettings` / `DisplayMode` / `SettingsError` do not exist.

- [ ] **Step 3: Implement `views.rs` and extend the manifest**

`manifest.rs`: `EXPECTED_CONTRACT_VERSION = 7`, remove `session_token`, add
`#[serde(default)] pub view_settings: VrViewSettings` and
`#[serde(default)] pub calibration: OverlayCalibration` to both structs. Update
`default_manifest()` and `test_manifest()`. Export the new names from `lib.rs`.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml views` — expected: 4 passed.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/views.rs vr_overlay/src/lib.rs vr_overlay/src/manifest.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): add HUD view settings and v7 startup envelope"
```

---

### Task 2: Domain types, shared HUD frame, and transcript reducer

> The adapter (Task 3) and projection (Task 4) both depend on this task. `CaptionEvent` and
> `HudFrame` are defined here, never duplicated.

**Files:**
- Create: `vr_overlay/src/hud.rs`
- Create: `vr_overlay/src/transcript.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/transcript.rs`, `vr_overlay/src/hud.rs`

**Interfaces:**

```rust
// ---- transcript.rs ----
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum SpeakerKey { Anonymous, Diarized(String) }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum TrackPhase { #[default] Empty, Draft, Committed, Refined }

#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub struct TextTrack { pub text: String, pub phase: TrackPhase, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
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
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedSource { pub speaker: SpeakerKey, pub sentence_id: Option<String>, pub text: String, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetUpdate { pub speaker: SpeakerKey, pub sentence_id: Option<String>, pub text: String, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LiveInputRow {
    Hidden,
    Streaming(LiveSourceSnapshot),
    Settled { snapshot: LiveSourceSnapshot, closed_at: Instant },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptionEvent {
    SourceLive(LiveSourceSnapshot),
    SourceCommitted(CommittedSource),
    SourceEnd { speaker: SpeakerKey, sentence_id: Option<String> },
    TargetDraft(TargetUpdate),
    TargetCommitted(TargetUpdate),
    RefinedTarget(TargetUpdate),
    Clear { preserve_existing: bool },
    ViewSettingsChanged(VrViewSettings),
    Activity,
}

pub struct TranscriptState {
    pub sentences: VecDeque<SentenceRecord>,
    pub live_input: LiveInputRow,
    speaker_recency: VecDeque<SpeakerKey>,
    next_local_ordinal: u64,
    upstream_index: HashMap<String, u64>,
}

impl TranscriptState {
    pub fn apply(&mut self, event: &CaptionEvent, now: Instant);
    pub fn clear(&mut self, preserve_existing: bool);
    pub fn open_record_index(&self, speaker: &SpeakerKey) -> Option<usize>;
    pub fn sentence_by_upstream_id(&self, id: &str) -> Option<&SentenceRecord>;
}

// ---- hud.rs ----
pub const HUD_SLOT_COUNT: usize = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HudRowRole { UpperPrimary, UpperSecondary, LiveSource }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HudRowKind { Draft, Settled }

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct HudRow {
    pub role: HudRowRole,
    pub kind: HudRowKind,
    pub text: String,
    pub speaker_label: Option<String>,
    pub language: Option<String>,
    pub sentence: Option<SentenceKey>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HudFrame { pub slots: [Option<HudRow>; HUD_SLOT_COUNT] }

impl Default for HudFrame {
    fn default() -> Self {
        Self { slots: std::array::from_fn(|_| None) }
    }
}
```

Reducer rules (exact):
- `SourceLive` non-empty: find the speaker's **open** record = the speaker's most recent record
  with `source.phase == Draft`. If none, append a record with a fresh monotonic `local_ordinal`.
  Set `source = { text, Draft, language }`. Update recency. Set `live_input = Streaming(snapshot)`.
- `SourceLive` empty: no-op. It never settles.
- `SourceCommitted`: resolve the record by `sentence_id` if present, else the speaker's open
  record, else append. Set `source = { text, Committed, language }`. If the record was the live
  one, set `live_input = Settled { snapshot: { speaker, sentence_id, text: committed text,
  language }, closed_at: now }` — the **committed full text**, never the partial tail.
- `SourceEnd { speaker, sentence_id }`: resolve the record; set `source.phase = Committed` (keep
  its text) and settle `live_input` with that record's source text. If there is no record, settle
  with the current `Streaming` snapshot text; if there is no streaming snapshot either, it is a
  no-op.
- Target events: resolve by `sentence_id` if present (append when unknown but an id is given),
  else bind to the speaker's open record, else **drop**. Set `target = { text, phase, language }`
  where phase is `Draft` / `Committed` / `Refined`.
- Never drop an older sentence's target: each target updates its own record.
- Records evict oldest-first beyond 64.
- `clear(false)`: reset sentences, `live_input = Hidden`, recency, ordinals.
- `clear(true)`: retain records with `source.phase` in `{Committed, Refined}` or `target.phase`
  in `{Committed, Refined}`; drop every draft-only record; set `live_input = Hidden`; keep the
  retained `upstream_id`s for a late refinement.

- [ ] **Step 1: Write failing reducer tests**

```rust
use super::*;
use std::time::{Duration, Instant};

fn s_live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: None,
        text: text.into(),
        language: Some("en".into()),
    })
}
fn s_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("en".into()),
    })
}
fn s_end(speaker: &str) -> CaptionEvent {
    CaptionEvent::SourceEnd { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: None }
}
fn t_draft(speaker: &str, id: Option<&str>, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: id.map(str::to_owned),
        text: text.into(),
        language: Some("fr".into()),
    })
}
fn t_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

#[test]
fn source_live_creates_open_record_and_streams() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hel"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].source.phase, TrackPhase::Draft);
    assert_eq!(s.sentences[0].source.text, "hel");
    assert!(matches!(&s.live_input, LiveInputRow::Streaming(snap) if snap.text == "hel"));
}

#[test]
fn asr_commit_settles_the_full_committed_text() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&s_live("1", "hello wor"), t0);
    s.apply(&s_commit("1", "A", "hello world"), t0);
    match &s.live_input {
        LiveInputRow::Settled { snapshot, .. } => {
            assert_eq!(snapshot.text, "hello world", "must freeze the committed text, not the tail");
            assert_eq!(snapshot.sentence_id.as_deref(), Some("A"));
        }
        other => panic!("expected Settled, got {other:?}"),
    }
}

#[test]
fn empty_non_final_does_not_settle_the_live_row() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hello"), Instant::now());
    s.apply(&s_live("1", ""), Instant::now());
    assert!(matches!(&s.live_input, LiveInputRow::Streaming(snap) if snap.text == "hello"));
}

#[test]
fn explicit_source_end_settles_with_current_text() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hello"), Instant::now());
    s.apply(&s_end("1"), Instant::now());
    assert!(matches!(&s.live_input, LiveInputRow::Settled { snapshot, .. } if snapshot.text == "hello"));
}

#[test]
fn target_without_id_binds_to_open_sentence() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "bonjour"), Instant::now());
    s.apply(&t_draft("1", None, "hello"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].target.text, "hello");
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Draft);
}

#[test]
fn target_without_id_is_dropped_when_no_open_sentence() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "done"), Instant::now());
    s.apply(&t_draft("1", None, "orphan"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Empty);
}

#[test]
fn late_target_for_older_sentence_is_kept_in_the_reducer() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.apply(&s_commit("1", "B", "b"), Instant::now());
    s.apply(&t_commit("1", "B", "bee"), Instant::now());
    s.apply(&t_commit("1", "A", "ay"), Instant::now());
    let a = s.sentence_by_upstream_id("A").unwrap();
    let b = s.sentence_by_upstream_id("B").unwrap();
    assert_eq!(a.target.text, "ay");
    assert_eq!(b.target.text, "bee");
}

#[test]
fn local_identity_binds_to_upstream_id_without_duplication() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hel"), Instant::now());
    s.apply(&s_commit("1", "A", "hello"), Instant::now());
    s.apply(&t_commit("1", "A", "bonjour"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
}

#[test]
fn language_is_recorded_on_both_tracks() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "hello"), Instant::now());
    s.apply(&t_commit("1", "A", "bonjour"), Instant::now());
    assert_eq!(s.sentences[0].source.language.as_deref(), Some("en"));
    assert_eq!(s.sentences[0].target.language.as_deref(), Some("fr"));
}

#[test]
fn recent_ledger_stays_bounded_at_64() {
    let mut s = TranscriptState::default();
    for i in 0..80u64 {
        s.apply(&s_commit("1", &format!("{i}"), "x"), Instant::now());
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
            language: None,
        }), Instant::now());
    }
    assert!(s.sentences.iter().all(|r| r.speaker == SpeakerKey::Anonymous));
    assert_eq!(s.sentences.len(), 3);
}

#[test]
fn clear_false_resets_everything() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.clear(false);
    assert!(s.sentences.is_empty());
    assert!(matches!(s.live_input, LiveInputRow::Hidden));
}

#[test]
fn clear_true_keeps_settled_records_and_drops_drafts() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.apply(&t_commit("1", "A", "ay"), Instant::now());
    s.apply(&s_live("2", "draft in progress"), Instant::now());
    s.clear(true);
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].key.upstream_id.as_deref(), Some("A"));
    assert!(matches!(s.live_input, LiveInputRow::Hidden));
}
```

Add one `hud.rs` test: `hud_frame_default_has_five_empty_slots` asserting
`HudFrame::default().slots.iter().all(Option::is_none)`.

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --lib transcript -- --nocapture`

- [ ] **Step 3: Implement `transcript.rs` and `hud.rs`**

Derive `PartialOrd, Ord` on `SpeakerKey` and `SentenceKey`; derive/implement `Default` for
`TrackPhase` and `TextTrack`. `HudFrame::default` uses `std::array::from_fn`.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --lib transcript` and
`... --lib hud` — expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/transcript.rs vr_overlay/src/hud.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add domain types, shared HUD frame, and transcript reducer"
```

---

### Task 3: Desktop protocol adapter

**Files:**
- Create: `vr_overlay/src/protocol.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/protocol.rs`

**Interfaces:**

```rust
use crate::transcript::CaptionEvent; // produced here, defined in Task 2

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum TrackKind { Source, Translation }

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct AccumKey { sentence_id: Option<String>, speaker: SpeakerKey, track: TrackKind }

pub struct DesktopProtocol {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    accumulators: BTreeMap<AccumKey, String>,
    replace_on_next_source: bool,
    replace_on_next_translation: bool,
    previous_final_fingerprint: Option<Vec<(bool, TrackKind, String, Option<String>)>>,
    detached: bool,
}

impl DesktopProtocol {
    pub async fn connect(manifest: &OverlayManifest) -> Result<Self, BridgeError>;
    pub async fn next_event(&mut self) -> Result<CaptionEvent, BridgeError>;
    /// Some(event) when a frame is already buffered; None when the socket would block.
    pub fn try_next_event(&mut self) -> Option<Result<CaptionEvent, BridgeError>>;
}
```

- [ ] **Step 1: Write failing adapter tests**

```rust
use super::*;
use crate::transcript::{CaptionEvent, SpeakerKey};
use crate::views::DisplayMode;

async fn adapter_events(frames: &[&str]) -> Result<Vec<CaptionEvent>, BridgeError> {
    let (url, server) = spawn_scripted_server(frames); // in-process WS server, sends then closes
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

fn committed_texts(events: &[CaptionEvent]) -> Vec<String> {
    events
        .iter()
        .filter_map(|e| match e {
            CaptionEvent::SourceCommitted(c) => Some(c.text.clone()),
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn final_source_true_delta_appends_and_replay_dedupes() {
    let frames = [
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"update","final_tokens":[{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},{"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
    ];
    let events = adapter_events(&frames).await.unwrap();
    assert_eq!(committed_texts(&events), vec!["very".to_string(), "veryvery".to_string()]);
}

#[tokio::test]
async fn final_tokens_segment_by_sentence_and_speaker() {
    let frames = [r#"{"type":"update","final_tokens":[
        {"text":"hello ","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
        {"text":"bonjour ","speaker":"2","translation_status":"original","llm_sentence_id":"B","is_final":true}],
        "non_final_tokens":[]}"#];
    let events = adapter_events(&frames).await.unwrap();
    let mut by_id = std::collections::BTreeMap::new();
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
        {"text":"from two","speaker":"2","translation_status":"original","is_final":false}]}"#];
    let events = adapter_events(&frames).await.unwrap();
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
async fn language_is_carried_on_caption_events() {
    let frames = [r#"{"type":"update","final_tokens":[
        {"text":"hi","speaker":"1","translation_status":"original","llm_sentence_id":"A","language":"en","is_final":true},
        {"text":"salut","speaker":"1","translation_status":"translation","llm_sentence_id":"A","language":"fr","is_final":true}],
        "non_final_tokens":[]}"#];
    let events = adapter_events(&frames).await.unwrap();
    let src = events.iter().find_map(|e| match e { CaptionEvent::SourceCommitted(c) => Some(c), _ => None }).unwrap();
    let tgt = events.iter().find_map(|e| match e { CaptionEvent::TargetCommitted(t) => Some(t), _ => None }).unwrap();
    assert_eq!(src.language.as_deref(), Some("en"));
    assert_eq!(tgt.language.as_deref(), Some("fr"));
}

#[tokio::test]
async fn malformed_frames_are_ignored_without_breaking_state() {
    let frames = [
        r#"{"type":"update","final_tokens":[{"text":"ok","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
        r#"{"type":"update","final_tokens":"not-an-array"}"#,
        r#"this is not json"#,
        r#"{"type":"update","final_tokens":[{"text":" more","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#,
    ];
    let events = adapter_events(&frames).await.unwrap();
    assert_eq!(committed_texts(&events), vec!["ok".to_string(), "ok more".to_string()]);
}

#[tokio::test]
async fn refine_clear_view_settings_and_heartbeat_map_to_events() {
    let frames = [
        r#"{"type":"refine_result","sentence_id":"A","source":"src","original_translation":"draft","refined_translation":"refined","no_change":false}"#,
        r#"{"type":"clear","preserve_existing":true}"#,
        r#"{"type":"vr_view_settings","display_mode":"original","max_speakers":2,"bilingual_pair_count":1,"show_speaker_labels":false}"#,
        r#"{"type":"heartbeat"}"#,
    ];
    let events = adapter_events(&frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::RefinedTarget(t) if t.text == "refined" && t.sentence_id.as_deref() == Some("A"))));
    assert!(events.iter().any(|e| matches!(e, CaptionEvent::Clear { preserve_existing: true })));
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::ViewSettingsChanged(s) if s.display_mode == DisplayMode::Original && s.max_speakers == 2)));
    assert!(events.iter().any(|e| matches!(e, CaptionEvent::Activity)));
}

#[tokio::test]
async fn explicit_end_token_becomes_source_end() {
    let frames = [r#"{"type":"update","final_tokens":[{"text":"<end>","speaker":"1","translation_status":"original","is_final":true}],"non_final_tokens":[]}"#];
    let events = adapter_events(&frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::SourceEnd { speaker, sentence_id: None } if *speaker == SpeakerKey::Diarized("1".into()))));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --lib protocol -- --nocapture`

- [ ] **Step 3: Implement the adapter**

- Port the transport from `bridge.rs`; delete the `/ws` vs `/vr_ws` branch. `connect` rejects a
  manifest whose URL path is not `/ws` with `BridgeError::UnsupportedPath`.
- Whole-frame final-token fingerprint suppresses only the identical immediately-previous frame.
- Per `AccumKey`: separator arms `replace_on_next_{source,translation}`; a strictly longer
  cumulative prefix replaces; otherwise append exactly. Emit one committed event per changed key,
  carrying `language` from the token.
- `non_final_tokens`: group source tokens by speaker; emit one `SourceLive` for the **last**
  speaker group. Emit one `TargetDraft` per translation speaker group.
- A final token whose text is `<end>` emits `SourceEnd { speaker, sentence_id }` and does not
  enter an accumulator.
- `refine_result` -> `RefinedTarget`. `clear` -> `Clear`. `vr_view_settings` -> validate; invalid
  is logged and downgraded to `Activity`. Heartbeat/unknown -> `Activity`.
- Malformed JSON or a malformed frame is logged and downgraded to `Activity`; it never returns
  `Err` and never clears state. Only transport/socket failures return `Err`.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --lib protocol` — expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/protocol.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add typed /ws protocol adapter"
```

---

### Task 4: Projection module

**Files:**
- Create: `vr_overlay/src/projection.rs`
- Create: `vr_overlay/tests/projection.rs`
- Modify: `vr_overlay/src/lib.rs`

**Interfaces:**

```rust
pub const LIVE_SOURCE_HOLD: Duration = Duration::from_millis(1200);

pub fn project(state: &TranscriptState, settings: &VrViewSettings, now: Instant) -> HudFrame;
```

Projection rules:
- `original`: per speaker, the speaker's record with the highest `local_ordinal` whose
  `source.phase` is `Committed`/`Refined`; text = source text; `kind = Settled`. Speaker rows are
  bottom-aligned in slots 1..=3, ordered by `speaker_recency` (oldest at the top).
- `translation`: per speaker, the record with the highest `local_ordinal` among records with a
  non-empty target; text = target text; `kind` = `Draft` if `target.phase == Draft` else
  `Settled`. Same bottom-aligned slots. An older sentence's late target never replaces the
  speaker's newest row.
- `both`: records with non-empty `source.text` and non-empty `target.text`, ordered by
  `local_ordinal`, capacity `bilingual_pair_count`. One pair -> target slot 2, source slot 3.
  Two pairs -> older pair slots 0,1; newer pair slots 2,3. Target `UpperPrimary`, source
  `UpperSecondary`. `speaker_label = None`.
- Live row: slot 4. `Hidden` -> none. `Streaming(s)` -> `LiveSource(row from s)`. `Settled` ->
  show when `now < closed_at + LIVE_SOURCE_HOLD`, or when the mode-specific handoff is absent;
  clear when the hold elapsed and the handoff exists. Handoff:
  - `original`: the settled sentence has a `Committed`/`Refined` source.
  - `translation`: the settled sentence has a non-empty target.
  - `both`: the settled sentence has a non-empty source and target (pair visible).
- `language` is copied from the owning track; `speaker_label` is `Some(speaker)` only when
  `show_speaker_labels` and the mode is not `both`.

- [ ] **Step 1: Write failing projection tests**

```rust
use rinbridge_overlay::hud::{HudRowKind, HudRowRole};
use rinbridge_overlay::projection::{project, LIVE_SOURCE_HOLD};
use rinbridge_overlay::transcript::{
    CaptionEvent, CommittedSource, LiveSourceSnapshot, SpeakerKey, TargetUpdate, TranscriptState,
};
use rinbridge_overlay::views::{DisplayMode, VrViewSettings};
use std::time::{Duration, Instant};

fn settings(mode: DisplayMode) -> VrViewSettings {
    VrViewSettings { display_mode: mode, max_speakers: 3, bilingual_pair_count: 1, show_speaker_labels: true }
}
fn apply(s: &mut TranscriptState, e: CaptionEvent, now: Instant) { s.apply(&e, now); }
fn commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()),
        text: text.into(), language: Some("en".into()),
    })
}
fn live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: None,
        text: text.into(), language: Some("en".into()),
    })
}
fn t_draft(speaker: &str, id: Option<&str>, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: id.map(str::to_owned),
        text: text.into(), language: Some("fr".into()),
    })
}
fn t_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()),
        text: text.into(), language: Some("fr".into()),
    })
}

fn row_text(slots: &[Option<rinbridge_overlay::hud::HudRow>; 5], i: usize) -> Option<&str> {
    slots[i].as_ref().map(|r| r.text.as_str())
}

#[test]
fn original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for (sp, txt) in [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2")] {
        apply(&mut s, commit(sp, &format!("{sp}-{txt}"), txt), t);
    }
    let f = project(&s, &settings(DisplayMode::Original), t);
    assert_eq!(f.slots[0], None);
    assert_eq!(row_text(&f.slots, 1), Some("b1"));
    assert_eq!(row_text(&f.slots, 2), Some("c1"));
    assert_eq!(row_text(&f.slots, 3), Some("a2"));
    assert_eq!(f.slots[4], None);
}

#[test]
fn original_capacity_three_evicts_least_recent_speaker() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for (sp, txt) in [("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")] {
        apply(&mut s, commit(sp, txt, txt), t);
    }
    let f = project(&s, &settings(DisplayMode::Original), t);
    assert_eq!(row_text(&f.slots, 1), Some("b"));
    assert_eq!(row_text(&f.slots, 2), Some("c"));
    assert_eq!(row_text(&f.slots, 3), Some("d"));
}

#[test]
fn translation_shows_only_the_newest_target_per_speaker() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    apply(&mut s, commit("1", "A", "a"), t);
    apply(&mut s, commit("1", "B", "b"), t);
    apply(&mut s, t_commit("1", "A", "ay"), t);
    apply(&mut s, t_commit("1", "B", "bee"), t);
    let f = project(&s, &settings(DisplayMode::Translation), t);
    assert_eq!(row_text(&f.slots, 3), Some("bee"));
    // late target for the older sentence updates the reducer but not the row
    apply(&mut s, t_commit("1", "A", "ay2"), t);
    let f = project(&s, &settings(DisplayMode::Translation), t);
    assert_eq!(row_text(&f.slots, 3), Some("bee"));
}

#[test]
fn translation_draft_is_upgraded_in_place_by_final() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    apply(&mut s, commit("1", "A", "a"), t);
    apply(&mut s, t_draft("1", Some("A"), "bon"), t);
    let draft = project(&s, &settings(DisplayMode::Translation), t);
    assert_eq!(row_text(&draft.slots, 3), Some("bon"));
    assert_eq!(draft.slots[3].as_ref().unwrap().kind, HudRowKind::Draft);
    apply(&mut s, t_commit("1", "A", "bonjour"), t);
    let final_ = project(&s, &settings(DisplayMode::Translation), t);
    assert_eq!(row_text(&final_.slots, 3), Some("bonjour"));
    assert_eq!(final_.slots[3].as_ref().unwrap().kind, HudRowKind::Settled);
}

#[test]
fn both_mode_one_pair_occupies_slots_two_and_three() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    apply(&mut s, commit("1", "A", "hello"), t);
    apply(&mut s, t_draft("1", Some("A"), "bonjour"), t);
    let f = project(&s, &settings(DisplayMode::Both), t);
    assert_eq!(f.slots[0], None);
    assert_eq!(f.slots[1], None);
    assert_eq!(row_text(&f.slots, 2), Some("bonjour"));
    assert_eq!(f.slots[2].as_ref().unwrap().role, HudRowRole::UpperPrimary);
    assert_eq!(row_text(&f.slots, 3), Some("hello"));
    assert_eq!(f.slots[3].as_ref().unwrap().role, HudRowRole::UpperSecondary);
    assert!(f.slots.iter().flatten().all(|r| r.speaker_label.is_none()));
}

#[test]
fn both_mode_two_pairs_occupy_slots_zero_through_three() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for id in ["A", "B"] {
        apply(&mut s, commit("1", id, id), t);
        apply(&mut s, t_draft("1", Some(id), &format!("t{id}")), t);
    }
    let two = VrViewSettings { bilingual_pair_count: 2, ..settings(DisplayMode::Both) };
    let f = project(&s, &two, t);
    assert_eq!(row_text(&f.slots, 0), Some("tA"));
    assert_eq!(row_text(&f.slots, 1), Some("A"));
    assert_eq!(row_text(&f.slots, 2), Some("tB"));
    assert_eq!(row_text(&f.slots, 3), Some("B"));
}

#[test]
fn live_row_always_occupies_slot_four() {
    let mut s = TranscriptState::default();
    apply(&mut s, live("1", "now speaking"), Instant::now());
    for mode in [DisplayMode::Original, DisplayMode::Translation, DisplayMode::Both] {
        let f = project(&s, &settings(mode), Instant::now());
        let row = f.slots[4].as_ref().expect("live row present");
        assert_eq!(row.role, HudRowRole::LiveSource);
        assert_eq!(row.text, "now speaking");
        assert!(f.slots[0..4].iter().all(Option::is_none));
    }
}

#[test]
fn settled_source_hold_not_expired_keeps_row_then_drops_after_handoff() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    apply(&mut s, live("1", "hello wor"), t0);
    apply(&mut s, commit("1", "A", "hello world"), t0);
    apply(&mut s, t_draft("1", Some("A"), "bonjour"), t0 + Duration::from_millis(500));
    let early = project(&s, &settings(DisplayMode::Translation), t0 + Duration::from_millis(600));
    assert_eq!(row_text(&early.slots, 4), Some("hello world"), "hold not expired");
    let late = project(&s, &settings(DisplayMode::Translation), t0 + LIVE_SOURCE_HOLD + Duration::from_millis(1));
    assert_eq!(late.slots[4], None, "hold expired and handoff visible");
}

#[test]
fn missing_translation_keeps_settled_source_visible() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    apply(&mut s, live("1", "hello wor"), t0);
    apply(&mut s, commit("1", "A", "hello world"), t0);
    let late = project(&s, &settings(DisplayMode::Translation), t0 + Duration::from_secs(10));
    assert_eq!(row_text(&late.slots, 4), Some("hello world"));
}

#[test]
fn language_propagates_to_hud_rows() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    apply(&mut s, commit("1", "A", "hello"), t);
    apply(&mut s, t_commit("1", "A", "bonjour"), t);
    let f = project(&s, &settings(DisplayMode::Both), t);
    assert_eq!(f.slots[2].as_ref().unwrap().language.as_deref(), Some("fr"));
    assert_eq!(f.slots[3].as_ref().unwrap().language.as_deref(), Some("en"));
}

#[test]
fn speaker_labels_are_separate_and_both_mode_has_none() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    apply(&mut s, commit("1", "A", "hello"), t);
    let original = project(&s, &settings(DisplayMode::Original), t);
    let row = original.slots[3].as_ref().unwrap();
    assert_eq!(row.text, "hello");
    assert_eq!(row.speaker_label.as_deref(), Some("1"));
    let both = project(&s, &settings(DisplayMode::Both), t);
    assert!(both.slots.iter().flatten().all(|r| r.speaker_label.is_none()));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test projection -- --nocapture`

- [ ] **Step 3: Implement the three projections and the live-row builder**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test projection` — expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/projection.rs vr_overlay/tests/projection.rs vr_overlay/src/lib.rs
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
- `CaptionRenderer::render_hud_frame(&self, frame: &HudFrame, presentation: &CaptionPresentation) -> Result<RenderedFrame, CaptionRenderError>`.
  The renderer consumes `crate::hud::HudFrame` directly; there is no renderer mirror type.
- Block id convention: `slot-<index>`, e.g. `slot-3`.
- Slot index -> fixed top: `HUD_FIRST_SLOT_TOP_PX + index as f32 * HUD_SLOT_STRIDE_PX`.
- Left origin `HUD_TEXT_LEFT_PX`; content width `HUD_CONTENT_WIDTH_PX`.
- Role -> font size: `UpperPrimary` primary scale; `UpperSecondary`/`LiveSource` secondary scale;
  all multiplied by `presentation.text_scale`.
- The renderer alone composes `speaker_label` into the measured line (label prefix + body).
- One physical line per slot. Overflow uses a trailing ellipsis except `LiveSource`, which keeps
  the newest tail with a leading ellipsis.
- `LineCacheKey` and `LayoutCacheKey` gain `speaker_label: Option<String>` and `kind: HudRowKind`,
  so a label or draft/final change cannot reuse a stale layout.
- Pure truncation helper, exported for tests:
  `pub fn fit_row_text(text: &str, max_advance: f32, direction: Truncation, advance: &dyn Fn(char) -> f32) -> String;`
  with `pub enum Truncation { Trailing, Leading }`.

- [ ] **Step 1: Write failing renderer tests**

```rust
use rinbridge_overlay::hud::{HudFrame, HudRow, HudRowKind, HudRowRole};
use rinbridge_overlay::renderer::Truncation;
use rinbridge_overlay::{fit_row_text, CaptionPresentation, CaptionRenderer};

fn row(role: HudRowRole, text: &str) -> HudRow {
    HudRow { role, kind: HudRowKind::Settled, text: text.into(), speaker_label: None, language: None, sentence: None }
}

fn frame_with(entries: &[(usize, HudRow)]) -> HudFrame {
    let mut f = HudFrame::default();
    for (i, r) in entries {
        f.slots[*i] = Some(r.clone());
    }
    f
}

fn line_of<'a>(frame: &'a rinbridge_overlay::RenderedFrame, index: usize) -> &'a str {
    &frame.layout().visible_blocks.iter().find(|b| b.id == format!("slot-{index}")).unwrap().primary_lines[0].text
}

#[test]
fn hud_rows_use_fixed_left_origins_and_do_not_move_when_text_grows() {
    let renderer = CaptionRenderer::new().unwrap();
    let short = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, "hi"))]), &CaptionPresentation::default()).unwrap();
    let long = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, "hi there everyone"))]), &CaptionPresentation::default()).unwrap();
    let a = short.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    let b = long.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    assert!((a.bounds.left_px - b.bounds.left_px).abs() < 0.01);
    assert!((a.bounds.left_px - rinbridge_overlay::HUD_TEXT_LEFT_PX).abs() < 0.01);
    assert!((a.bounds.top_px - b.bounds.top_px).abs() < 0.01);
}

#[test]
fn live_row_keeps_newest_tail_with_leading_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "α".repeat(400) + "NEWESTTAIL";
    let frame = renderer.render_hud_frame(&frame_with(&[(4, row(HudRowRole::LiveSource, &text))]), &CaptionPresentation::default()).unwrap();
    let line = line_of(&frame, 4);
    assert!(line.starts_with('…'));
    assert!(line.ends_with("NEWESTTAIL"));
}

#[test]
fn single_language_row_overflow_uses_trailing_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "BEGINNING".to_string() + &"x".repeat(400);
    let frame = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, &text))]), &CaptionPresentation::default()).unwrap();
    let line = line_of(&frame, 3);
    assert!(line.starts_with("BEGINNING"));
    assert!(line.ends_with('…'));
}

#[test]
fn cjk_prohibition_never_strands_punctuation_at_a_truncated_edge() {
    let advance = |_ch: char| 1.0f32;
    assert_eq!(fit_row_text("你好（世界", 3.0, Truncation::Trailing, &advance), "你好…");
    assert_eq!(fit_row_text("世界）好你", 3.0, Truncation::Leading, &advance), "…好你");
}

#[test]
fn renderer_is_the_only_speaker_label_composer() {
    let renderer = CaptionRenderer::new().unwrap();
    let mut r = row(HudRowRole::UpperPrimary, "hello");
    r.speaker_label = Some("S1".into());
    let frame = renderer.render_hud_frame(&frame_with(&[(3, r)]), &CaptionPresentation::default()).unwrap();
    let line = line_of(&frame, 3);
    assert!(line.starts_with("S1"));
    assert!(line.contains("hello"));
}

#[test]
fn speaker_label_is_part_of_the_layout_cache_key() {
    let renderer = CaptionRenderer::new().unwrap();
    let mut a = row(HudRowRole::UpperPrimary, "hello");
    a.speaker_label = Some("S1".into());
    let mut b = row(HudRowRole::UpperPrimary, "hello");
    b.speaker_label = Some("S2".into());
    let fa = renderer.render_hud_frame(&frame_with(&[(3, a)]), &CaptionPresentation::default()).unwrap();
    let fb = renderer.render_hud_frame(&frame_with(&[(3, b)]), &CaptionPresentation::default()).unwrap();
    let ka = fa.layout().visible_blocks[0].block_cache_key();
    let kb = fb.layout().visible_blocks[0].block_cache_key();
    assert_ne!(ka, kb);
}

#[test]
fn empty_slots_are_transparent_and_five_slots_are_reserved() {
    let renderer = CaptionRenderer::new().unwrap();
    let frame = renderer.render_hud_frame(&frame_with(&[(4, row(HudRowRole::LiveSource, "x"))]), &CaptionPresentation::default()).unwrap();
    assert_eq!(frame.layout().visible_blocks.len(), 1);
    assert_eq!(frame.layout().visible_blocks[0].id, "slot-4");
}

#[test]
fn draft_and_final_rows_have_identical_geometry() {
    let renderer = CaptionRenderer::new().unwrap();
    let mut draft = row(HudRowRole::UpperPrimary, "bon");
    draft.kind = HudRowKind::Draft;
    let mut final_ = row(HudRowRole::UpperPrimary, "bon");
    final_.kind = HudRowKind::Settled;
    let a = renderer.render_hud_frame(&frame_with(&[(3, draft)]), &CaptionPresentation::default()).unwrap();
    let b = renderer.render_hud_frame(&frame_with(&[(3, final_)]), &CaptionPresentation::default()).unwrap();
    let ba = &a.layout().visible_blocks[0].bounds;
    let bb = &b.layout().visible_blocks[0].bounds;
    assert!((ba.left_px - bb.left_px).abs() < 0.01);
    assert!((ba.top_px - bb.top_px).abs() < 0.01);
    assert!((ba.right_px - bb.right_px).abs() < 0.01);
    assert!((ba.bottom_px - bb.bottom_px).abs() < 0.01);
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test renderer hud -- --nocapture`

- [ ] **Step 3: Implement the slot layout path and `fit_row_text`**

Export `fit_row_text`, `Truncation`, `HUD_TEXT_LEFT_PX` from `lib.rs`. Keep the old
`render_blocks` path only until Task 8 removes its last user.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test renderer hud` — expected: all pass.

- [ ] **Step 5: Commit**

```powershell
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

- Implement in every implementor: `OpenVrOverlay` (delegates to backend), `FakeOpenVr`,
  `ShellSubmitter`, `RecordingSubmitter`. Clamp to `0.0..=1.0`. `FakeOpenVr` gains
  `last_alpha: Cell<Option<f32>>` and `pub fn last_alpha(&self) -> Option<f32>`.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn overlay_alpha_is_clamped_and_reaches_the_submitter() {
    let mut fake = FakeOpenVr::default();
    assert_eq!(fake.last_alpha(), None);
    fake.set_overlay_alpha(0.5).unwrap();
    assert_eq!(fake.last_alpha(), Some(0.5));
    fake.set_overlay_alpha(2.0).unwrap();
    assert_eq!(fake.last_alpha(), Some(1.0));
    fake.set_overlay_alpha(-1.0).unwrap();
    assert_eq!(fake.last_alpha(), Some(0.0));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml overlay_alpha_is_clamped -- --nocapture`

Expected: compile error — the trait method does not exist.

- [ ] **Step 3: Implement the trait method and every implementor**

Run `rg "impl (crate::openvr::)?OverlayFrameSubmitter" vr_overlay/src vr_overlay/tests` and add
`set_overlay_alpha` to each result. The absence of a default body guarantees a missed real
implementation fails compilation.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test runtime overlay_alpha` — expected: 1
passed.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/openvr.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): require explicit compositor overlay alpha"
```

---

### Task 7: Runtime coordinator

**Files:**
- Rewrite: `vr_overlay/src/runtime.rs`
- Modify: `vr_overlay/src/lib.rs`
- Create: `vr_overlay/tests/coordinator.rs`
- Modify: `vr_overlay/tests/runtime.rs`

**Interfaces:**

```rust
pub trait Clock { fn now(&self) -> Instant; }
pub struct SystemClock;
pub struct FakeClock { inner: Rc<Cell<Instant>> }
impl FakeClock {
    pub fn new(start: Instant) -> Self;
    pub fn advance(&self, by: Duration);
    pub fn set(&self, to: Instant);
}

pub trait ParentLiveness { fn alive(&self) -> bool; }
pub struct ProcessLiveness { pub parent_pid: u32 }
pub struct FakeParent { pub alive: Cell<bool> }

pub enum VisibilityAction { SetAlpha(f32), Hide }

pub struct VisibilityController {
    visible: bool,
    last_activity: Instant,
    fade_started: Option<Instant>,
    last_alpha: f32,
}
impl VisibilityController {
    pub fn new(now: Instant) -> Self;
    pub fn on_activity(&mut self, now: Instant);
    /// Returns the alpha/hide action to emit, if the alpha changed.
    pub fn tick(&mut self, now: Instant) -> Option<VisibilityAction>;
    pub fn visible(&self) -> bool;
    pub fn next_deadline(&self) -> Option<Instant>;
}

pub const LIVE_SOURCE_HOLD: Duration;      // from projection
pub const SILENCE_BEFORE_FADE: Duration;   // 4.0 s
pub const FADE_DURATION: Duration;         // 1.2 s
pub const FADE_STEP: Duration;             // 50 ms
pub const PARENT_POLL_INTERVAL: Duration;  // 2.0 s
```

`RuntimeCoordinator` owns `TranscriptState`, `VrViewSettings`, last submitted `HudFrame`,
`VisibilityController`, `last_frame`, counters, and reconnect/backoff state.

Event-loop rules:
- Submit one **transparent empty `HudFrame`** immediately after renderer/overlay init; on its
  success emit `overlay_ready`. Readiness never waits for the first caption.
- After the first event, drain with `try_next_event()` until `None`, apply all events to the
  reducer, project once, submit only if the frame changed.
- Compute `next_deadline(now)` = the minimum of: live-row hold expiry, silence-start
  (last_activity + `SILENCE_BEFORE_FADE`), next fade step, and the parent-poll deadline.
  `tokio::select!` between `protocol.next_event()` and `sleep_until(next_deadline)`.
- On a deadline wake call `tick(now)`, which re-projects (so a target arriving inside the hold
  still clears the live row when the hold expires) and emits alpha actions.
- `SourceLive` with text or any new visible target/refinement call `on_activity`.
  `Activity`/heartbeats never do.
- `ViewSettingsChanged` validates; invalid is ignored.
- Startup retries connect until `startup_deadline_ms` while `parent.alive()`.
- Disconnect after readiness: clear live input, hide, reconnect with backoff `250ms` doubling to
  `5s`; reconnect resets transcript state.
- Parent poll at `PARENT_POLL_INTERVAL`; dead parent exits cleanly.

- [ ] **Step 1: Write failing coordinator tests**

```rust
use rinbridge_overlay::hud::{HudRowRole};
use rinbridge_overlay::runtime::{
    FakeClock, VisibilityAction, VisibilityController, SILENCE_BEFORE_FADE, FADE_DURATION,
};
use std::time::{Duration, Instant};

#[test]
fn visibility_fades_over_alpha_then_hides_and_wakes_instantly() {
    let t = Instant::now();
    let mut v = VisibilityController::new(t);
    v.on_activity(t);
    assert_eq!(v.tick(t), None);
    assert_eq!(v.tick(t + SILENCE_BEFORE_FADE), Some(VisibilityAction::SetAlpha(1.0)));
    assert_eq!(v.tick(t + SILENCE_BEFORE_FADE + Duration::from_millis(600)), Some(VisibilityAction::SetAlpha(0.5)));
    assert_eq!(v.tick(t + SILENCE_BEFORE_FADE + FADE_DURATION), Some(VisibilityAction::Hide));
    assert!(!v.visible());
    v.on_activity(t + SILENCE_BEFORE_FADE + Duration::from_millis(1300));
    assert_eq!(v.tick(t + SILENCE_BEFORE_FADE + Duration::from_millis(1300)), Some(VisibilityAction::SetAlpha(1.0)));
    assert!(v.visible());
}

#[test]
fn hold_expiry_reprojects_without_a_new_caption_event() {
    let mut c = RuntimeCoordinator::for_test();
    let t0 = c.now();
    c.push_for_test(src_live("1", "hello wor"), t0);
    c.push_for_test(src_commit("1", "A", "hello world"), t0);
    c.push_for_test(target_draft("1", Some("A"), "bonjour"), t0 + Duration::from_millis(500));
    c.render_for_test(t0 + Duration::from_millis(600));
    assert!(c.last_frame_for_test().slots[4].is_some(), "hold not expired");
    // No new protocol event; only the deadline tick.
    c.tick_for_test(t0 + Duration::from_millis(1201));
    assert!(c.last_frame_for_test().slots[4].is_none(), "hold expired, handoff visible");
}

#[test]
fn unchanged_projection_does_not_submit_a_texture() {
    let mut c = RuntimeCoordinator::for_test();
    let t = c.now();
    c.push_for_test(src_live("1", "same"), t);
    c.push_for_test(src_live("1", "same"), t);
    c.render_for_test(t);
    assert_eq!(c.submit_count_for_test(), 1);
}

#[test]
fn queued_events_coalesce_into_one_render() {
    let mut c = RuntimeCoordinator::for_test();
    let t = c.now();
    c.push_for_test(src_live("1", "hello"), t);
    c.push_for_test(src_live("1", "hello wor"), t);
    c.drain_and_render_for_test(t);
    assert_eq!(c.submit_count_for_test(), 1);
    assert_eq!(c.last_frame_for_test().slots[4].as_ref().unwrap().text, "hello wor");
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
fn parent_death_exits_cleanly() {
    let mut c = RuntimeCoordinator::for_test_with_parent(false);
    c.poll_parent_for_test(c.now());
    assert!(c.should_exit_for_test());
}

#[test]
fn transparent_empty_frame_is_submitted_before_ready() {
    let mut c = RuntimeCoordinator::for_test();
    let t = c.now();
    c.start_for_test(t);
    assert!(c.ready_for_test());
    assert_eq!(c.submit_count_for_test(), 1);
    assert!(c.last_frame_for_test().slots.iter().all(Option::is_none));
}

#[test]
fn startup_deadline_expires_without_desktop() {
    let mut c = RuntimeCoordinator::for_test_with_parent(true);
    c.set_deadline_for_test(Duration::from_millis(3000));
    assert!(c.startup_tick_for_test(Duration::from_millis(3001)).is_err());
}

#[test]
fn reconnect_resets_upper_state() {
    let mut c = RuntimeCoordinator::for_test();
    let t = c.now();
    c.push_for_test(src_commit("1", "A", "hello"), t);
    c.push_for_test(target_draft("1", Some("A"), "bonjour"), t);
    c.on_disconnect_for_test();
    assert!(c.transcript_for_test().sentences.is_empty());
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator -- --nocapture`

- [ ] **Step 3: Implement the coordinator**

Keep the `StartupError` exit-code table minus `BridgeAuth`. Keep `run_cli`, `run_with_manifest`,
and the HMD retry preflight.

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator` — expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/runtime.rs vr_overlay/src/lib.rs vr_overlay/tests/coordinator.rs vr_overlay/tests/runtime.rs
git commit -m "feat(vr): add projection-driven runtime coordinator"
```

---

### Task 8: Remove the snapshot protocol, legacy state, and the obsolete DLL sidecar

**Files:**
- Delete: `vr_overlay/src/bridge.rs`, `vr_overlay/src/desktop_caption.rs`
- Delete: `vr_overlay/tests/state.rs`
- Delete: `vr_overlay/vendor/openvr_api.dll`, `vr_overlay/vendor/README.md`
- Trim: `vr_overlay/src/state.rs` (keep only `OverlayCalibration` and its non-zero defaults)
- Modify: `vr_overlay/src/openvr.rs` (line ~983 `OverlayPresentationCalibration::default()`
  becomes `OverlayCalibration::default()`)
- Modify: `vr_overlay/src/lib.rs` (prune removed exports)
- Modify: `vr_overlay/tests/renderer.rs`, `vr_overlay/tests/runtime.rs` (drop snapshot imports and
  the snapshot-based tests; keep the alpha test)
- Modify: `vr_overlay/scripts/verify.ps1` (delete the `vendor/openvr_api.dll` precheck and any
  vendored-DLL text)
- Test: full suite

Removed names: `OverlayPresentationSnapshot`, `OverlayPresentationBlock`,
`OverlayPresentationBlockVariant`, `OverlayPresentationCalibration`, `OverlayState`,
`OverlayStateScene`, `OverlayStateSlot`, `PresentationScene`, `PresentationSlot`, `RuntimeState`,
`VISIBLE_SLOT_CAP`, `SLOT_ROW_STRIDE_PX`, `FIRST_SLOT_TOP_PX`, `CaptionUpdate`,
`OverlayBridgeEvent`, `BridgeControl`, `BridgeClient`'s authenticated branch, and `render_blocks`
if no live user remains.

- [ ] **Step 1: Delete modules, types, vendored DLL, and the verify precheck**

```powershell
git rm vr_overlay/src/bridge.rs vr_overlay/src/desktop_caption.rs vr_overlay/tests/state.rs
git rm vr_overlay/vendor/openvr_api.dll vr_overlay/vendor/README.md
```

Then in `state.rs` keep only `OverlayCalibration` plus
`impl Default for OverlayCalibration` (anchor `head_locked`, offset_y `-0.45`, distance `1.1`,
text_scale `1.0`, background_alpha `0.24`), and delete the rest of the file.

- [ ] **Step 2: Migrate the remaining call sites by name**

- `lib.rs`: remove every re-export listed above; add `pub use state::OverlayCalibration;`.
- `openvr.rs`: replace the `OverlayPresentationCalibration::default()` call.
- `tests/renderer.rs`: delete `overlay_state_*` / snapshot tests; keep only `hud_*` and the
  existing font/glyph tests.
- `tests/runtime.rs`: delete tests importing `OverlayPresentation*`, `OverlayState`,
  `BridgeClient`, `CaptionUpdate`, `OverlayBridgeEvent`; keep the alpha and CLI tests.
- `scripts/verify.ps1`: delete the `Test-Path ... vendor\openvr_api.dll` block and the
  `Fail-Environment 'vr_overlay/vendor/openvr_api.dll is missing'` line.

- [ ] **Step 3: Run the full suite and fix the remaining compile errors**

`cargo test --manifest-path vr_overlay/Cargo.toml` — expected: zero failures, no references to
the removed names.

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
- Modify: `vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md` (only if the
  implemented names drifted)
- Test: `vr_overlay/tests/coordinator.rs`

**Interfaces:** diagnostics records `event_kind`, `speaker_hash`, `sentence_ordinal`,
`dirty_slots`, `render_ms`, `submit_sequence`.

- [ ] **Step 1: Write failing diagnostic tests**

```rust
#[test]
fn basic_diagnostics_never_contain_caption_text() {
    let log = render_logs(OverlayLoggingMode::Basic, &[src_commit("1", "A", "SECRETTEXT")]);
    assert!(!log.contains("SECRETTEXT"));
    assert!(log.contains("event_kind=source_committed"));
    assert!(log.contains("submit_sequence="));
}

#[test]
fn detailed_diagnostics_cap_preview_length() {
    let long = "x".repeat(200);
    let log = render_logs(OverlayLoggingMode::Detailed, &[src_commit("1", "A", &long)]);
    let preview = log.split("preview=").nth(1).unwrap().lines().next().unwrap();
    assert!(preview.chars().count() <= 80);
}
```

(`render_logs` drives the coordinator with a recording logger and returns the captured output.)

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator diagnostics -- --nocapture`

- [ ] **Step 3: Implement the diagnostics**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator diagnostics` — expected: all
pass.

- [ ] **Step 5: Update `AGENTS.md` and `README.md`**

Confirm `AGENTS.md` matches the implemented API (it was written in the bootstrap commit) and
finish the `README.md` contract section: v7 envelope, no `session_token`, one
`RinBridgeOverlay.exe`, no sidecar DLL.

- [ ] **Step 6: Commit**

```powershell
git add vr_overlay/src/logging.rs vr_overlay/src/runtime.rs vr_overlay/tests/coordinator.rs vr_overlay/AGENTS.md vr_overlay/README.md
git commit -m "chore(vr): add HUD diagnostics and update the runtime contract"
```

---

### Task 10: Completion gate, packaging probe, and HMD acceptance

**Files:**
- Modify: `vr_overlay/scripts/verify.ps1`

- [ ] **Step 1: Replace the vendored-DLL precheck with a build/font preflight, and add the packaging probe**

After `cargo build --release`, append:

```powershell
$exe = Join-Path $repoRoot 'vr_overlay\target\release\RinBridgeOverlay.exe'
$strayDlls = @(Get-ChildItem -LiteralPath (Split-Path $exe) -Filter '*.dll' -File)
if ($strayDlls) { throw "release dir contains DLLs: $($strayDlls.Name -join ', ')" }

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("rin-package-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $stage 'RinBridgeOverlay.exe')
$files = @(Get-ChildItem -LiteralPath $stage -File -Recurse)
if ($files.Count -ne 1 -or $files[0].Name -ne 'RinBridgeOverlay.exe') {
    throw "runtime package must contain exactly RinBridgeOverlay.exe; found: $($files.Name -join ', ')"
}
$contract = & (Join-Path $stage 'RinBridgeOverlay.exe') --check-startup-contract
if ($LASTEXITCODE -ne 0 -or $contract.Trim() -ne '{"contract_version":7}') {
    throw "no-sidecar contract probe failed: exit=$LASTEXITCODE output=$contract"
}
Remove-Item -LiteralPath $stage -Recurse -Force
```

- [ ] **Step 2: Run the completion gate**

```powershell
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
powershell -ExecutionPolicy Bypass -File .\vr_overlay\scripts\verify.ps1
```

Changed paths must contain only `vr_overlay/`.

- [ ] **Step 3: Commit**

```powershell
git add vr_overlay/scripts/verify.ps1
git commit -m "chore(vr): add no-sidecar packaging probe"
```

- [ ] **Step 4: Record the physical HMD acceptance checklist as pending**

1. downward viewing position preserved;
2. first live source appears with no perceptible delay;
3. streaming text never shifts horizontally and the live row never moves slots;
4. each mode matches the connection-panel setting;
5. speaker rows never duplicate one speaker;
6. both mode never shows a target against the wrong source;
7. idle CPU/GPU negligible and no visible drops under rapid tokens;
8. fade, instant wake, desktop exit, and reconnect behave correctly.

---

## Verification Matrix

| Requirement | Test |
| --- | --- |
| v7 envelope, settings validation | Task 1 `views` tests |
| Committed full text frozen on settle | `asr_commit_settles_the_full_committed_text` |
| Empty non-finals do not settle | `empty_non_final_does_not_settle_the_live_row` |
| Explicit end settles | `explicit_source_end_settles_with_current_text` |
| No-ID target binds only to open sentence | `target_without_id_binds_to_open_sentence`, `target_without_id_is_dropped_when_no_open_sentence` |
| Older-sentence target retained by reducer | `late_target_for_older_sentence_is_kept_in_the_reducer` |
| `clear(true)` drops drafts | `clear_true_keeps_settled_records_and_drops_drafts` |
| Language on both tracks / rows | `language_is_recorded_on_both_tracks`, `language_propagates_to_hud_rows` |
| Segmented final accumulation | `final_tokens_segment_by_sentence_and_speaker` |
| One live row from last speaker | `multi_speaker_non_final_emits_one_live_event_for_the_last_speaker` |
| Malformed frames ignored | `malformed_frames_are_ignored_without_breaking_state` |
| Original LRU bottom-aligned slots | `original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered` |
| Translation newest-target + late target | `translation_shows_only_the_newest_target_per_speaker` |
| Draft -> final in place | `translation_draft_is_upgraded_in_place_by_final` |
| Both mode fixed slots | `both_mode_one_pair_occupies_slots_two_and_three`, `both_mode_two_pairs_occupy_slots_zero_through_three` |
| Live always slot 4 | `live_row_always_occupies_slot_four` |
| Hold expiry then handoff | `settled_source_hold_not_expired_keeps_row_then_drops_after_handoff` |
| Missing translation keeps source | `missing_translation_keeps_settled_source_visible` |
| Fixed origins / ellipsis / CJK | Task 5 renderer tests |
| Label cache key | `speaker_label_is_part_of_the_layout_cache_key` |
| Mandatory alpha clamping | `overlay_alpha_is_clamped_and_reaches_the_submitter` |
| Alpha fade schedule | `visibility_fades_over_alpha_then_hides_and_wakes_instantly` |
| Hold-expiry reprojection | `hold_expiry_reprojects_without_a_new_caption_event` |
| Transparent frame before ready | `transparent_empty_frame_is_submitted_before_ready` |
| Parent death / reconnect / deadline | Task 7 coordinator tests |
| No-sidecar single-file package | Task 10 packaging probe |
| Rust-only PR boundary | Task 10 gate |
| Physical HMD behavior | Task 10 Step 4 (pending) |

## Open Risks

- **Renderer churn.** Land Task 5 as an additive `render_hud_frame` path; remove the old block
  path only in Task 8.
- **Non-blocking drain.** `tokio-tungstenite` has no poll API. Implement `try_next_event` with
  `futures_util::future::poll_immediate` and test with several pre-queued frames.
- **v7 manifest handshake.** The desktop must produce v7 before a packaged end-to-end run.
- **Non-Windows CI.** `renderer/layout.rs` is Windows-gated; new `hud_*` tests must pass on the
  non-Windows heuristic path too.
- **Vendored DLL deletion.** Confirm nothing inside `vr_overlay/` references `vendor/` after
  Task 8; the desktop packaging spec is outside the Rust-only boundary.

## Documentation-Only Bootstrap Commit

Before any code task starts, commit the reworked spec, plan, `AGENTS.md`, and README build
section alone:

```powershell
git add vr_overlay/AGENTS.md vr_overlay/README.md `
  vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md `
  vr_overlay/docs/superpowers/plans/2026-09-12-vr-hud-window-redesign.md
git commit -m "docs(vr): align AGENTS contract, spec, and implementation plan"
```

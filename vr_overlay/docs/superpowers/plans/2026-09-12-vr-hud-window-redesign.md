# VR HUD Window Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Rin VR runtime around the desktop's raw `/ws` stream with three
speaker-aware/bilingual HUD projections, a shared fixed five-slot frame whose slot 4 is always
the universal live row, a change-only render path, and compositor-alpha silence fading with a
consumed live-row lifecycle.

**Architecture:** Pure domain modules plus a coordinator. Dependency order is exact:
**view settings/envelope -> domain types + reducer + shared HUD frame -> protocol adapter ->
projection -> renderer -> OpenVR alpha -> coordinator -> removal -> diagnostics -> gate ->
post-commit re-run.** `src/hud.rs` is the single shared frame value type; projection and renderer
both consume it. The existing D3D11/DirectWrite implementation is reused; only row layout and
one mandatory alpha call are extended. The authenticated `/vr_ws` snapshot protocol and all
snapshot presentation types are removed.

**Tech Stack:** Rust 2021, `tokio`, `tokio-tungstenite`, `futures-util`, `serde`/`serde_json`,
`thiserror`, existing DirectWrite renderer, `openvr_sys` 2.1.3 (statically linked client
binding), PowerShell verification.

**Spec:** `vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md`

## Baseline (verified before planning)

- `cargo 1.97.1`, `rustc 1.97.1`; `cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check`
  clean; `target/release/RinBridgeOverlay.exe` already builds.
- Branch `pr/vr-subtitle-rust-only`.
- Rust-only changed-path boundary holds against `origin/main`.
- `openvr_sys` 2.1.3 `build.rs` does `cargo:rustc-link-lib=static=openvr_api64`; the static
  loader reads `openvrpaths.vrpath` and loads `vrclient_x64.dll`. No sidecar DLL is required.

## Pre-build Local Check (not a repository change)

- `vr_overlay/AGENTS.md` is **git-excluded** through `.git/info/exclude`
  (`# Local agent instructions are workspace guidance, not PR source changes.`). It must never
  appear in a `Files` list, a `git add`, or a commit, and it must not be force-added. Confirm on
  disk that it still matches this contract (v7, no `session_token`, no exit 12, `SourceEnd`,
  `dismiss_settled_live`, static OpenVR linkage); it is workspace guidance only.
- Run the baseline gate once before Task 1: `cargo fmt --check`, `cargo test`, and
  `cargo build --release` must all pass.

## Global Constraints

- Every changed path relative to `origin/main` stays under `vr_overlay/`.
- Product caption protocol is the unauthenticated desktop `/ws` stream only. A manifest path
  other than `/ws` is rejected at startup.
- `update.final_tokens` accumulate per `(sentence_id, speaker, track)`; no global accumulator.
- `update.non_final_tokens` is a replaceable snapshot; exactly one live row per frame, from the
  last speaker in source order.
- The live row is `Hidden | Streaming(snapshot) | Settled { snapshot, closed_at }`; it settles
  only on the owning sentence's commit or an explicit `SourceEnd`, retains the committed full
  source text, and is consumed to `Hidden` exactly once after a visible handoff.
- The HUD frame is `[Option<HudRow>; 5]`; slot 4 is always live.
- `HudRow.text` is body-only; the renderer alone composes `speaker_label`.
- Geometry cache keys include `speaker_label` and exclude `HudRowKind`; `HudRowKind` changes only
  draw color.
- Render only when the projected frame changes.
- Basic logs never contain recognized or translated text.
- Automated tests do not replace the physical SteamVR/HMD acceptance test.

## Resolved Decisions

1. **Manifest contract `v7`.** `EXPECTED_CONTRACT_VERSION = 7`. `session_token` removed; add
   `view_settings` and `calibration`. No v6 compatibility branch.
2. **Exit code 12 removed** with `StartupError::BridgeAuth` / `BridgeError::Auth`.
3. **Fixed five slots.** Slot 4 live. Single-language speaker rows bottom-align in slots 1..=3.
   `both` one pair -> slots 2,3; two pairs -> slots 0,1 and 2,3.
4. **Shared frame types in `src/hud.rs`.**
5. **`SourceEnd` and `Refinement` events**; refinement resolves its owner by `sentence_id`.
6. **Reducer keeps older-sentence targets**; projection prevents overwrite.
7. **One-shot live dismissal** via `TranscriptState::dismiss_settled_live`.
8. **`VisibilityTick` can Show and SetAlpha in the same turn.**
9. **Static OpenVR linkage**; delete `vendor/openvr_api.dll` and the verify precheck.

---

## Shared Test Harness (defined once, used by later tasks)

Create these helpers in the named modules. Every later test refers to them by these signatures.

`vr_overlay/tests/support/mod.rs` (declared as `mod support;` by each integration test):

```rust
use rinbridge_overlay::hud::HudFrame;
use rinbridge_overlay::openvr::OpenVrError;
use rinbridge_overlay::protocol::BridgeError;
use rinbridge_overlay::transcript::{
    CaptionEvent, CommittedSource, LiveSourceSnapshot, Refinement, SpeakerKey, TargetUpdate,
};
use rinbridge_overlay::{
    OverlayCalibration, OverlayFrameSubmitter, OverlayLoggingMode, OverlayManifest,
    RenderedFrame, VrViewSettings, EXPECTED_CONTRACT_VERSION,
};
use futures_util::SinkExt;
use std::sync::{Arc, Mutex};

pub struct RecordingSubmitter {
    pub frames: Arc<Mutex<Vec<HudFrame>>>,
    pub alphas: Arc<Mutex<Vec<f32>>>,
    pub visibles: Arc<Mutex<Vec<bool>>>,
    pub submit_count: usize,
}
impl RecordingSubmitter {
    pub fn new() -> Self {
        Self {
            frames: Arc::new(Mutex::new(Vec::new())),
            alphas: Arc::new(Mutex::new(Vec::new())),
            visibles: Arc::new(Mutex::new(Vec::new())),
            submit_count: 0,
        }
    }
    pub fn frames(&self) -> Vec<HudFrame> { self.frames.lock().unwrap().clone() }
    pub fn last_frame(&self) -> Option<HudFrame> { self.frames().pop() }
    pub fn last_alpha(&self) -> Option<f32> { self.alphas.lock().unwrap().last().copied() }
    pub fn last_visible(&self) -> Option<bool> { self.visibles.lock().unwrap().last().copied() }
}
impl OverlayFrameSubmitter for RecordingSubmitter {
    fn submit_frame(&mut self, _frame: &RenderedFrame) -> Result<(), OpenVrError> { self.submit_count += 1; Ok(()) }
    fn set_overlay_alpha(&mut self, alpha: f32) -> Result<(), OpenVrError> { self.alphas.lock().unwrap().push(alpha.clamp(0.0, 1.0)); Ok(()) }
    fn set_overlay_visible(&mut self, visible: bool) -> Result<(), OpenVrError> { self.visibles.lock().unwrap().push(visible); Ok(()) }
}

pub fn test_manifest_with_url(url: &str) -> OverlayManifest {
    OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "overlay-test".into(),
        bridge_url: url.into(),
        parent_pid: 1,
        startup_deadline_ms: 3000,
        log_dir: std::env::temp_dir()
            .join("rinbridge-overlay-tests")
            .display()
            .to_string(),
        log_level: "INFO".into(),
        locale: "en".into(),
        logging_mode: OverlayLoggingMode::Basic,
        view_settings: VrViewSettings::default(),
        calibration: OverlayCalibration::default(),
    }
}

// Shared CaptionEvent constructors for integration tests.
pub fn src_live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: None,
        text: text.into(), language: Some("en".into()),
    })
}
pub fn src_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()),
        text: text.into(), language: Some("en".into()),
    })
}
pub fn src_end(speaker: &str, id: Option<&str>) -> CaptionEvent {
    CaptionEvent::SourceEnd { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: id.map(str::to_owned) }
}
pub fn target_draft(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()),
        text: text.into(), language: Some("fr".into()),
    })
}
pub fn target_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()),
        text: text.into(), language: Some("fr".into()),
    })
}
pub fn refine(id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::RefinedTarget(Refinement { sentence_id: id.into(), text: text.into(), language: Some("fr".into()) })
}

/// Binds a loopback TcpListener, accepts one WebSocket client, sends each
/// frame as a Text message, then closes. Returns (ws_url, join_handle).
pub fn spawn_scripted_server(frames: Vec<String>) -> (String, tokio::task::JoinHandle<()>) {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let listener = tokio::net::TcpListener::from_std(listener).unwrap();
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
        for frame in frames {
            ws.send(tokio_tungstenite::tungstenite::Message::Text(frame.into()))
                .await
                .unwrap();
        }
        ws.close(None).await.ok();
    });
    (format!("ws://{addr}/ws"), handle)
}

// `render_logs` is defined in Task 9 with the runtime logger.
```

`test_manifest_with_url` builds `OverlayManifest` directly with `contract_version = 7`,
`bridge_url = url`, `view_settings = VrViewSettings::default()`,
`calibration = OverlayCalibration::default()`.

`vr_overlay/tests/support/fakes.rs` (re-exported by `support/mod.rs`) for coordinator tests:

```rust
#[derive(Clone)]
pub struct FakeClock { inner: std::rc::Rc<std::cell::Cell<Instant>> }
impl FakeClock { pub fn new(start: Instant) -> Self; pub fn advance(&self, by: Duration); pub fn set(&self, to: Instant); }
impl Clock for FakeClock { fn now(&self) -> Instant { self.inner.get() } }

pub struct FakeParent { pub alive: std::cell::Cell<bool> }
impl ParentLiveness for FakeParent { fn alive(&self) -> bool { self.alive.get() } }
```

---

### Task 1: View settings and v7 startup envelope

**Files:**
- Create: `vr_overlay/src/views.rs`
- Modify: `vr_overlay/src/lib.rs`
- Modify: `vr_overlay/src/manifest.rs`
- Modify: `vr_overlay/src/runtime.rs` (`default_manifest` only)
- Modify: `vr_overlay/tests/support/mod.rs` (new; `test_manifest_with_url`)
- Test: `vr_overlay/src/views.rs`, `vr_overlay/src/manifest.rs`

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

#[derive(Debug, Error)]
pub enum SettingsError {
    #[error("max_speakers must be 1..=3, got {0}")]
    MaxSpeakers(u8),
    #[error("bilingual_pair_count must be 1..=2, got {0}")]
    BilingualPairCount(u8),
}

impl Default for VrViewSettings {
    fn default() -> Self {
        Self {
            display_mode: DisplayMode::Both,
            max_speakers: 3,
            bilingual_pair_count: 1,
            show_speaker_labels: true,
        }
    }
}
impl VrViewSettings { pub fn validate(&self) -> Result<(), SettingsError>; }
```

`OverlayManifest` fields: `contract_version, app_version, overlay_instance_id, bridge_url,
parent_pid, startup_deadline_ms, log_dir, log_level, locale, logging_mode, view_settings,
calibration`; `session_token` removed. `OverlayManifestSerde` keeps `deny_unknown_fields`.

- [ ] **Step 1: Write failing view-settings tests** (`src/views.rs`)

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
    for s in [
        VrViewSettings { max_speakers: 0, ..Default::default() },
        VrViewSettings { max_speakers: 4, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 0, ..Default::default() },
        VrViewSettings { bilingual_pair_count: 3, ..Default::default() },
    ] {
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

- [ ] **Step 2: Write failing manifest-contract tests** (`src/manifest.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn raw_v7() -> String {
        serde_json::json!({
            "contract_version": 7,
            "app_version": "0.1.0",
            "overlay_instance_id": "x",
            "bridge_url": "ws://127.0.0.1:1/ws",
            "parent_pid": 1,
            "startup_deadline_ms": 3000,
            "log_dir": "/tmp",
            "log_level": "INFO",
            "locale": "en",
            "logging_mode": "basic"
        })
        .to_string()
    }

    #[test]
    fn contract_version_seven_is_accepted() {
        let parsed: OverlayManifestSerde = serde_json::from_str(&raw_v7()).unwrap();
        let m: OverlayManifest = parsed.try_into().unwrap();
        assert!(validate_manifest(&m).is_ok());
    }

    #[test]
    fn contract_version_six_is_rejected() {
        let raw = raw_v7().replace("\"contract_version\":7", "\"contract_version\":6");
        let m: OverlayManifest = serde_json::from_str::<OverlayManifestSerde>(&raw).unwrap().try_into().unwrap();
        assert!(matches!(validate_manifest(&m), Err(StartupError::ContractMismatch(_))));
    }

    #[test]
    fn session_token_in_the_envelope_is_rejected() {
        let mut value: serde_json::Value = serde_json::from_str(&raw_v7()).unwrap();
        value["session_token"] = serde_json::json!("secret");
        assert!(serde_json::from_value::<OverlayManifestSerde>(value).is_err());
    }
}
```

- [ ] **Step 3: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml views -- --nocapture`
`cargo test --manifest-path vr_overlay/Cargo.toml manifest -- --nocapture`

- [ ] **Step 4: Implement `views.rs` and the manifest changes**

Set `EXPECTED_CONTRACT_VERSION = 7`; remove `session_token`; add defaulted `view_settings` and
`calibration` to both structs; update `default_manifest()`. Export `DisplayMode`,
`VrViewSettings`, `SettingsError` from `lib.rs`. Add `tests/support/mod.rs` with
`test_manifest_with_url` and the `RecordingSubmitter` skeleton (alpha/submit bodies finalized in
Task 6).

- [ ] **Step 5: Run and verify GREEN**

Both Step 3 commands pass.

- [ ] **Step 6: Commit**

```powershell
git add vr_overlay/src/views.rs vr_overlay/src/manifest.rs vr_overlay/src/lib.rs vr_overlay/src/runtime.rs vr_overlay/tests/support
git commit -m "feat(vr): add HUD view settings and v7 startup envelope"
```

---

### Task 2: Domain types, shared HUD frame, and transcript reducer

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
pub struct SentenceRecord { pub key: SentenceKey, pub speaker: SpeakerKey, pub source: TextTrack, pub target: TextTrack }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LiveSourceSnapshot { pub speaker: SpeakerKey, pub sentence_id: Option<String>, pub text: String, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedSource { pub speaker: SpeakerKey, pub sentence_id: Option<String>, pub text: String, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetUpdate { pub speaker: SpeakerKey, pub sentence_id: Option<String>, pub text: String, pub language: Option<String> }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refinement { pub sentence_id: String, pub text: String, pub language: Option<String> }

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
    RefinedTarget(Refinement),
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
    pub fn speaker_recency(&self) -> &VecDeque<SpeakerKey>;
    pub fn sentence_by_upstream_id(&self, id: &str) -> Option<&SentenceRecord>;
    pub fn open_record_index(&self, speaker: &SpeakerKey) -> Option<usize>;
    /// One-shot consumption: settles to Hidden only when the current live row
    /// is Settled and matches the given speaker and sentence. Returns whether it
    /// dismissed anything.
    pub fn dismiss_settled_live(&mut self, speaker: &SpeakerKey, sentence_id: Option<&str>) -> bool;
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
impl Default for HudFrame { fn default() -> Self { Self { slots: std::array::from_fn(|_| None) } } }
```

Reducer rules (exact):
- `SourceLive` non-empty: find the speaker's open record (most recent with `source.phase == Draft`);
  if none, append one with a fresh monotonic ordinal. Set `source = { text, Draft, language }`,
  refresh recency, set `live_input = Streaming(snapshot)`.
- `SourceLive` empty: no-op.
- `SourceCommitted`: resolve by `sentence_id` (append if unknown), else the speaker's open record,
  else append. Set `source = { text, Committed, language }`, refresh recency, and settle
  `live_input = Settled { snapshot with the committed full text, closed_at: now }` when this
  record owns the live row.
- `SourceEnd { speaker, sentence_id }`:
  - if a record resolves and its `speaker == event.speaker` and `sentence_id` does not conflict,
    set `source.phase = Committed` (keep text) and settle using that source text;
  - else if `live_input` is `Streaming(snap)` with `snap.speaker == event.speaker` and
    (`event.sentence_id` is `None` or equals `snap.sentence_id`), settle using `snap.text`;
  - else no-op.
- Target events: resolve by `sentence_id` if present (append when unknown), else the speaker's
  open record, else drop. Set `target = { text, phase, language }`.
- `RefinedTarget`: resolve strictly by `sentence_id`; if absent, queue in a bounded (64) pending
  refinement list and replay when a matching sentence appears. Never fabricate a speaker.
- Never drop an older sentence's target; each target updates its own record.
- Records evict oldest-first beyond 64.
- `clear(false)`: reset sentences, `live_input = Hidden`, recency, ordinals.
- `clear(true)`: per record, keep `source` only if its phase is `Committed`/`Refined`, else reset
  to `Empty`; keep `target` only if its phase is `Committed`/`Refined`, else reset to `Empty`.
  Drop records with both tracks `Empty`. `live_input = Hidden`. Keep retained upstream IDs.

- [ ] **Step 1: Write failing reducer tests** (`src/transcript.rs`)

```rust
use super::*;
use std::time::Instant;

fn s_live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: None, text: text.into(), language: Some("en".into()) })
}
fn s_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()), text: text.into(), language: Some("en".into()) })
}
fn s_end(speaker: &str, id: Option<&str>) -> CaptionEvent {
    CaptionEvent::SourceEnd { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: id.map(str::to_owned) }
}
fn t_draft(speaker: &str, id: Option<&str>, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: id.map(str::to_owned), text: text.into(), language: Some("fr".into()) })
}
fn t_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate { speaker: SpeakerKey::Diarized(speaker.into()), sentence_id: Some(id.into()), text: text.into(), language: Some("fr".into()) })
}
fn refine(id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::RefinedTarget(Refinement { sentence_id: id.into(), text: text.into(), language: Some("fr".into()) })
}

#[test]
fn source_live_creates_open_record_and_streams() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hel"), Instant::now());
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].source.phase, TrackPhase::Draft);
    assert!(matches!(&s.live_input, LiveInputRow::Streaming(snap) if snap.text == "hel"));
}

#[test]
fn source_commit_refreshes_recency_and_settles_full_text() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&s_live("1", "hello wor"), t0);
    s.apply(&s_live("2", "other"), t0);
    s.apply(&s_commit("1", "A", "hello world"), t0);
    assert_eq!(s.speaker_recency().back(), Some(&SpeakerKey::Diarized("1".into())));
    match &s.live_input {
        LiveInputRow::Settled { snapshot, .. } => assert_eq!(snapshot.text, "hello world"),
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
fn source_end_settles_only_the_matching_speaker() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hello"), Instant::now());
    s.apply(&s_end("2", None), Instant::now());
    assert!(matches!(&s.live_input, LiveInputRow::Streaming(_)), "wrong speaker must not settle");
    s.apply(&s_end("1", None), Instant::now());
    assert!(matches!(&s.live_input, LiveInputRow::Settled { snapshot, .. } if snapshot.text == "hello"));
}

#[test]
fn source_end_with_conflicting_id_is_a_noop() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.apply(&s_end("1", Some("B")), Instant::now());
    assert!(matches!(&s.live_input, LiveInputRow::Hidden));
}

#[test]
fn target_without_id_binds_to_open_sentence() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "bonjour"), Instant::now());
    s.apply(&t_draft("1", None, "hello"), Instant::now());
    assert_eq!(s.sentences[0].target.text, "hello");
}

#[test]
fn target_without_id_is_dropped_when_no_open_sentence() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "done"), Instant::now());
    s.apply(&t_draft("1", None, "orphan"), Instant::now());
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Empty);
}

#[test]
fn late_target_for_older_sentence_is_kept_in_the_reducer() {
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.apply(&s_commit("1", "B", "b"), Instant::now());
    s.apply(&t_commit("1", "B", "bee"), Instant::now());
    s.apply(&t_commit("1", "A", "ay"), Instant::now());
    assert_eq!(s.sentence_by_upstream_id("A").unwrap().target.text, "ay");
    assert_eq!(s.sentence_by_upstream_id("B").unwrap().target.text, "bee");
}

#[test]
fn refinement_resolves_by_id_and_replays_when_late() {
    let mut s = TranscriptState::default();
    s.apply(&refine("A", "early"), Instant::now());
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    assert_eq!(s.sentence_by_upstream_id("A").unwrap().target.phase, TrackPhase::Refined);
    assert_eq!(s.sentence_by_upstream_id("A").unwrap().target.text, "early");
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
    for i in 0..80u64 { s.apply(&s_commit("1", &format!("{i}"), "x"), Instant::now()); }
    assert_eq!(s.sentences.len(), 64);
}

#[test]
fn anonymous_provider_uses_one_speaker_key() {
    let mut s = TranscriptState::default();
    for i in 0..3u64 {
        s.apply(&CaptionEvent::SourceCommitted(CommittedSource { speaker: SpeakerKey::Anonymous, sentence_id: Some(format!("{i}")), text: "x".into(), language: None }), Instant::now());
    }
    assert!(s.sentences.iter().all(|r| r.speaker == SpeakerKey::Anonymous));
}

#[test]
fn clear_true_is_per_track() {
    // committed source + draft target -> source kept, target reset
    let mut s = TranscriptState::default();
    s.apply(&s_commit("1", "A", "a"), Instant::now());
    s.apply(&t_draft("1", Some("A"), "draft"), Instant::now());
    s.clear(true);
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].source.phase, TrackPhase::Committed);
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Empty);

    // draft source + committed target -> target kept, source reset
    let mut s = TranscriptState::default();
    s.apply(&s_live("2", "draft src"), Instant::now());
    s.apply(&t_commit("2", "B", "final tgt"), Instant::now());
    s.clear(true);
    assert_eq!(s.sentences.len(), 1);
    assert_eq!(s.sentences[0].source.phase, TrackPhase::Empty);
    assert_eq!(s.sentences[0].target.phase, TrackPhase::Committed);

    // neither settled -> dropped
    let mut s = TranscriptState::default();
    s.apply(&s_live("3", "only draft"), Instant::now());
    s.clear(true);
    assert!(s.sentences.is_empty());
}

#[test]
fn dismiss_settled_live_matches_and_consumes_once() {
    let mut s = TranscriptState::default();
    s.apply(&s_live("1", "hello wor"), Instant::now());
    s.apply(&s_commit("1", "A", "hello world"), Instant::now());
    assert!(!s.dismiss_settled_live(&SpeakerKey::Diarized("2".into()), None));
    assert!(s.dismiss_settled_live(&SpeakerKey::Diarized("1".into()), Some("A")));
    assert!(matches!(s.live_input, LiveInputRow::Hidden));
    assert!(!s.dismiss_settled_live(&SpeakerKey::Diarized("1".into()), Some("A")));
}

#[test]
fn hud_frame_default_has_five_empty_slots() {
    assert!(HudFrame::default().slots.iter().all(Option::is_none));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --lib transcript -- --nocapture`
`cargo test --manifest-path vr_overlay/Cargo.toml --lib hud -- --nocapture`

- [ ] **Step 3: Implement `transcript.rs` and `hud.rs`**

- [ ] **Step 4: Run and verify GREEN**

Both Step 2 commands pass.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/transcript.rs vr_overlay/src/hud.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add domain types, shared HUD frame, and transcript reducer"
```

---

### Task 3: Desktop protocol adapter

**Files:**
- Create: `vr_overlay/src/protocol.rs`
- Create: `vr_overlay/tests/protocol.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/tests/protocol.rs` (`mod support;`)

**Interfaces:**

```rust
use crate::transcript::CaptionEvent;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum TrackKind { Source, Translation }

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct AccumKey { sentence_id: Option<String>, speaker: SpeakerKey, track: TrackKind }

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalTokenFingerprint {
    is_separator: bool,
    track: TrackKind,
    speaker: SpeakerKey,
    sentence_id: Option<String>,
    text: String,
    language: Option<String>,
}

pub struct DesktopProtocol {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    pending: VecDeque<CaptionEvent>,
    accumulators: BTreeMap<AccumKey, String>,
    replace_on_next: [bool; 2],
    previous_final_fingerprint: Option<Vec<FinalTokenFingerprint>>,
}

impl DesktopProtocol {
    pub async fn connect(manifest: &OverlayManifest) -> Result<Self, BridgeError>;
    pub async fn next_event(&mut self) -> Result<CaptionEvent, BridgeError>;
    /// Await-free drain: pops a queued event, else polls the socket once.
    pub async fn try_next_event(&mut self) -> Option<Result<CaptionEvent, BridgeError>>;
}
```

`next_event` pops `pending`, else reads one frame, parses it into zero or more events appended to
`pending`, and returns the first. `try_next_event` pops `pending`, else wraps the read in
`futures_util::future::poll_immediate(...).await`; `None` means the socket would block.

- [ ] **Step 1: Write failing adapter tests** (`tests/protocol.rs`)

```rust
mod support;
use rinbridge_overlay::protocol::{BridgeError, DesktopProtocol};
use rinbridge_overlay::transcript::{CaptionEvent, SpeakerKey};
use rinbridge_overlay::views::DisplayMode;
use support::{spawn_scripted_server, test_manifest_with_url};

#[tokio::test]
async fn one_frame_can_yield_multiple_events_through_the_pending_queue() {
    // A single `update` frame carries a source commit, a target commit, and a
    // live snapshot. The adapter must queue the extra events instead of
    // discarding them.
    let frames = vec![r#"{"type":"update","final_tokens":[
        {"text":"hi","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
        {"text":"salut","speaker":"1","translation_status":"translation","llm_sentence_id":"A","is_final":true}],
        "non_final_tokens":[{"text":"live","speaker":"2","translation_status":"original","is_final":false}]}"#.to_string()];
    let (url, server) = spawn_scripted_server(frames);
    let manifest = test_manifest_with_url(&url);
    let mut p = DesktopProtocol::connect(&manifest).await.unwrap();
    let first = p.next_event().await.unwrap(); // reads the frame, queues the rest
    let mut seen = vec![first];
    for _ in 0..2 {
        seen.push(p.try_next_event().await.expect("queued event").unwrap());
    }
    assert!(seen.iter().any(|e| matches!(e, CaptionEvent::SourceCommitted(_))));
    assert!(seen.iter().any(|e| matches!(e, CaptionEvent::TargetCommitted(_))));
    assert!(seen.iter().any(|e| matches!(e, CaptionEvent::SourceLive(_))));
    let extra = p.try_next_event().await;
    assert!(extra.is_none() || matches!(extra, Some(Err(_))), "no fourth caption event");
    server.await.unwrap();
}

#[tokio::test]
async fn final_source_true_delta_appends_and_replay_dedupes() {
    let frames = vec![
        ts_delta("very", "A"),
        ts_delta("very", "A"),
        format!("{{\"type\":\"update\",\"final_tokens\":[{{\"text\":\"very\",\"speaker\":\"1\",\"translation_status\":\"original\",\"llm_sentence_id\":\"A\",\"is_final\":true}},{{\"text\":\"very\",\"speaker\":\"1\",\"translation_status\":\"original\",\"llm_sentence_id\":\"A\",\"is_final\":true}}],\"non_final_tokens\":[]}}"),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert_eq!(committed_texts(&events), vec!["very".to_string(), "veryvery".to_string()]);
}

fn ts_delta(text: &str, id: &str) -> String {
    format!("{{\"type\":\"update\",\"final_tokens\":[{{\"text\":\"{text}\",\"speaker\":\"1\",\"translation_status\":\"original\",\"llm_sentence_id\":\"{id}\",\"is_final\":true}}],\"non_final_tokens\":[]}}")
}

async fn adapter_events(frames: Vec<String>) -> Result<Vec<CaptionEvent>, BridgeError> {
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

fn committed_texts(events: &[CaptionEvent]) -> Vec<String> {
    events.iter().filter_map(|e| match e {
        CaptionEvent::SourceCommitted(c) => Some(c.text.clone()),
        _ => None,
    }).collect()
}

#[tokio::test]
async fn final_tokens_segment_by_sentence_speaker_and_language() {
    let frames = vec![r#"{"type":"update","final_tokens":[
        {"text":"hello ","speaker":"1","translation_status":"original","llm_sentence_id":"A","language":"en","is_final":true},
        {"text":"bonjour ","speaker":"2","translation_status":"original","llm_sentence_id":"B","language":"en","is_final":true}],
        "non_final_tokens":[]}"#.to_string()];
    let events = adapter_events(frames).await.unwrap();
    let mut by_id = std::collections::BTreeMap::new();
    for e in &events {
        if let CaptionEvent::SourceCommitted(c) = e { by_id.insert(c.sentence_id.clone().unwrap(), c.text.clone()); }
    }
    assert_eq!(by_id.get("A").map(String::as_str), Some("hello "));
    assert_eq!(by_id.get("B").map(String::as_str), Some("bonjour "));
}

#[tokio::test]
async fn multi_speaker_non_final_emits_one_live_event_for_the_last_speaker() {
    let frames = vec![r#"{"type":"update","final_tokens":[],"non_final_tokens":[
        {"text":"from one","speaker":"1","translation_status":"original","is_final":false},
        {"text":"from two","speaker":"2","translation_status":"original","is_final":false}]}"#.to_string()];
    let events = adapter_events(frames).await.unwrap();
    let live: Vec<_> = events.iter().filter_map(|e| match e { CaptionEvent::SourceLive(s) => Some(s.clone()), _ => None }).collect();
    assert_eq!(live.len(), 1);
    assert_eq!(live[0].speaker, SpeakerKey::Diarized("2".into()));
    assert_eq!(live[0].text, "from two");
}

#[tokio::test]
async fn refinement_maps_to_a_sentence_keyed_event() {
    let frames = vec![r#"{"type":"refine_result","sentence_id":"A","source":"src","original_translation":"draft","refined_translation":"refined","no_change":false}"#.to_string()];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::RefinedTarget(r) if r.text == "refined" && r.sentence_id == "A")));
}

#[tokio::test]
async fn explicit_end_token_becomes_source_end() {
    let frames = vec![r#"{"type":"update","final_tokens":[{"text":"<end>","speaker":"1","translation_status":"original","is_final":true}],"non_final_tokens":[]}"#.to_string()];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::SourceEnd { speaker, sentence_id: None } if *speaker == SpeakerKey::Diarized("1".into()))));
}

#[tokio::test]
async fn malformed_frames_are_ignored_without_breaking_state() {
    let frames = vec![
        ts_delta("ok", "A"),
        r#"{"type":"update","final_tokens":"not-an-array"}"#.to_string(),
        "this is not json".to_string(),
        ts_delta(" more", "A"),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert_eq!(committed_texts(&events), vec!["ok".to_string(), "ok more".to_string()]);
}

#[tokio::test]
async fn view_settings_and_clear_map_to_events() {
    let frames = vec![
        r#"{"type":"clear","preserve_existing":true}"#.to_string(),
        r#"{"type":"vr_view_settings","display_mode":"original","max_speakers":2,"bilingual_pair_count":1,"show_speaker_labels":false}"#.to_string(),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|e| matches!(e, CaptionEvent::Clear { preserve_existing: true })));
    assert!(events.iter().any(|e| matches!(e,
        CaptionEvent::ViewSettingsChanged(s) if s.display_mode == DisplayMode::Original && s.max_speakers == 2)));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test protocol -- --nocapture`

- [ ] **Step 3: Implement the adapter**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test protocol` passes.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/protocol.rs vr_overlay/tests/protocol.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add typed /ws protocol adapter with pending event queue"
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LiveRowDirective {
    None,
    Show,
    Dismiss { speaker: SpeakerKey, sentence_id: Option<String> },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Projection { pub frame: HudFrame, pub live_row: LiveRowDirective }

pub fn project(state: &TranscriptState, settings: &VrViewSettings, now: Instant) -> Projection;
```

Projection rules:
- `original`: per speaker, the record with the highest `local_ordinal` whose `source.phase` is
  `Committed`/`Refined`; text = source text; `kind = Settled`. Speaker rows bottom-align in
  slots 1..=3, ordered by `state.speaker_recency()` oldest-first.
- `translation`: per speaker, the record with the highest `local_ordinal` with a non-empty
  target; `kind = Draft` when `target.phase == Draft` else `Settled`. Same slots.
- `both`: records with non-empty `source.text` and non-empty `target.text`, ordered by
  `local_ordinal`, capacity `bilingual_pair_count`. One pair -> target slot 2, source slot 3.
  Two pairs -> older pair slots 0,1; newer pair slots 2,3. Target `UpperPrimary`, source
  `UpperSecondary`, `speaker_label = None`.
- Live row and directive: `Hidden` -> no row, `None`. `Streaming(s)` -> show row at slot 4,
  `Show`. `Settled`: if `now < closed_at + LIVE_SOURCE_HOLD` -> show, `Show`; else if the
  mode-specific handoff is visible -> do not render the row and return `Dismiss { .. }`;
  else -> show, `Show`. Handoff: original = settled sentence has committed source; translation =
  settled sentence has non-empty target; both = settled sentence has non-empty source and target.
- `language` copied from the owning track. `speaker_label = Some(speaker)` only when
  `show_speaker_labels` and mode != `both`.

- [ ] **Step 1: Write failing projection tests** (`tests/projection.rs`)

```rust
mod support;
use rinbridge_overlay::hud::{HudRowKind, HudRowRole};
use rinbridge_overlay::projection::{project, LiveRowDirective, LIVE_SOURCE_HOLD};
use rinbridge_overlay::transcript::{CaptionEvent, CommittedSource, LiveSourceSnapshot, SpeakerKey, TargetUpdate, TranscriptState};
use rinbridge_overlay::views::{DisplayMode, VrViewSettings};
use std::time::{Duration, Instant};

fn settings(mode: DisplayMode, max: u8, pairs: u8) -> VrViewSettings {
    VrViewSettings { display_mode: mode, max_speakers: max, bilingual_pair_count: pairs, show_speaker_labels: true }
}
fn commit(sp: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource { speaker: SpeakerKey::Diarized(sp.into()), sentence_id: Some(id.into()), text: text.into(), language: Some("en".into()) })
}
fn live(sp: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot { speaker: SpeakerKey::Diarized(sp.into()), sentence_id: None, text: text.into(), language: Some("en".into()) })
}
fn t_draft(sp: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate { speaker: SpeakerKey::Diarized(sp.into()), sentence_id: Some(id.into()), text: text.into(), language: Some("fr".into()) })
}
fn t_commit(sp: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate { speaker: SpeakerKey::Diarized(sp.into()), sentence_id: Some(id.into()), text: text.into(), language: Some("fr".into()) })
}
fn text_at(f: &rinbridge_overlay::hud::HudFrame, i: usize) -> Option<&str> { f.slots[i].as_ref().map(|r| r.text.as_str()) }

#[test]
fn original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for (sp, txt) in [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2")] { s.apply(&commit(sp, &format!("{sp}-{txt}"), txt), t); }
    let p = project(&s, &settings(DisplayMode::Original, 3, 1), t);
    assert_eq!(p.frame.slots[0], None);
    assert_eq!(text_at(&p.frame, 1), Some("b1"));
    assert_eq!(text_at(&p.frame, 2), Some("c1"));
    assert_eq!(text_at(&p.frame, 3), Some("a2"));
}

#[test]
fn original_capacity_one_two_three_evicts_least_recent() {
    for (cap, expected) in [
        (1u8, vec![None, None, Some("c")]),
        (2u8, vec![None, Some("b"), Some("c")]),
        (3u8, vec![Some("a"), Some("b"), Some("c")]),
    ] {
        let mut s = TranscriptState::default();
        let t = Instant::now();
        for (sp, txt) in [("A", "a"), ("B", "b"), ("C", "c")] { s.apply(&commit(sp, txt, txt), t); }
        let p = project(&s, &settings(DisplayMode::Original, cap, 1), t);
        assert_eq!(vec![text_at(&p.frame, 1).map(str::to_string), text_at(&p.frame, 2).map(str::to_string), text_at(&p.frame, 3).map(str::to_string)], expected, "cap {cap}");
    }
}

#[test]
fn translation_capacity_evicts_least_recent_speaker() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for sp in ["A", "B", "C"] {
        s.apply(&commit(sp, sp, "src"), t);
        s.apply(&t_commit(sp, sp, &format!("t{sp}")), t);
    }
    let p = project(&s, &settings(DisplayMode::Translation, 1, 1), t);
    assert_eq!(text_at(&p.frame, 3), Some("tC"));
    assert_eq!(p.frame.slots[1], None);
    assert_eq!(p.frame.slots[2], None);
}

#[test]
fn translation_shows_only_the_newest_target_per_speaker() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    s.apply(&commit("1", "A", "a"), t);
    s.apply(&commit("1", "B", "b"), t);
    s.apply(&t_commit("1", "A", "ay"), t);
    s.apply(&t_commit("1", "B", "bee"), t);
    assert_eq!(text_at(&project(&s, &settings(DisplayMode::Translation, 3, 1), t).frame, 3), Some("bee"));
    s.apply(&t_commit("1", "A", "ay2"), t);
    assert_eq!(text_at(&project(&s, &settings(DisplayMode::Translation, 3, 1), t).frame, 3), Some("bee"));
}

#[test]
fn translation_draft_upgrades_in_place() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    s.apply(&commit("1", "A", "a"), t);
    s.apply(&t_draft("1", "A", "bon"), t);
    let draft = project(&s, &settings(DisplayMode::Translation, 3, 1), t);
    assert_eq!(draft.frame.slots[3].as_ref().unwrap().kind, HudRowKind::Draft);
    s.apply(&t_commit("1", "A", "bonjour"), t);
    let final_ = project(&s, &settings(DisplayMode::Translation, 3, 1), t);
    assert_eq!(text_at(&final_.frame, 3), Some("bonjour"));
    assert_eq!(final_.frame.slots[3].as_ref().unwrap().kind, HudRowKind::Settled);
}

#[test]
fn both_mode_one_pair_occupies_slots_two_and_three() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    s.apply(&commit("1", "A", "hello"), t);
    s.apply(&t_draft("1", "A", "bonjour"), t);
    let p = project(&s, &settings(DisplayMode::Both, 3, 1), t);
    assert_eq!(p.frame.slots[0], None);
    assert_eq!(p.frame.slots[1], None);
    assert_eq!(p.frame.slots[2].as_ref().unwrap().role, HudRowRole::UpperPrimary);
    assert_eq!(text_at(&p.frame, 2), Some("bonjour"));
    assert_eq!(p.frame.slots[3].as_ref().unwrap().role, HudRowRole::UpperSecondary);
    assert_eq!(text_at(&p.frame, 3), Some("hello"));
    assert!(p.frame.slots.iter().flatten().all(|r| r.speaker_label.is_none()));
}

#[test]
fn both_mode_two_pairs_occupy_slots_zero_through_three() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for id in ["A", "B"] { s.apply(&commit("1", id, id), t); s.apply(&t_draft("1", id, &format!("t{id}")), t); }
    let p = project(&s, &settings(DisplayMode::Both, 3, 2), t);
    assert_eq!(text_at(&p.frame, 0), Some("tA"));
    assert_eq!(text_at(&p.frame, 1), Some("A"));
    assert_eq!(text_at(&p.frame, 2), Some("tB"));
    assert_eq!(text_at(&p.frame, 3), Some("B"));
}

#[test]
fn both_mode_evicts_the_oldest_pair_beyond_capacity() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    for id in ["A", "B", "C"] { s.apply(&commit("1", id, id), t); s.apply(&t_draft("1", id, &format!("t{id}")), t); }
    let p = project(&s, &settings(DisplayMode::Both, 3, 2), t);
    assert_eq!(text_at(&p.frame, 0), Some("tB"));
    assert_eq!(text_at(&p.frame, 1), Some("B"));
    assert_eq!(text_at(&p.frame, 2), Some("tC"));
    assert_eq!(text_at(&p.frame, 3), Some("C"));
}

#[test]
fn both_mode_never_crosses_sentence_ids() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    s.apply(&commit("1", "A", "a"), t);
    s.apply(&commit("1", "B", "b"), t);
    s.apply(&t_commit("1", "B", "tb"), t);
    let p = project(&s, &settings(DisplayMode::Both, 3, 2), t);
    // Only the fully materialized B pair is visible; tB never pairs with A.
    assert_eq!(p.frame.slots[0], None);
    assert_eq!(p.frame.slots[1], None);
    assert_eq!(text_at(&p.frame, 2), Some("tb"));
    assert_eq!(text_at(&p.frame, 3), Some("b"));
}

#[test]
fn live_row_always_occupies_slot_four() {
    let mut s = TranscriptState::default();
    s.apply(&live("1", "now speaking"), Instant::now());
    for mode in [DisplayMode::Original, DisplayMode::Translation, DisplayMode::Both] {
        let p = project(&s, &settings(mode, 3, 1), Instant::now());
        let row = p.frame.slots[4].as_ref().expect("live row present");
        assert_eq!(row.role, HudRowRole::LiveSource);
        assert_eq!(row.text, "now speaking");
        assert!(p.frame.slots[0..4].iter().all(Option::is_none));
    }
}

#[test]
fn settled_row_reports_dismiss_after_hold_and_handoff() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&live("1", "hello wor"), t0);
    s.apply(&commit("1", "A", "hello world"), t0);
    s.apply(&t_draft("1", "A", "bonjour"), t0 + Duration::from_millis(500));
    let early = project(&s, &settings(DisplayMode::Translation, 3, 1), t0 + Duration::from_millis(600));
    assert_eq!(early.live_row, LiveRowDirective::Show);
    assert_eq!(text_at(&early.frame, 4), Some("hello world"));
    let late = project(&s, &settings(DisplayMode::Translation, 3, 1), t0 + LIVE_SOURCE_HOLD + Duration::from_millis(1));
    assert!(matches!(late.live_row, LiveRowDirective::Dismiss { .. }));
    assert_eq!(late.frame.slots[4], None);
}

#[test]
fn no_handoff_keeps_settled_row_visible() {
    let mut s = TranscriptState::default();
    let t0 = Instant::now();
    s.apply(&live("1", "hello wor"), t0);
    s.apply(&commit("1", "A", "hello world"), t0);
    let p = project(&s, &settings(DisplayMode::Translation, 3, 1), t0 + Duration::from_secs(10));
    assert_eq!(p.live_row, LiveRowDirective::Show);
    assert_eq!(text_at(&p.frame, 4), Some("hello world"));
}

#[test]
fn language_propagates_and_labels_stay_separate() {
    let mut s = TranscriptState::default();
    let t = Instant::now();
    s.apply(&commit("1", "A", "hello"), t);
    s.apply(&t_commit("1", "A", "bonjour"), t);
    let both = project(&s, &settings(DisplayMode::Both, 3, 1), t);
    assert_eq!(both.frame.slots[2].as_ref().unwrap().language.as_deref(), Some("fr"));
    assert_eq!(both.frame.slots[3].as_ref().unwrap().language.as_deref(), Some("en"));
    let original = project(&s, &settings(DisplayMode::Original, 3, 1), t);
    let row = original.frame.slots[3].as_ref().unwrap();
    assert_eq!(row.text, "hello");
    assert_eq!(row.speaker_label.as_deref(), Some("1"));
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test projection -- --nocapture`

- [ ] **Step 3: Implement the three projections, the live-row builder, and the directive.**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test projection` passes.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/projection.rs vr_overlay/tests/projection.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): add fixed-slot HUD projections with live-row directives"
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
- `CaptionRenderer::render_hud_frame(&self, frame: &HudFrame, presentation: &CaptionPresentation) -> Result<RenderedFrame, CaptionRenderError>`
  consuming `crate::hud::HudFrame` directly. Block id convention: `slot-<index>`.
- Slot top: `HUD_FIRST_SLOT_TOP_PX + index as f32 * HUD_SLOT_STRIDE_PX`; left `HUD_TEXT_LEFT_PX`;
  width `HUD_CONTENT_WIDTH_PX`.
- Role scale: `UpperPrimary` primary; `UpperSecondary`/`LiveSource` secondary; times
  `presentation.text_scale`.
- The renderer alone composes `speaker_label` into the measured line.
- One physical line per slot; trailing ellipsis except `LiveSource` (leading ellipsis).
- `LineCacheKey` and `LayoutCacheKey` gain `speaker_label: Option<String>` and **must not** gain
  `HudRowKind`. `kind` is passed to the draw pass for fill color only.
- Export `fit_row_text(text: &str, max_advance: f32, direction: Truncation, advance: &dyn Fn(char) -> f32) -> String`
  and `pub enum Truncation { Trailing, Leading }`.

- [ ] **Step 1: Write failing renderer tests**

```rust
use rinbridge_overlay::hud::{HudFrame, HudRow, HudRowKind, HudRowRole};
use rinbridge_overlay::renderer::Truncation;
use rinbridge_overlay::{fit_row_text, CaptionPresentation, CaptionRenderer, HUD_TEXT_LEFT_PX};

fn row(role: HudRowRole, kind: HudRowKind, text: &str) -> HudRow {
    HudRow { role, kind, text: text.into(), speaker_label: None, language: None, sentence: None }
}
fn frame_with(entries: &[(usize, HudRow)]) -> HudFrame {
    let mut f = HudFrame::default();
    for (i, r) in entries { f.slots[*i] = Some(r.clone()); }
    f
}
fn line_of(frame: &rinbridge_overlay::RenderedFrame, index: usize) -> &str {
    &frame.layout().visible_blocks.iter().find(|b| b.id == format!("slot-{index}")).unwrap().primary_lines[0].text
}

#[test]
fn hud_rows_use_fixed_left_origins_and_do_not_move_when_text_grows() {
    let renderer = CaptionRenderer::new().unwrap();
    let short = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hi"))]), &CaptionPresentation::default()).unwrap();
    let long = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hi there everyone"))]), &CaptionPresentation::default()).unwrap();
    let a = short.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    let b = long.layout().visible_blocks.iter().find(|b| b.id == "slot-3").unwrap();
    assert!((a.bounds.left_px - b.bounds.left_px).abs() < 0.01);
    assert!((a.bounds.left_px - HUD_TEXT_LEFT_PX).abs() < 0.01);
    assert!((a.bounds.top_px - b.bounds.top_px).abs() < 0.01);
}

#[test]
fn live_row_keeps_newest_tail_with_leading_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "α".repeat(400) + "NEWESTTAIL";
    let frame = renderer.render_hud_frame(&frame_with(&[(4, row(HudRowRole::LiveSource, HudRowKind::Settled, &text))]), &CaptionPresentation::default()).unwrap();
    let line = line_of(&frame, 4);
    assert!(line.starts_with('…'));
    assert!(line.ends_with("NEWESTTAIL"));
}

#[test]
fn single_language_row_overflow_uses_trailing_ellipsis() {
    let renderer = CaptionRenderer::new().unwrap();
    let text = "BEGINNING".to_string() + &"x".repeat(400);
    let frame = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, &text))]), &CaptionPresentation::default()).unwrap();
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
    let mut r = row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello");
    r.speaker_label = Some("S1".into());
    let frame = renderer.render_hud_frame(&frame_with(&[(3, r)]), &CaptionPresentation::default()).unwrap();
    let line = line_of(&frame, 3);
    assert!(line.starts_with("S1"));
    assert!(line.contains("hello"));
}

#[test]
fn speaker_label_is_part_of_the_layout_cache_key() {
    let renderer = CaptionRenderer::new().unwrap();
    let mut a = row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello");
    a.speaker_label = Some("S1".into());
    let mut b = row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello");
    b.speaker_label = Some("S2".into());
    let fa = renderer.render_hud_frame(&frame_with(&[(3, a)]), &CaptionPresentation::default()).unwrap();
    let fb = renderer.render_hud_frame(&frame_with(&[(3, b)]), &CaptionPresentation::default()).unwrap();
    assert_ne!(fa.layout().visible_blocks[0].block_cache_key(), fb.layout().visible_blocks[0].block_cache_key());
}

#[test]
fn draft_and_final_share_the_geometry_cache_key() {
    let renderer = CaptionRenderer::new().unwrap();
    let fa = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Draft, "bon"))]), &CaptionPresentation::default()).unwrap();
    let fb = renderer.render_hud_frame(&frame_with(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "bon"))]), &CaptionPresentation::default()).unwrap();
    assert_eq!(fa.layout().visible_blocks[0].block_cache_key(), fb.layout().visible_blocks[0].block_cache_key());
    let ba = &fa.layout().visible_blocks[0].bounds;
    let bb = &fb.layout().visible_blocks[0].bounds;
    assert!((ba.left_px - bb.left_px).abs() < 0.01 && (ba.top_px - bb.top_px).abs() < 0.01);
}

#[test]
fn empty_slots_are_transparent_and_five_slots_are_reserved() {
    let renderer = CaptionRenderer::new().unwrap();
    let frame = renderer.render_hud_frame(&frame_with(&[(4, row(HudRowRole::LiveSource, HudRowKind::Settled, "x"))]), &CaptionPresentation::default()).unwrap();
    assert_eq!(frame.layout().visible_blocks.len(), 1);
    assert_eq!(frame.layout().visible_blocks[0].id, "slot-4");
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test renderer hud -- --nocapture`

- [ ] **Step 3: Implement the slot layout path and `fit_row_text`; export the new names.**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test renderer hud` passes.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/renderer vr_overlay/src/lib.rs vr_overlay/tests/renderer.rs
git commit -m "feat(vr): render fixed five-slot HUD frames"
```

---

### Task 6: OpenVR compositor alpha (mandatory implementation)

**Files:**
- Modify: `vr_overlay/src/openvr.rs`
- Modify: `vr_overlay/tests/support/mod.rs` (finalize `RecordingSubmitter`)
- Test: `vr_overlay/tests/runtime.rs`

**Interfaces:**
- Add to `OverlayFrameSubmitter` with **no default body**:

```rust
fn set_overlay_alpha(&mut self, alpha: f32) -> Result<(), OpenVrError>;
```

- Implement in every implementor, found by
  `rg "impl (crate::openvr::)?OverlayFrameSubmitter" vr_overlay/src vr_overlay/tests`:
  `OpenVrOverlay` (delegates to backend), `FakeOpenVr`, `ShellSubmitter`, `RecordingSubmitter`.
  Clamp to `0.0..=1.0`. `FakeOpenVr` gains `last_alpha: Cell<Option<f32>>` and
  `pub fn last_alpha(&self) -> Option<f32>`.

- [ ] **Step 1: Write the failing test** (`tests/runtime.rs`)

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

`cargo test --manifest-path vr_overlay/Cargo.toml --test runtime overlay_alpha_is_clamped`

Expected: compile error — the trait method does not exist.

- [ ] **Step 3: Implement the trait method and every implementor; finalize `RecordingSubmitter`.**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test runtime overlay_alpha_is_clamped` passes.

- [ ] **Step 5: Commit**

```powershell
git add vr_overlay/src/openvr.rs vr_overlay/tests/support vr_overlay/tests/runtime.rs
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
pub trait ParentLiveness { fn alive(&self) -> bool; }
pub struct ProcessLiveness { pub parent_pid: u32 }

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct VisibilityTick { pub set_visible: Option<bool>, pub alpha: Option<f32> }

pub struct VisibilityController { visible: bool, last_activity: Instant, fade_started: Option<Instant>, last_alpha: f32 }
impl VisibilityController {
    pub fn new(now: Instant) -> Self;
    pub fn on_activity(&mut self, now: Instant);
    pub fn tick(&mut self, now: Instant) -> VisibilityTick;
    pub fn next_deadline(&self) -> Option<Instant>;
    pub fn is_visible(&self) -> bool;
}

pub const SILENCE_BEFORE_FADE: Duration = Duration::from_millis(4000);
pub const FADE_DURATION: Duration = Duration::from_millis(1200);
pub const FADE_STEP: Duration = Duration::from_millis(50);
pub const PARENT_POLL_INTERVAL: Duration = Duration::from_millis(2000);
```

Event-loop rules:
- Immediately submit `HudFrame::default()` (transparent); on success emit `overlay_ready`.
- After an event, drain `protocol.try_next_event().await` until `None`, apply all events, then
  project **with consumption**: project once; if `live_row == Dismiss`, call
  `transcript.dismiss_settled_live(...)` and project again; submit only if the final frame
  differs from the last submitted frame.
- `next_deadline(now)` = minimum of:
  - live hold: `Some(closed_at + LIVE_SOURCE_HOLD)` only while `live_input` is `Settled` **and**
    `now < closed_at + LIVE_SOURCE_HOLD` (once elapsed, `None` — the handoff is detected on the
    next event, so the deadline is never re-armed and cannot busy-loop);
  - silence start: `last_activity + SILENCE_BEFORE_FADE` while visible, not fading, and not yet
    elapsed;
  - fade step: next `FADE_STEP` boundary while fading;
  - parent poll: next `PARENT_POLL_INTERVAL` boundary.
- `tokio::select!` between `protocol.next_event()` and `sleep_until(next_deadline)`; on a
  deadline wake call `tick(now)`.
- `tick` applies `VisibilityTick` as `set_visible` then `alpha` in the same turn.
- `SourceLive` with text or any new visible target/refinement calls `on_activity`.
  `Activity`/heartbeats never do.
- `ViewSettingsChanged` validates; invalid is ignored.
- Startup retries connect until `startup_deadline_ms` while `parent.alive()`.
- Disconnect after readiness: clear live input, hide, reconnect with `250ms` doubling to `5s`;
  reconnect resets transcript state.
- Parent poll at `PARENT_POLL_INTERVAL`; dead parent exits cleanly.

- [ ] **Step 1: Write failing coordinator tests** (`tests/coordinator.rs`)

```rust
mod support;
use rinbridge_overlay::runtime::{VisibilityController, SILENCE_BEFORE_FADE, FADE_DURATION};
use rinbridge_overlay::transcript::LiveInputRow;
use rinbridge_overlay::views::{DisplayMode, VrViewSettings};
use support::{src_commit, src_live, target_draft};
use std::time::{Duration, Instant};

#[test]
fn visibility_wakes_with_show_and_alpha_in_one_tick() {
    let t = Instant::now();
    let mut v = VisibilityController::new(t);
    v.on_activity(t);
    let _ = v.tick(t);
    let hide = v.tick(t + SILENCE_BEFORE_FADE + FADE_DURATION);
    assert_eq!(hide.alpha, Some(0.0));
    assert_eq!(hide.set_visible, Some(false));
    assert!(!v.is_visible());
    v.on_activity(t + SILENCE_BEFORE_FADE + Duration::from_millis(1300));
    let wake = v.tick(t + SILENCE_BEFORE_FADE + Duration::from_millis(1300));
    assert_eq!(wake.set_visible, Some(true), "wake must Show, not only set alpha");
    assert_eq!(wake.alpha, Some(1.0));
    assert!(v.is_visible());
}

#[test]
fn live_hold_deadline_is_armed_once_and_never_busy_loops() {
    let mut c = RuntimeCoordinator::for_test();
    let t0 = c.now();
    c.push_for_test(src_live("1", "hello wor"), t0);
    c.push_for_test(src_commit("1", "A", "hello world"), t0);
    c.push_for_test(target_draft("1", "A", "bonjour"), t0 + Duration::from_millis(500));
    assert_eq!(c.next_wake_for_test(t0 + Duration::from_millis(600)), Some(t0 + Duration::from_millis(1200)));
    c.tick_for_test(t0 + Duration::from_millis(1201));
    assert!(matches!(c.transcript_for_test().live_input, LiveInputRow::Hidden));
    assert_eq!(c.next_wake_for_test(t0 + Duration::from_millis(1201)), None, "consumed hold must not re-arm");
}

#[test]
fn dismissed_live_row_does_not_resurrect_on_mode_switch() {
    let mut c = RuntimeCoordinator::for_test();
    let t0 = c.now();
    c.push_for_test(src_live("1", "hello wor"), t0);
    c.push_for_test(src_commit("1", "A", "hello world"), t0);
    c.push_for_test(target_draft("1", "A", "bonjour"), t0 + Duration::from_millis(500));
    c.tick_for_test(t0 + Duration::from_millis(1201));
    c.apply_settings_for_test(VrViewSettings { display_mode: DisplayMode::Original, ..Default::default() });
    c.render_for_test(t0 + Duration::from_millis(1300));
    assert!(c.last_frame_for_test().slots[4].is_none());
}

#[test]
fn hold_expiry_without_handoff_keeps_row_and_clears_when_target_arrives() {
    let mut c = RuntimeCoordinator::for_test();
    let t0 = c.now();
    c.push_for_test(src_live("1", "hello wor"), t0);
    c.push_for_test(src_commit("1", "A", "hello world"), t0);
    c.tick_for_test(t0 + Duration::from_millis(1201));
    assert!(c.last_frame_for_test().slots[4].is_some());
    c.push_for_test(target_draft("1", "A", "bonjour"), t0 + Duration::from_millis(1500));
    c.drain_and_render_for_test(t0 + Duration::from_millis(1500));
    assert!(c.last_frame_for_test().slots[4].is_none());
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
    c.start_for_test(c.now());
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
    c.push_for_test(target_draft("1", "A", "bonjour"), t);
    c.on_disconnect_for_test();
    assert!(c.transcript_for_test().sentences.is_empty());
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator -- --nocapture`

- [ ] **Step 3: Implement the coordinator, clocks, fakes, and deadline arithmetic.**

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator` passes.

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
- Trim: `vr_overlay/src/state.rs` to `OverlayCalibration` + its non-zero `Default` only
- Modify: `vr_overlay/src/openvr.rs` (`OverlayPresentationCalibration::default()` ->
  `OverlayCalibration::default()`)
- Modify: `vr_overlay/src/lib.rs` (final export list)
- Modify: `vr_overlay/tests/renderer.rs`, `vr_overlay/tests/runtime.rs` (drop snapshot imports)
- Modify: `vr_overlay/scripts/verify.ps1` (delete the vendored-DLL precheck block)
- Test: full suite

**Post-removal public surface (`lib.rs`):**

```rust
pub mod hud;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod projection;
pub mod protocol;
pub mod renderer;
pub mod runtime;
pub mod state;
pub mod transcript;
pub mod views;

pub use hud::{HudFrame, HudRow, HudRowKind, HudRowRole, HUD_SLOT_COUNT};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{FakeOpenVr, OpenVrError, OpenVrOverlay, OverlayFrameSubmitter, OverlayPlacementPolicy};
pub use projection::{project, LiveRowDirective, Projection, LIVE_SOURCE_HOLD};
pub use protocol::{BridgeError, DesktopProtocol, TrackKind};
pub use renderer::{fit_row_text, CaptionRenderer, CaptionPresentation, RenderedFrame, Truncation, HUD_TEXT_LEFT_PX};
pub use runtime::{run_cli, run_with_manifest, Clock, ParentLiveness, RuntimeFailure, StartupError, VisibilityController, VisibilityTick};
pub use state::OverlayCalibration;
pub use transcript::{CaptionEvent, LiveInputRow, SpeakerKey, TranscriptState};
pub use views::{DisplayMode, SettingsError, VrViewSettings};
```

Deleted names: `OverlayPresentationSnapshot`, `OverlayPresentationBlock`,
`OverlayPresentationBlockVariant`, `OverlayPresentationCalibration`, `OverlayState`,
`OverlayStateScene`, `OverlayStateSlot`, `PresentationScene`, `PresentationSlot`, `RuntimeState`,
`VISIBLE_SLOT_CAP`, `SLOT_ROW_STRIDE_PX`, `FIRST_SLOT_TOP_PX`, `CaptionUpdate`,
`OverlayBridgeEvent`, `BridgeControl`, the authenticated `BridgeClient` branch, and
`render_blocks` if no live user remains.

- [ ] **Step 1: Delete the dead files and the vendored DLL**

```powershell
git rm vr_overlay/src/bridge.rs vr_overlay/src/desktop_caption.rs vr_overlay/tests/state.rs `
  vr_overlay/vendor/openvr_api.dll vr_overlay/vendor/README.md
```

- [ ] **Step 2: Apply each known call-site edit**

1. `state.rs`: keep only `OverlayCalibration` and its `Default`; delete every other type,
   constant, and test in the file.
2. `openvr.rs`: replace the `OverlayPresentationCalibration` import/call with
   `OverlayCalibration`.
3. `lib.rs`: replace all exports with the post-removal surface above.
4. `tests/renderer.rs`: delete tests whose names start with `overlay_state_` and every snapshot
   import; keep `hud_*`, font, and glyph tests.
5. `tests/runtime.rs`: delete every test importing `OverlayPresentation*`, `OverlayState`,
   `BridgeClient`, `CaptionUpdate`, or `OverlayBridgeEvent`; keep the alpha and CLI tests.
6. `scripts/verify.ps1`: delete the `if (-not (Test-Path ... vendor\openvr_api.dll))` block and
   its `Fail-Environment` line.

- [ ] **Step 3: Run the full suite**

`cargo test --manifest-path vr_overlay/Cargo.toml`

Expected: zero failures; `rg "OverlayPresentation|OverlayState|BridgeClient|CaptionUpdate|OverlayBridgeEvent" vr_overlay/src vr_overlay/tests` returns no matches.

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
- Modify: `vr_overlay/README.md`
- Modify: `vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md` (only if names
  drifted during implementation)
- Test: `vr_overlay/tests/coordinator.rs`

**Interfaces:** diagnostics emit `event_kind`, `speaker_hash`, `sentence_ordinal`,
`dirty_slots`, `render_ms`, `submit_sequence`. Basic mode contains no caption text; detailed
mode caps a `preview=` field at 80 Unicode scalars.

- [ ] **Step 1: Write failing diagnostic tests** (`tests/coordinator.rs`)

```rust
#[test]
fn basic_diagnostics_never_contain_caption_text() {
    let log = support::render_logs(OverlayLoggingMode::Basic, &[src_commit("1", "A", "SECRETTEXT")]);
    assert!(!log.contains("SECRETTEXT"));
    assert!(log.contains("event_kind="));
    assert!(log.contains("submit_sequence="));
}

#[test]
fn detailed_diagnostics_cap_preview_length() {
    let long = "x".repeat(200);
    let log = support::render_logs(OverlayLoggingMode::Detailed, &[src_commit("1", "A", &long)]);
    let preview = log.split("preview=").nth(1).expect("preview field").lines().next().unwrap();
    assert!(preview.chars().count() <= 80);
}
```

- [ ] **Step 2: Run and verify RED**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator diagnostics -- --nocapture`

- [ ] **Step 3: Implement the diagnostics**

1. `logging.rs`: add `OverlayLogger::diagnostic(&self, fields: &[(&str, &str)])` writing
   `[overlay][DIAG] k=v ...`; basic mode drops any field named `text`/`preview`.
2. `runtime.rs`: emit one diagnostic line per reduced event
   (`event_kind`, `speaker_hash`, `sentence_ordinal`), one per render (`dirty_slots`,
   `render_ms`), and one per submit (`submit_sequence`); detailed mode adds `preview=` truncated
   to 80 chars.
3. `tests/support/mod.rs` + `logging.rs`: add a capturing test logger and `render_logs`:

```rust
// logging.rs (test constructor)
impl OverlayLogger {
    /// Test constructor: writes to a private captured buffer.
    pub fn capture(mode: OverlayLoggingMode) -> Self {
        Self::with_sink(mode, Arc::new(Mutex::new(Vec::new())))
    }
    pub fn captured(&self) -> String {
        self.sink_lines().join("\n")
    }
}

// tests/support/mod.rs
pub fn render_logs(mode: OverlayLoggingMode, events: &[CaptionEvent]) -> String {
    let clock = FakeClock::new(Instant::now());
    let logger = OverlayLogger::capture(mode);
    let mut coordinator = RuntimeCoordinator::for_test_with_logger(&logger);
    for event in events {
        coordinator.apply_event_for_test(event, clock.now());
    }
    coordinator.render_for_test(clock.now());
    logger.captured()
}
```

- [ ] **Step 4: Run and verify GREEN**

`cargo test --manifest-path vr_overlay/Cargo.toml --test coordinator diagnostics` passes.

- [ ] **Step 5: Update `README.md`**

Document the v7 envelope, no `session_token`, the single `/ws` path, one
`RinBridgeOverlay.exe`, and the no-sidecar static OpenVR linkage. Confirm the git-excluded
`vr_overlay/AGENTS.md` on disk already matches; do not add it.

- [ ] **Step 6: Commit**

```powershell
git add vr_overlay/src/logging.rs vr_overlay/src/runtime.rs vr_overlay/tests/support vr_overlay/tests/coordinator.rs vr_overlay/README.md
git commit -m "chore(vr): add HUD diagnostics and update the runtime contract"
```

---

### Task 10: Packaging probe

**Files:**
- Modify: `vr_overlay/scripts/verify.ps1`

**Interface:** the script gains three functions with fixed behavior:

```powershell
function Assert-NoStrayDlls([string]$releaseDir)   # throws if any *.dll exists
function Assert-SingleFilePackage([string]$exe)     # stages the exe, asserts exactly one file
function Assert-ContractProbe([string]$exe)         # runs --check-startup-contract == {"contract_version":7}
```

- [ ] **Step 1: Write the probe**

Append to `verify.ps1` after `cargo build --release`:

```powershell
function Assert-NoStrayDlls([string]$releaseDir) {
    $stray = @(Get-ChildItem -LiteralPath $releaseDir -Filter '*.dll' -File)
    if ($stray.Count -ne 0) { throw "release dir contains DLLs: $($stray.Name -join ', ')" }
}
function Assert-SingleFilePackage([string]$exe) {
    $stage = Join-Path ([System.IO.Path]::GetTempPath()) ("rin-package-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    Copy-Item -LiteralPath $exe -Destination (Join-Path $stage 'RinBridgeOverlay.exe')
    $files = @(Get-ChildItem -LiteralPath $stage -File -Recurse)
    if ($files.Count -ne 1 -or $files[0].Name -ne 'RinBridgeOverlay.exe') {
        throw "runtime package must contain exactly RinBridgeOverlay.exe; found: $($files.Name -join ', ')"
    }
    return $stage
}
function Assert-ContractProbe([string]$exe) {
    $out = & $exe --check-startup-contract
    if ($LASTEXITCODE -ne 0 -or $out.Trim() -ne '{"contract_version":7}') {
        throw "no-sidecar contract probe failed: exit=$LASTEXITCODE output=$out"
    }
}
$releaseDir = Join-Path $repoRoot 'vr_overlay\target\release'
Assert-NoStrayDlls $releaseDir
$stage = Assert-SingleFilePackage (Join-Path $releaseDir 'RinBridgeOverlay.exe')
Assert-ContractProbe (Join-Path $stage 'RinBridgeOverlay.exe')
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

---

### Task 11: Post-commit completion re-run

**Files:** none.

- [ ] **Step 1: Re-run the entire gate on the committed tree**

```powershell
git status --porcelain
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
powershell -ExecutionPolicy Bypass -File .\vr_overlay\scripts\verify.ps1
```

Expected: empty `git status`; only `vr_overlay/` paths; every command exits zero. Record the raw
output. A failure here must be fixed with a new commit, then this task is re-run from Step 1.

- [ ] **Step 2: Record the physical HMD acceptance checklist as pending**

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
| v7 accepted, v6 rejected, `session_token` rejected | Task 1 `manifest` tests |
| Settings validation | Task 1 `views` tests |
| Committed full text frozen; recency refreshed | `source_commit_refreshes_recency_and_settles_full_text` |
| Empty non-finals do not settle | `empty_non_final_does_not_settle_the_live_row` |
| `SourceEnd` speaker/ID guards | `source_end_settles_only_the_matching_speaker`, `source_end_with_conflicting_id_is_a_noop` |
| No-ID target binds/drops correctly | `target_without_id_binds_to_open_sentence`, `target_without_id_is_dropped_when_no_open_sentence` |
| Older target retained; refinement by id | `late_target_for_older_sentence_is_kept_in_the_reducer`, `refinement_resolves_by_id_and_replays_when_late` |
| `clear(true)` per track | `clear_true_is_per_track` |
| One-shot dismissal | `dismiss_settled_live_matches_and_consumes_once` |
| Multiple events per frame via pending queue | `one_frame_can_yield_multiple_events_through_the_pending_queue` |
| Segmented accumulation incl. language | `final_tokens_segment_by_sentence_speaker_and_language` |
| One live row from last speaker | `multi_speaker_non_final_emits_one_live_event_for_the_last_speaker` |
| Malformed frames ignored | `malformed_frames_are_ignored_without_breaking_state` |
| Original capacity 1/2/3 | `original_capacity_one_two_three_evicts_least_recent` |
| Translation capacity + newest target | `translation_capacity_evicts_least_recent_speaker`, `translation_shows_only_the_newest_target_per_speaker` |
| Both fixed slots / cross-ID / over-capacity | `both_mode_*` tests |
| Live slot 4; dismiss directive | `live_row_always_occupies_slot_four`, `settled_row_reports_dismiss_after_hold_and_handoff` |
| Language + separate labels | `language_propagates_and_labels_stay_separate` |
| Geometry key: label in, kind out | `speaker_label_is_part_of_the_layout_cache_key`, `draft_and_final_share_the_geometry_cache_key` |
| CJK / ellipsis / fixed origins | Task 5 renderer tests |
| Mandatory alpha | `overlay_alpha_is_clamped_and_reaches_the_submitter` |
| Wake shows + alpha same turn | `visibility_wakes_with_show_and_alpha_in_one_tick` |
| Hold consumed once, no busy loop / resurrect | `live_hold_deadline_is_armed_once_and_never_busy_loops`, `dismissed_live_row_does_not_resurrect_on_mode_switch` |
| Transparent frame before ready | `transparent_empty_frame_is_submitted_before_ready` |
| Diagnostics | Task 9 tests |
| No-sidecar single-file package | Task 10 probe |
| Post-commit full re-run | Task 11 |
| Physical HMD behavior | Task 11 Step 2 (pending) |

## Open Risks

- **Renderer churn.** Land Task 5 additively; remove `render_blocks` only in Task 8.
- **`poll_immediate` drain.** `tokio-tungstenite` has no poll API; `try_next_event` must be
  async and is tested with pre-queued frames (`one_frame_can_yield_multiple_events_...`).
- **v7 manifest handshake.** The desktop must produce v7 before a packaged end-to-end run.
- **Non-Windows CI.** `renderer/layout.rs` is Windows-gated; `hud_*` tests must pass on the
  non-Windows heuristic path too.
- **Vendored DLL deletion.** Confirm no in-tree reference to `vendor/` after Task 8.

## Documentation-Only Bootstrap Commit

Commit the reworked spec and plan alone (never `AGENTS.md`):

```powershell
git add vr_overlay/docs/superpowers/specs/2026-09-12-vr-hud-window-redesign.md `
  vr_overlay/docs/superpowers/plans/2026-09-12-vr-hud-window-redesign.md
git commit -m "docs(vr): revise HUD redesign spec and implementation plan"
```

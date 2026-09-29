# Realtime Independent Caption Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live source and translation update independently in SteamVR, with translation above source and no dependency on a later sentence.

**Architecture:** Add a pure `DesktopCaptionReducer` between desktop JSON and `BridgeClient`, then map its independent line changes into the existing runtime. Verify the causal path with reducer unit tests and a production-frame WebSocket test ending at a recording submitter.

**Tech Stack:** Rust 2021, serde_json, tokio, tokio-tungstenite, existing DirectWrite renderer and OpenVR submission trait, PowerShell verification.

**Spec:** `vr_overlay/docs/superpowers/specs/2026-09-10-realtime-independent-caption-lines-design.md`

## Global Constraints

- All changed paths must remain under `vr_overlay/`.
- Top row is translation; bottom row is source/original speech.
- `non_final_tokens` is a replaceable snapshot; `final_tokens` is an ordered incremental list.
- Source and translation updates never clear or delay one another.
- Refinement staleness is relative to the sentence represented by the translation line.
- One bridge event that changes a visible line requests and drains one redraw in the same event turn.
- Automated tests do not replace the physical SteamVR/HMD smoke test.

---

### Task 1: Pure reducer and live snapshot behavior

**Files:**
- Create: `vr_overlay/src/desktop_caption.rs`
- Modify: `vr_overlay/src/lib.rs`
- Test: `vr_overlay/src/desktop_caption.rs`

**Interfaces:**
- Consumes: `serde_json::Map<String, Value>` desktop messages.
- Produces: `DesktopCaptionReducer::apply_message(&Map<String, Value>) -> DesktopCaptionOutcome`.
- Produces: independent `DesktopCaptionChange { source: Option<String>, translation: Option<String> }` values for the bridge.

- [ ] **Step 1: Write failing live-source and independent-line tests**

Add unit tests beside the reducer. Use a helper that parses an object and passes its map to the reducer:

```rust
fn apply(reducer: &mut DesktopCaptionReducer, value: Value) -> DesktopCaptionOutcome {
    reducer.apply_message(value.as_object().unwrap())
}

fn change(source: Option<&str>, translation: Option<&str>) -> DesktopCaptionOutcome {
    DesktopCaptionOutcome::Change(DesktopCaptionChange {
        source: source.map(str::to_owned),
        translation: translation.map(str::to_owned),
    })
}

fn source_draft(text: &str) -> Value {
    json!({"type":"update","final_tokens":[],"non_final_tokens":[{
        "text":text,"translation_status":"original","is_final":false
    }]})
}

fn translation_draft(text: &str) -> Value {
    json!({"type":"update","final_tokens":[],"non_final_tokens":[{
        "text":text,"translation_status":"translation","is_final":false
    }]})
}

#[test]
fn non_final_source_snapshot_replaces_live_source() {
    let mut reducer = DesktopCaptionReducer::default();
    assert_eq!(
        apply(&mut reducer, json!({
            "type": "update",
            "final_tokens": [],
            "non_final_tokens": [{
                "text": "Hello",
                "translation_status": "original",
                "is_final": false
            }]
        })),
        change(Some("Hello"), None)
    );
    assert_eq!(
        apply(&mut reducer, json!({
            "type": "update",
            "final_tokens": [],
            "non_final_tokens": [{
                "text": "Hello world",
                "translation_status": "original",
                "is_final": false
            }]
        })),
        change(Some("Hello world"), None)
    );
}

#[test]
fn translation_snapshot_does_not_replace_source() {
    let mut reducer = DesktopCaptionReducer::default();
    let _ = apply(&mut reducer, source_draft("source A"));
    assert_eq!(
        apply(&mut reducer, translation_draft("translation A")),
        change(None, Some("translation A"))
    );
    assert_eq!(reducer.visible_source(), "source A");
}
```

- [ ] **Step 2: Run the reducer tests and verify RED**

Run:

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml desktop_caption -- --nocapture
```

Expected: compilation fails because `DesktopCaptionReducer` and its outcomes do not exist.

- [ ] **Step 3: Implement the reducer data structures and non-final snapshot transition**

Create these concrete types and keep network/runtime types out of this module:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DesktopCaptionChange {
    pub(crate) source: Option<String>,
    pub(crate) translation: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum DesktopCaptionOutcome {
    Change(DesktopCaptionChange),
    Clear,
    Noop(DesktopCaptionNoopReason),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DesktopCaptionNoopReason {
    Unrelated,
    Unchanged,
    StaleRefinement,
    UncorrelatedRefinement,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SentenceRef {
    id: String,
    ordinal: u64,
}

#[derive(Debug, Default)]
struct CaptionLineState {
    visible_text: String,
    owner: Option<SentenceRef>,
}

#[derive(Debug, Default)]
struct FinalAccumulator {
    text: String,
    owner: Option<SentenceRef>,
    replace_on_next_token: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalTokenFingerprint {
    is_separator: bool,
    line: LineKind,
    text: String,
    sentence_id: Option<String>,
    is_final: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LineKind {
    Source,
    Translation,
    Separator,
}

type FinalTokensFingerprint = Vec<FinalTokenFingerprint>;

#[derive(Debug, Default)]
pub(crate) struct DesktopCaptionReducer {
    source: CaptionLineState,
    translation: CaptionLineState,
    source_final: FinalAccumulator,
    translation_final: FinalAccumulator,
    sentence_ordinals: HashMap<String, u64>,
    recent_source_by_sentence: VecDeque<(SentenceRef, String)>,
    next_sentence_ordinal: u64,
    previous_update_final_tokens: Option<FinalTokensFingerprint>,
}
```

For `update`, rebuild source and translation draft strings separately from
`non_final_tokens`. Apply a non-empty draft only when that line did not receive
a final token in the same message. Compare against `visible_text`; emit `Some`
only for a real change. Do not blank a line when its draft is absent.

- [ ] **Step 4: Run the reducer tests and verify GREEN**

Run the Task 1 command. Expected: both new tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add vr_overlay/src/desktop_caption.rs vr_overlay/src/lib.rs
git commit -m "feat(vr): reduce live desktop caption snapshots"
```

### Task 2: Executable final-token accumulation

**Files:**
- Modify: `vr_overlay/src/desktop_caption.rs`
- Test: `vr_overlay/src/desktop_caption.rs`

**Interfaces:**
- Consumes: ordered `update.final_tokens` arrays.
- Produces: deterministic per-line final accumulators and one-message replay detection.

- [ ] **Step 1: Write failing final-token rule tests**

Add separate tests for all ambiguous rules:

```rust
fn final_token(sentence_id: &str, text: &str, status: &str) -> Value {
    json!({
        "text": text,
        "translation_status": status,
        "llm_sentence_id": sentence_id,
        "is_final": true
    })
}

fn final_source_frame(sentence_id: &str, text: &str) -> Value {
    json!({
        "type": "update",
        "final_tokens": [final_token(sentence_id, text, "original")],
        "non_final_tokens": []
    })
}

fn final_translation_frame(sentence_id: &str, text: &str) -> Value {
    json!({
        "type": "update",
        "final_tokens": [final_token(sentence_id, text, "translation")],
        "non_final_tokens": []
    })
}

fn separator_frame() -> Value {
    json!({
        "type": "update",
        "final_tokens": [{"is_separator": true, "is_final": true}],
        "non_final_tokens": []
    })
}

fn seeded_pair_a() -> DesktopCaptionReducer {
    let mut reducer = DesktopCaptionReducer::default();
    let _ = apply(&mut reducer, final_source_frame("A", "source A"));
    let _ = apply(&mut reducer, final_translation_frame("A", "translation A"));
    reducer
}

#[test]
fn consecutive_identical_final_arrays_are_idempotent() {
    let mut reducer = DesktopCaptionReducer::default();
    let frame = final_source_frame("A", "very");
    assert_eq!(apply(&mut reducer, frame.clone()), change(Some("very"), None));
    assert_eq!(apply(&mut reducer, frame), DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged));
    assert_eq!(reducer.visible_source(), "very");
}

#[test]
fn equal_deltas_inside_one_frame_are_both_appended() {
    let mut reducer = DesktopCaptionReducer::default();
    let outcome = apply(&mut reducer, json!({
        "type": "update",
        "final_tokens": [
            final_token("A", "very", "original"),
            final_token("A", "very", "original")
        ],
        "non_final_tokens": []
    }));
    assert_eq!(outcome, change(Some("veryvery"), None));
}

#[test]
fn strictly_longer_whole_line_replay_replaces_accumulator() {
    let mut reducer = DesktopCaptionReducer::default();
    let _ = apply(&mut reducer, final_source_frame("A", "Hello"));
    assert_eq!(
        apply(&mut reducer, final_source_frame("A", "Hello world")),
        change(Some("Hello world"), None)
    );
}

#[test]
fn separator_rollover_is_consumed_independently_per_line() {
    let mut reducer = seeded_pair_a();
    let _ = apply(&mut reducer, separator_frame());
    assert_eq!(apply(&mut reducer, final_source_frame("B", "source B")), change(Some("source B"), None));
    assert_eq!(reducer.visible_translation(), "translation A");
}
```

- [ ] **Step 2: Run tests and verify RED**

Run the Task 1 command. Expected: assertions show duplicate accumulation,
repeated-delta loss, or incorrect separator rollover.

- [ ] **Step 3: Implement the six ordered accumulation rules**

Canonicalize the current `final_tokens` array into an equality-comparable tuple
vector. Suppress final accumulation only when it equals the immediately prior
`update` fingerprint; still process non-final snapshots. Clear the fingerprint
on every non-`update` message.

For each line, replace on its own rollover flag or a newer owner; replace only
for a strictly longer prefix replay; otherwise append the delta exactly. Never
use `ends_with` for deduplication.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command. Expected: all reducer tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add vr_overlay/src/desktop_caption.rs
git commit -m "feat(vr): define final caption accumulation"
```

### Task 3: Translation-owner correlation and A/B regression

**Files:**
- Modify: `vr_overlay/src/desktop_caption.rs`
- Test: `vr_overlay/src/desktop_caption.rs`

**Interfaces:**
- Consumes: `llm_sentence_id` on tokens and `sentence_id` on `refine_result`.
- Produces: translation changes whose stale comparison uses `translation.owner` only.

- [ ] **Step 1: Write the exact failing A/B/refine-A regression**

```rust
#[test]
fn refine_for_translation_a_is_accepted_after_source_b() {
    let mut reducer = DesktopCaptionReducer::default();

    assert_eq!(apply(&mut reducer, final_source_frame("A", "source A")), change(Some("source A"), None));
    assert_eq!(apply(&mut reducer, final_translation_frame("A", "draft A")), change(None, Some("draft A")));
    assert_eq!(apply(&mut reducer, separator_frame()), DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged));
    assert_eq!(apply(&mut reducer, final_source_frame("B", "source B")), change(Some("source B"), None));

    assert_eq!(
        apply(&mut reducer, json!({
            "type": "refine_result",
            "sentence_id": "A",
            "source": "source A",
            "original_translation": "draft A",
            "refined_translation": "refined A",
            "no_change": false
        })),
        change(None, Some("refined A"))
    );
    assert_eq!(reducer.visible_source(), "source B");
    assert_eq!(reducer.visible_translation(), "refined A");
    assert_eq!(reducer.translation_owner_id(), Some("A"));
}
```

Also add `refine_older_than_translation_owner_is_rejected`, where source A and
B are registered, translation B is visible, and refine A yields
`Noop(StaleRefinement)`.

- [ ] **Step 2: Run the named regression and verify RED**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml refine_for_translation_a_is_accepted_after_source_b -- --exact --nocapture
```

Expected: current source-relative correlation rejects refine A or the new API
is not implemented.

- [ ] **Step 3: Implement sentence registration and translation-owner comparison**

Register opaque IDs with monotonically increasing ordinals when first observed
on source or translation tokens. Store the owner on each visible line. Accept a
refinement when its ordinal equals or exceeds the translation owner ordinal;
reject only a lower known ordinal. Do not compare it to source owner B. Keep ID
ordinals until `clear`, and cap normalized source fallback entries at 256.

- [ ] **Step 4: Run all reducer tests and verify GREEN**

Run the Task 1 command. Expected: A/B/refine-A and stale-A/translation-B both pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add vr_overlay/src/desktop_caption.rs
git commit -m "fix(vr): correlate refinements to translation owner"
```

### Task 4: Bridge/runtime wiring and submitted-frame regression

**Files:**
- Modify: `vr_overlay/src/bridge.rs`
- Modify: `vr_overlay/src/runtime.rs`
- Modify: `vr_overlay/tests/runtime.rs`
- Create: `vr_overlay/tests/fixtures/desktop_ws_single_sentence.jsonl`

**Interfaces:**
- Consumes: `DesktopCaptionOutcome` from Task 1.
- Produces: existing `OverlayBridgeEvent::Captions(CaptionUpdate)`.
- Verifies: `OverlayFrameSubmitter::submit_frame` receives a changed frame before any next sentence.

- [ ] **Step 1: Add the production-shaped fixture and failing bridge test**

The JSONL fixture contains exactly these frames and no next sentence:

```json
{"type":"update","final_tokens":[],"non_final_tokens":[{"text":"source A","speaker":"1","translation_status":"original","is_final":false}]}
{"type":"update","final_tokens":[{"text":"source A","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},{"text":"draft A","speaker":"1","translation_status":"translation","llm_sentence_id":"A","is_final":true},{"is_separator":true,"is_final":true}],"non_final_tokens":[]}
{"type":"refine_result","sentence_id":"A","source":"source A","original_translation":"draft A","refined_translation":"refined A","no_change":false}
```

Update the bridge test to replay this file and assert each resulting caption
event without inserting a later source frame.

- [ ] **Step 2: Add a failing event-loop/recording-submitter assertion**

Drive the fixture through `BridgeClient`, `OverlayRuntime`, the real test
renderer, and `RecordingSubmitter`. Before allowing the scripted server to
close, wait with a bounded timeout for the recording submitter to observe the
refinement submission. Assert runtime state maps:

```rust
assert_eq!(block.primary_text, "refined A"); // top: translation
assert_eq!(block.secondary_text, "source A"); // bottom: source
assert!(submitter.calls >= 3); // initial, live source, completed pair/refinement
```

- [ ] **Step 3: Run the integration regression and verify RED**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml desktop_ws_single_sentence -- --nocapture
```

Expected: current bridge ignores `non_final_tokens`, or row mapping is reversed.

- [ ] **Step 4: Wire reducer outcomes into the bridge and correct row mapping**

Replace `DesktopCaptionLines` parsing with `DesktopCaptionReducer`. Map source
and translation options to the existing booleans without filling an unchanged
line. In `OverlayRuntime`, preserve either missing line from prior state and
construct the renderer block as:

```rust
primary_text: translation_text,
secondary_text: source_text,
```

Keep the event loop's existing `handle_event` followed by `drain_redraw` order.

- [ ] **Step 5: Run integration and full Rust tests**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml desktop_ws_single_sentence -- --nocapture
cargo test --manifest-path vr_overlay/Cargo.toml
```

Expected: both commands pass with zero failures.

- [ ] **Step 6: Commit Task 4**

```powershell
git add vr_overlay/src/bridge.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs vr_overlay/tests/fixtures/desktop_ws_single_sentence.jsonl
git commit -m "fix(vr): submit independent caption lines immediately"
```

### Task 5: Diagnostics and one-command verification

**Files:**
- Modify: `vr_overlay/src/logging.rs`
- Modify: `vr_overlay/src/bridge.rs`
- Modify: `vr_overlay/src/runtime.rs`
- Create: `vr_overlay/scripts/verify.ps1`
- Modify: `vr_overlay/AGENTS.md`
- Modify: `vr_overlay/README.md`
- Test: `vr_overlay/tests/runtime.rs`

**Interfaces:**
- Produces: causal diagnostic records for receive, reduce, redraw, and submit.
- Produces: `vr_overlay/scripts/verify.ps1` as the supported local completion gate.

- [ ] **Step 1: Add failing diagnostic assertions**

Extend the existing logger tests to require frame sequence, message type,
changed-line flags or no-op reason, runtime revision/redraw, and submit sequence.
Assert basic mode contains no subtitle text; detailed mode may contain a
bounded escaped preview.

- [ ] **Step 2: Run diagnostic tests and verify RED**

```powershell
cargo test --manifest-path vr_overlay/Cargo.toml diagnostic -- --nocapture
```

Expected: required causal fields are absent.

- [ ] **Step 3: Add non-blocking causal logging**

Log after JSON decoding, after reducer decision, when the redraw latch changes,
and after `submit_frame` succeeds. Use monotonically increasing local sequence
numbers. Keep content out of basic mode and cap detailed previews at 80 Unicode
scalar values.

- [ ] **Step 4: Create the verification script**

The script uses `$PSScriptRoot` to locate the repository, checks `cargo`,
`cmake`, MSBuild, Windows SDK readability, and `vr_overlay/vendor/openvr_api.dll`,
then executes in order:

```powershell
git diff --check origin/main...HEAD
$changed = @(git diff --name-only origin/main...HEAD)
if ($changed | Where-Object { $_ -notlike 'vr_overlay/*' }) { throw 'Rust-only PR boundary violated' }
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
```

Exit before Cargo with an `ENVIRONMENT PREFLIGHT FAILED:` prefix when a tool or
SDK is unavailable; use Cargo's exit code for compilation/test failures.

- [ ] **Step 5: Update contract documentation**

Document top translation/bottom source, non-final snapshot semantics,
translation-owner stale comparison, the A/B/refine-A invariant, the verification
script, and the required one-sentence physical HMD test.

- [ ] **Step 6: Run diagnostics and complete verification gate**

```powershell
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
cargo fmt --manifest-path vr_overlay/Cargo.toml -- --check
cargo test --manifest-path vr_overlay/Cargo.toml
cargo build --manifest-path vr_overlay/Cargo.toml --release
```

Expected: all commands exit zero; changed paths all begin with `vr_overlay/`.
Record the physical HMD test as pending unless actually performed.

- [ ] **Step 7: Commit Task 5**

```powershell
git add vr_overlay/src/logging.rs vr_overlay/src/bridge.rs vr_overlay/src/runtime.rs vr_overlay/tests/runtime.rs vr_overlay/scripts/verify.ps1 vr_overlay/AGENTS.md vr_overlay/README.md
git commit -m "chore(vr): add caption timing diagnostics and verification"
```

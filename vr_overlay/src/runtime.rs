use std::time::{Duration, Instant};

use thiserror::Error;

use crate::bridge::{BridgeClient, BridgeError, OverlayBridgeEvent};
use crate::logging::OverlayLogger;
use crate::manifest::{self, OverlayManifest};
use crate::openvr::{self, OpenVrOverlay, OverlayFrameSubmitter};
use crate::renderer::{
    CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionRenderError, CaptionRenderer,
};
use crate::state::{
    OverlayPresentationBlockVariant, OverlayPresentationSnapshot, PresentationScene,
    PresentationSlot, RuntimeState, FIRST_SLOT_TOP_PX, SLOT_ROW_STRIDE_PX,
};

#[derive(Debug, Error)]
pub enum StartupError {
    #[error("manifest contract mismatch: {0}")]
    ContractMismatch(String),
    #[error("bridge auth failed: {0}")]
    BridgeAuth(String),
    #[error("SteamVR not installed")]
    SteamVrNotInstalled,
    #[error("SteamVR not running")]
    SteamVrNotRunning,
    #[error("HMD not found")]
    HmdNotFound,
    #[error("OpenVR init failed: {0}")]
    OpenVrInit(String),
    #[error("renderer init failed: {0}")]
    RendererInit(String),
    #[error("bridge error: {0}")]
    Bridge(String),
    #[error("runtime disconnected")]
    RuntimeDisconnected,
    #[error("overlay OpenVR error: {0}")]
    OpenVr(String),
    #[error("manifest error: {0}")]
    Manifest(String),
}

impl StartupError {
    /// 标准化退出码 (测试 tests/runtime.rs:370-384 钉死)。
    pub fn exit_code(&self) -> i32 {
        match self {
            StartupError::ContractMismatch(_) => 10,
            StartupError::BridgeAuth(_) => 12,
            StartupError::SteamVrNotInstalled
            | StartupError::SteamVrNotRunning
            | StartupError::HmdNotFound
            | StartupError::OpenVrInit(_) => 20,
            StartupError::RendererInit(_) => 21,
            StartupError::Bridge(_)
            | StartupError::RuntimeDisconnected
            | StartupError::OpenVr(_)
            | StartupError::Manifest(_) => 1,
        }
    }

    /// 机器可读失败原因 (测试 tests/runtime.rs:386-397 钉死; 用于 EVENT 行)。
    pub fn failure_reason(&self) -> &'static str {
        match self {
            StartupError::ContractMismatch(_) => "contract_mismatch",
            StartupError::BridgeAuth(_) => "auth_failed",
            StartupError::SteamVrNotInstalled => "steamvr_not_installed",
            StartupError::SteamVrNotRunning => "steamvr_not_running",
            StartupError::HmdNotFound => "hmd_not_found",
            StartupError::OpenVrInit(_) => "openvr_init_failed",
            StartupError::RendererInit(_) => "renderer_init_failed",
            StartupError::Bridge(_) => "bridge_error",
            StartupError::RuntimeDisconnected => "runtime_disconnected",
            StartupError::OpenVr(_) => "openvr_error",
            StartupError::Manifest(_) => "manifest_error",
        }
    }
}

impl From<openvr::OpenVrError> for StartupError {
    fn from(e: openvr::OpenVrError) -> Self {
        Self::OpenVr(e.to_string())
    }
}

impl From<crate::renderer::CaptionRenderError> for StartupError {
    fn from(e: crate::renderer::CaptionRenderError) -> Self {
        Self::RendererInit(e.to_string())
    }
}

/// 将 OpenVR 启动预检错误 1:1 映射到 `StartupError`(exit 20 系列)。
impl From<openvr::OpenVrStartupPreflightError> for StartupError {
    fn from(e: openvr::OpenVrStartupPreflightError) -> Self {
        match e {
            openvr::OpenVrStartupPreflightError::SteamVrNotInstalled => {
                StartupError::SteamVrNotInstalled
            }
            openvr::OpenVrStartupPreflightError::SteamVrNotRunning => {
                StartupError::SteamVrNotRunning
            }
            openvr::OpenVrStartupPreflightError::HmdNotFound => StartupError::HmdNotFound,
            openvr::OpenVrStartupPreflightError::Init(message) => StartupError::OpenVrInit(message),
        }
    }
}

/// A failure produced by the event-driven runtime core (decoupled from OpenVR).
///
/// Unlike `StartupError` (startup-phase fatal conditions), `RuntimeFailure`
/// represents the recovery-able / per-event failures surfaced by the core while
/// it is running (driving the bridge + applying snapshots).
#[derive(Debug, Error)]
pub enum RuntimeFailure {
    #[error("overlay OpenVR error: {0}")]
    OpenVr(String),
    #[error("renderer error: {0}")]
    Renderer(String),
    #[error("runtime disconnected")]
    RuntimeDisconnected,
}

impl RuntimeFailure {
    /// 机器可读失败原因 (测试 tests/runtime.rs:1562 钉死)。
    pub fn failure_reason(&self) -> &'static str {
        match self {
            RuntimeFailure::OpenVr(_) => "openvr_error",
            RuntimeFailure::Renderer(_) => "renderer_error",
            RuntimeFailure::RuntimeDisconnected => "runtime_disconnected",
        }
    }
}

impl From<openvr::OpenVrError> for RuntimeFailure {
    fn from(e: openvr::OpenVrError) -> Self {
        Self::OpenVr(e.to_string())
    }
}

impl From<CaptionRenderError> for RuntimeFailure {
    fn from(e: CaptionRenderError) -> Self {
        Self::Renderer(e.to_string())
    }
}

/// Result of applying a presentation snapshot to the runtime state.
///
/// Tests match `Applied { visual_changed, redraw_requested, .. }` (see
/// tests/runtime.rs:604-611, 938-943, 956-961). `visual_changed` indicates the
/// rendered picture differed from the previous one; `redraw_requested` indicates
/// the compositor should submit a fresh texture (e.g. a font change).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotApplyOutcome {
    Applied {
        visual_changed: bool,
        redraw_requested: bool,
        /// True when a previously-present slot was dropped by this snapshot.
        slot_removed: bool,
    },
    /// Snapshot carried no change worth re-rendering.
    Noop,
}

/// Delay after the overlay content becomes empty before the overlay is hidden.
const IDLE_HIDE_DELAY: Duration = Duration::from_millis(500);

/// Maximum number of simultaneously-visible presentation rows.
const VISIBLE_SLOT_CAP: usize = 2;

/// HMD 预检失败后的重试窗口 (`可调`)。SteamVR 可能在 overlay 启动后才把
/// 头显加载完成, 因此在放弃前给一段重试时间, 每 `HMD_RETRY_INTERVAL_MS`
/// 重试一次; 超时后才报 `no_hmd`(exit 20)。
const HMD_RETRY_DEADLINE_MS: u64 = 10_000;
const HMD_RETRY_INTERVAL_MS: u64 = 1_000;

/// Event-driven version of `OverlayRuntime` — a pure state core holding no
/// OpenVR / renderer references. The shell (OpenVR overlay, renderer, logger,
/// render loop) lives in `run_with_manifest` and drives this core through
/// `handle_event` / `apply_snapshot` / `submit_frame_if_needed`.
pub struct OverlayRuntime {
    state: RuntimeState,
    /// Real overlay instance id used in `overlay_ready` EVENT payloads.
    /// Defaults to `"overlay"` for in-process tests; the production shell sets
    /// the real id via `set_instance_id` before driving the event loop.
    overlay_instance_id: String,
    ready_sent: bool,
    stopped: bool,
    redraw_requested: bool,
    calibration_pending: bool,
    overlay_visible: bool,
    idle_hide_pending_since: Option<Instant>,
    entry_counter: u64,
    latest_revision: u64,
}

impl OverlayRuntime {
    pub fn new(initial: OverlayPresentationSnapshot) -> Self {
        let mut runtime = Self {
            state: RuntimeState::default(),
            overlay_instance_id: "overlay".into(),
            ready_sent: false,
            stopped: false,
            redraw_requested: true,
            calibration_pending: false,
            overlay_visible: false,
            idle_hide_pending_since: None,
            entry_counter: 0,
            latest_revision: initial.revision,
        };
        // Seed the scene from the initial snapshot (always a fresh layout).
        let _ = runtime.apply_snapshot(initial);
        runtime
    }

    /// Read-only view of the pure presentation state.
    pub fn state(&self) -> &RuntimeState {
        &self.state
    }

    /// Current caption blocks in slot order (the thing rendered to a texture).
    pub fn caption_blocks(&self) -> Vec<CaptionBlock> {
        // 两槽位设计不变: 一块一槽。但槽号是分配序(滑窗淘汰后新句可能拿到
        // slot 0), 视觉行序必须按句子时间序: 旧句在上、新句在下(同桌面)。
        // 锦点 = 行位固定: 行0 = 40px, 行1 = 40 + 504px (已译块实测高≤498,
        // 不会重叠)。
        let mut slots: Vec<_> = self.state.scene().occupied().collect();
        slots.sort_by_key(|slot| slot.slot_entry_order);
        slots
            .into_iter()
            .enumerate()
            .map(|(row, slot)| slot.to_caption_block_at_row(row))
            .collect()
    }

    pub fn is_stopped(&self) -> bool {
        self.stopped
    }

    pub fn ready_sent(&self) -> bool {
        self.ready_sent
    }

    /// Set the real overlay instance id used in the `overlay_ready` EVENT.
    /// The production shell calls this before the event loop; tests keep the
    /// default `"overlay"` id.
    pub fn set_instance_id(&mut self, overlay_instance_id: &str) {
        self.overlay_instance_id = overlay_instance_id.to_string();
    }

    /// Reset the redraw latch (used after draining/handling a snapshot).
    pub fn clear_redraw_flag(&mut self) {
        self.redraw_requested = false;
    }

    /// Apply a presentation snapshot to the pure state, returning whether the
    /// picture changed. Slot identity is preserved across updates so a given
    /// occupant keeps its fixed vertical position while its text changes.
    pub fn apply_snapshot(
        &mut self,
        snapshot: OverlayPresentationSnapshot,
    ) -> SnapshotApplyOutcome {
        let mut visual_changed = false;
        let mut redraw_requested = false;
        let mut slot_removed = false;

        // 1) Calibration.
        let new_cal = snapshot.calibration.to_overlay_calibration();
        if self.state.calibration() != &new_cal {
            self.state.set_calibration(new_cal);
            self.calibration_pending = true;
            visual_changed = true;
            redraw_requested = true;
        }

        // 2) Scene / slot assignment.
        let incoming: Vec<_> = snapshot.blocks.into_iter().take(VISIBLE_SLOT_CAP).collect();

        let mut scene = PresentationScene::default();
        let mut used_incoming = vec![false; incoming.len()];

        // 2a) Write-through updates for occupants that keep a matched slot.
        for slot_index in 0..self.state.scene().slots().len() {
            let previous = match self
                .state
                .scene()
                .slots()
                .get(slot_index)
                .and_then(Option::as_ref)
            {
                Some(previous) => previous,
                None => continue,
            };
            if let Some(found) = incoming.iter().position(|b| {
                b.occupant_key == previous.occupant_key
                    && b.appearance_seq == previous.appearance_seq
            }) {
                let block = incoming[found].clone();
                let mut updated = previous.clone();
                let changed = update_slot_content(&mut updated, &block);
                scene.set_slot(slot_index, Some(updated));
                used_incoming[found] = true;
                if changed.0 {
                    visual_changed = true;
                }
                if changed.1 {
                    redraw_requested = true;
                }
            } else {
                // Occupant no longer present — mark for removal.
                slot_removed = true;
                scene.set_slot(slot_index, None);
            }
        }

        // 2b) Assign any new occupants to the first free slot.
        for (i, block) in incoming.iter().enumerate() {
            if used_incoming[i] {
                continue;
            }
            let free_index = (0..VISIBLE_SLOT_CAP)
                .find(|idx| scene.slots().get(*idx).and_then(Option::as_ref).is_none());
            let slot_index = match free_index {
                Some(idx) => idx,
                None => {
                    // All slots full: replace the oldest entry (lowest entry order)
                    // to keep the window bounded.
                    let mut oldest: Option<(usize, u64)> = None;
                    for (idx, slot) in scene.slots().iter().enumerate() {
                        if let Some(slot) = slot {
                            let key = (idx, slot.slot_entry_order);
                            if oldest.map_or(true, |(_, order)| key.1 < order) {
                                oldest = Some(key);
                            }
                        }
                    }
                    match oldest {
                        Some((idx, _)) => idx,
                        None => scene.slots().len().min(VISIBLE_SLOT_CAP),
                    }
                }
            };
            let slot = PresentationSlot::from_block(block, slot_index, self.entry_counter);
            self.entry_counter += 1;
            scene.set_slot(slot_index, Some(slot));
            visual_changed = true;
            redraw_requested = true;
        }

        // 3) Commit scene if it changed.
        if self.state.maybe_replace_scene(scene) {
            visual_changed = true;
        }

        // Clear any pending idle-hide when fresh content (or an explicit empty
        // expectation) resets the window — non-empty content cancels a pending hide.
        if !self.caption_blocks().is_empty() {
            self.idle_hide_pending_since = None;
        }

        if self.latest_revision != snapshot.revision {
            self.latest_revision = snapshot.revision;
        }

        if visual_changed || redraw_requested {
            self.redraw_requested = true;
            self.idle_hide_pending_since = if self.caption_blocks().is_empty() {
                Some(self.idle_hide_pending_since.unwrap_or_else(Instant::now))
            } else {
                None
            };
            SnapshotApplyOutcome::Applied {
                visual_changed,
                redraw_requested,
                slot_removed,
            }
        } else {
            SnapshotApplyOutcome::Noop
        }
    }

    /// Handle one bridge event, mutating state (render loop is driven
    /// separately via `submit_frame_if_needed` / `run_event_loop`).
    pub async fn handle_event(&mut self, event: OverlayBridgeEvent) -> Result<(), RuntimeFailure> {
        match event {
            OverlayBridgeEvent::Shutdown => {
                self.stopped = true;
            }
            OverlayBridgeEvent::Snapshot(snapshot) => {
                self.apply_snapshot(snapshot);
            }
            OverlayBridgeEvent::Captions(update) => {
                if !update.self_text.is_empty() || !update.peer.is_empty() {
                    let block = crate::state::OverlayPresentationBlock {
                        id: "caption:1".into(),
                        occupant_key: "caption:1".into(),
                        appearance_seq: self.latest_revision,
                        channel: if !update.self_text.is_empty() {
                            "self"
                        } else {
                            "peer"
                        }
                        .into(),
                        block_variant: OverlayPresentationBlockVariant::Finalized,
                        primary_text: update.self_text,
                        secondary_text: update.peer,
                        secondary_enabled: true,
                        primary_language: None,
                        secondary_language: None,
                        update_id: None,
                        origin_wall_clock_ms: None,
                        session_scope: None,
                    };
                    let snapshot = OverlayPresentationSnapshot {
                        revision: self.latest_revision + 1,
                        calibration: Default::default(),
                        blocks: vec![block],
                    };
                    self.apply_snapshot(snapshot);
                }
            }
            OverlayBridgeEvent::Control(_)
            | OverlayBridgeEvent::Heartbeat
            | OverlayBridgeEvent::AuthError(_) => {
                // Control/logging mode and heartbeat are handled by the shell;
                // auth errors are escalated at startup (Task 4).
            }
        }
        Ok(())
    }

    fn handle_bridge_loss(&mut self) -> Result<(), RuntimeFailure> {
        if self.ready_sent {
            Err(RuntimeFailure::RuntimeDisconnected)
        } else {
            Ok(())
        }
    }

    /// Render the current caption blocks and submit a texture if needed, then
    /// reveal the overlay once (gating the `overlay_ready` EVENT on the first
    /// successful texture submit).
    pub async fn submit_frame_if_needed(
        &mut self,
        renderer: &CaptionRenderer,
        submitter: &mut impl crate::openvr::OverlayFrameSubmitter,
        bridge: &mut BridgeClient,
        _logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        let blocks = self.caption_blocks();
        let frame = renderer.render_blocks(blocks)?;
        let has_text = !frame.layout().visible_blocks.is_empty();

        let first = !self.ready_sent;
        let need_show = has_text && !self.overlay_visible && (first || self.redraw_requested);

        // Early-out if nothing needs to be drawn (no ready gate, no redraw,
        // no visibility transition) and the picture is unchanged.
        if !first && !self.redraw_requested && !need_show {
            return Ok(());
        }

        submitter.submit_frame(&frame)?;

        if first {
            self.ready_sent = true;
            self.emit_overlay_ready(bridge).await;
        }

        if need_show {
            submitter.set_overlay_visible(true)?;
            self.overlay_visible = true;
        }

        // Hide-when-idle: once empty, arm the idle-hide timer if not armed.
        if has_text {
            self.idle_hide_pending_since = None;
        } else if self.idle_hide_pending_since.is_none() {
            self.idle_hide_pending_since = Some(Instant::now());
        }

        self.redraw_requested = false;
        Ok(())
    }

    /// 发出 `overlay_ready` EVENT, 使用 runtime 的真实 `overlay_instance_id`
    /// (生产 shell 通过 `set_instance_id` 注入; 单测默认 `"overlay"`)。
    async fn emit_overlay_ready(&mut self, bridge: &mut BridgeClient) {
        let payload = serde_json::json!({
            "type": "overlay_ready",
            "overlay_instance_id": self.overlay_instance_id,
        });
        let _ = bridge.send_json(payload.clone()).await;
        eprintln!("EVENT {}", payload);
    }

    /// Drive the bridge event loop until shutdown, applying snapshots and
    /// maintaining overlay visibility (idle-hide / re-show).
    pub async fn run_event_loop(
        &mut self,
        bridge: &mut BridgeClient,
        renderer: &CaptionRenderer,
        submitter: &mut impl crate::openvr::OverlayFrameSubmitter,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        loop {
            if self.stopped {
                return Ok(());
            }

            let idle_deadline = self
                .idle_hide_pending_since
                .map(|start| start + IDLE_HIDE_DELAY);

            // Check whether the idle deadline has already elapsed without a
            // fresh event — used to decide whether to await the bridge or sleep
            // to the hide deadline.
            let idle_elapsed = idle_deadline
                .map(|deadline| Instant::now() >= deadline)
                .unwrap_or(false);

            let outcome: Result<(), RuntimeFailure> = if idle_elapsed {
                // Idle window expired: hide the (empty, stable) overlay.
                self.perform_idle_hide(submitter)?;
                Ok(())
            } else if let Some(deadline) = idle_deadline {
                let until = deadline.saturating_duration_since(Instant::now());
                tokio::select! {
                    msg = bridge.next_message() => {
                        self.consume_bridge_message(msg).await?;
                        self.drain_redraw(renderer, submitter, bridge, logger).await?;
                        continue;
                    }
                    _ = tokio::time::sleep(until) => continue,
                }
            } else {
                match bridge.next_message().await {
                    Ok(event) => {
                        self.handle_event(event).await?;
                        self.drain_redraw(renderer, submitter, bridge, logger)
                            .await?;
                    }
                    Err(crate::bridge::BridgeError::Disconnected) => {
                        // Treat mid-run disconnect after ready as runtime loss.
                        if self.ready_sent {
                            return Err(RuntimeFailure::RuntimeDisconnected);
                        }
                    }
                    Err(_) => {}
                }
                continue;
            };
            outcome?;
        }
    }

    fn perform_idle_hide(
        &mut self,
        submitter: &mut impl crate::openvr::OverlayFrameSubmitter,
    ) -> Result<(), RuntimeFailure> {
        if self.caption_blocks().is_empty() && self.overlay_visible {
            submitter.set_overlay_visible(false)?;
            self.overlay_visible = false;
        }
        self.idle_hide_pending_since = None;
        Ok(())
    }

    async fn consume_bridge_message(
        &mut self,
        msg: Result<OverlayBridgeEvent, crate::bridge::BridgeError>,
    ) -> Result<(), RuntimeFailure> {
        match msg {
            Ok(event) => self.handle_event(event).await,
            Err(crate::bridge::BridgeError::Disconnected) => {
                if self.ready_sent {
                    Err(RuntimeFailure::RuntimeDisconnected)
                } else {
                    Ok(())
                }
            }
            Err(_) => Ok(()),
        }
    }

    async fn drain_redraw(
        &mut self,
        renderer: &CaptionRenderer,
        submitter: &mut impl crate::openvr::OverlayFrameSubmitter,
        bridge: &mut BridgeClient,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        // 校准变更要落到 OpenVR 变换 (否则浮层停在视野正中)。
        if self.calibration_pending {
            submitter.apply_calibration(self.state.calibration())?;
            self.calibration_pending = false;
        }
        if self.redraw_requested {
            self.submit_frame_if_needed(renderer, submitter, bridge, logger)
                .await?;
        }
        Ok(())
    }

    /// Test helper: mark the runtime as "ready" so bridge-loss is considered a
    /// runtime disconnect (matches startup ordering semantics).
    pub fn mark_ready_for_test(&mut self) {
        self.ready_sent = true;
    }

    /// Test helper: simulate a bridge loss after readiness. Returns
    /// `RuntimeFailure::RuntimeDisconnected` (tests/runtime.rs:413-419).
    pub async fn handle_bridge_loss_for_test(&mut self) -> Result<(), RuntimeFailure> {
        self.handle_bridge_loss()
    }
}

impl PresentationSlot {
    fn from_block(
        block: &crate::state::OverlayPresentationBlock,
        slot_index: usize,
        entry_order: u64,
    ) -> Self {
        Self {
            slot_index,
            slot_entry_order: entry_order,
            occupant_key: block.occupant_key.clone(),
            appearance_seq: block.appearance_seq,
            update_id: block.update_id.clone(),
            session_scope: block.session_scope.clone(),
            id: block.id.clone(),
            primary_text: block.primary_text.clone(),
            secondary_text: block.secondary_text.clone(),
            secondary_enabled: block.secondary_enabled,
            primary_language: block.primary_language.clone(),
            secondary_language: block.secondary_language.clone(),
            channel: channel_from_str(&block.channel),
            block_variant: variant_from_protocol(block.block_variant),
        }
    }

    fn to_caption_block(&self) -> CaptionBlock {
        self.to_caption_block_at_row(self.slot_index)
    }

    /// 视觉行 `row` 的固定锦点 (两槽位行式布局: 行距容得下已译块最大高)。
    fn to_caption_block_at_row(&self, row: usize) -> CaptionBlock {
        let anchor_top_px = FIRST_SLOT_TOP_PX + row as f32 * SLOT_ROW_STRIDE_PX;
        let mut block = CaptionBlock::new(&self.id, &self.primary_text)
            .with_secondary_text(&self.secondary_text, self.secondary_enabled)
            .with_slot(row, anchor_top_px);
        block.block_variant = self.block_variant;
        block.channel = self.channel;
        block.primary_language = self.primary_language.clone();
        block.secondary_language = self.secondary_language.clone();
        block
    }
}

fn channel_from_str(channel: &str) -> Option<CaptionChannel> {
    match channel {
        "self" => Some(CaptionChannel::SelfChannel),
        "peer" => Some(CaptionChannel::PeerChannel),
        _ => None,
    }
}

fn variant_from_protocol(variant: OverlayPresentationBlockVariant) -> CaptionBlockVariant {
    match variant {
        OverlayPresentationBlockVariant::ActiveSelf => CaptionBlockVariant::ActiveSelf,
        OverlayPresentationBlockVariant::ActivePeer => CaptionBlockVariant::ActivePeer,
        OverlayPresentationBlockVariant::Finalized => CaptionBlockVariant::Finalized,
    }
}

/// Return `(visual_changed, redraw_requested)` for updating an existing slot.
/// Slot identity (index, entry order, occupant, update id) is never touched.
fn update_slot_content(
    slot: &mut PresentationSlot,
    block: &crate::state::OverlayPresentationBlock,
) -> (bool, bool) {
    let mut visual = false;
    let mut redraw = false;

    if slot.id != block.id {
        slot.id = block.id.clone();
        visual = true;
    }
    if slot.primary_text != block.primary_text {
        slot.primary_text = block.primary_text.clone();
        visual = true;
    }
    if slot.secondary_text != block.secondary_text {
        slot.secondary_text = block.secondary_text.clone();
        visual = true;
    }
    if slot.secondary_enabled != block.secondary_enabled {
        slot.secondary_enabled = block.secondary_enabled;
        visual = true;
    }
    if slot.primary_language != block.primary_language {
        slot.primary_language = block.primary_language.clone();
        visual = true;
        redraw = true;
    }
    if slot.secondary_language != block.secondary_language {
        slot.secondary_language = block.secondary_language.clone();
        visual = true;
        redraw = true;
    }
    let channel = channel_from_str(&block.channel);
    if slot.channel != channel {
        slot.channel = channel;
        visual = true;
    }
    let variant = variant_from_protocol(block.block_variant);
    if slot.block_variant != variant {
        slot.block_variant = variant;
        visual = true;
    }
    if slot.update_id != block.update_id {
        slot.update_id = block.update_id.clone();
    }
    if slot.session_scope != block.session_scope {
        slot.session_scope = block.session_scope.clone();
        visual = true;
        redraw = true;
    }

    (visual, redraw)
}

// ── Startup / CLI wiring (shell) ─────────────────────────────────────────
//
// These keep the existing startup path compiling. Task 4 wires the new core
// into `run_with_manifest` with the EVENT protocol + HMD retry.

/// Parse CLI args, load manifest, run the main loop. Returns the process exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    // No arguments: show usage and exit 2.
    if args.len() <= 1 {
        eprintln!("usage: RinBridgeOverlay --config <path> [--check-startup-contract]");
        return 2;
    }

    // `--check-startup-contract`: 打印当前 contracts 版本并成功退出(独立于
    // manifest 加载)。stdout 上输出 `{"contract_version": N}`。
    if args.iter().any(|a| a == "--check-startup-contract") {
        println!(
            "{}",
            serde_json::json!({ "contract_version": crate::manifest::EXPECTED_CONTRACT_VERSION })
        );
        return 0;
    }

    let config_path = parse_config_arg(args);
    let manifest = match config_path {
        Some(path) => match manifest::load_manifest(path) {
            Ok(m) => m,
            Err(e) => {
                // 启动失败(manifest 缺失/损坏): 发 EVENT 启动失败行 + exit 1。
                // 无 manifest 可读取实例 id, 统一发 `overlay_instance_id: null`。
                emit_startup_error(None, e.failure_reason(), &e.to_string());
                eprintln!("[overlay][ERROR] manifest load failed: {e}");
                return e.exit_code();
            }
        },
        None => {
            // Phase 1: no --config, run with defaults for smoke test
            default_manifest()
        }
    };

    if let Err(e) = manifest::validate_manifest(&manifest) {
        emit_startup_error(
            Some(&manifest.overlay_instance_id),
            e.failure_reason(),
            &e.to_string(),
        );
        eprintln!("[overlay][ERROR] manifest validation failed: {e}");
        return e.exit_code();
    }

    run_with_manifest(manifest).await
}

/// Main overlay runtime shell entrance — init subsystems, then drive the
/// event-driven core. Returns the process exit code (0 on clean success).
///
/// 顺序: 1) connect+auth 2) OpenVR HMD 预检(带重试) 3) OpenVR overlay +
/// renderer 4) 事件循环。auth 失败/连接失败/HMD 缺失在 connect 之后即可
/// 判出, 避免在无 VR 环境下 preflight 先阻塞而永远到不了 auth 检测。
pub async fn run_with_manifest(manifest: OverlayManifest) -> i32 {
    match run_overlay_inner(&manifest).await {
        Ok(()) => 0,
        Err(e) => {
            emit_runtime_startup_event(&manifest, &e);
            eprintln!("[overlay][ERROR] runtime fatal: {e}");
            e.exit_code()
        }
    }
}

async fn run_overlay_inner(manifest: &OverlayManifest) -> Result<(), StartupError> {
    // 1) Bridge 连接 + auth(放在 HMD 预检之前, 见 test
    //    `run_with_manifest_reports_bridge_auth_failures_as_startup_errors`)。
    //    auth 失败 → `BridgeError::Auth` → `StartupError::BridgeAuth`(exit 12);
    //    其他连接失败 → `BridgeError::Connect` → `StartupError::Bridge`(exit 1)。
    let (mut bridge, initial_snapshot) = match BridgeClient::connect(manifest).await {
        Ok(pair) => pair,
        Err(BridgeError::Auth(reason)) => {
            return Err(StartupError::BridgeAuth(reason));
        }
        Err(e) => {
            return Err(StartupError::Bridge(e.to_string()));
        }
    };

    // 2) OpenVR HMD 预检(带重试)。SteamVR 启动后头显可能才加载完成,
    //    因此在 HMD_RETRY_DEADLINE_MS 内每间隔重试, 超时报 no_hmd。
    match wait_for_hmd_preflight().await {
        Ok(()) => {}
        Err(e) => return Err(e),
    }

    // 3) Init logger
    let logger = OverlayLogger::open(&manifest.log_dir, manifest.logging_mode)
        .await
        .map_err(|e| StartupError::Bridge(e.to_string()))?;

    logger
        .info(format!(
            "[overlay] starting instance={} bridge={}",
            manifest.overlay_instance_id, manifest.bridge_url
        ))
        .await
        .ok();

    // 4) Init OpenVR overlay + renderer
    let overlay = OpenVrOverlay::new(&manifest.overlay_instance_id)?;
    let renderer = CaptionRenderer::new()?;

    let mut runtime = OverlayRuntime::new(initial_snapshot);
    runtime
        .run_with_shell(
            overlay,
            &renderer,
            &mut bridge,
            &logger,
            &manifest.overlay_instance_id,
        )
        .await
}

/// 运行 HMD 预检并在超时前重试。返回 `Ok(())` 表示预检通过;
/// 返回 `StartupError`(exit 20 系列)表示超时仍无 HMD/SteamVR 不可用。
async fn wait_for_hmd_preflight() -> Result<(), StartupError> {
    let deadline = Instant::now() + Duration::from_millis(HMD_RETRY_DEADLINE_MS);
    loop {
        match openvr::perform_startup_preflight() {
            Ok(()) => return Ok(()),
            Err(e) => {
                if Instant::now() >= deadline {
                    return Err(e.into());
                }
                tokio::time::sleep(Duration::from_millis(HMD_RETRY_INTERVAL_MS)).await;
            }
        }
    }
}

/// stderr 上输出一条 `EVENT {json}` 协议行。
fn emit_event_json(payload: &serde_json::Value) {
    eprintln!("EVENT {}", payload);
}

/// 统一输出一条 `startup_error` EVENT 行。`overlay_instance_id` 必填:
/// 有 manifest 时传 `Some(id)`; manifest 加载失败时传 `None`(序列化为 `null`),
/// 保证所有调用点 schema 一致。
fn emit_startup_error(instance_id: Option<&str>, reason: &str, detail: &str) {
    emit_event_json(&serde_json::json!({
        "type": "startup_error",
        "reason": reason,
        "detail": detail,
        "overlay_instance_id": instance_id,
    }));
}

/// 将运行期启动失败映射为对应的 EVENT 协议行：
/// - BridgeAuth   → `auth_failed`
/// - Bridge       → `connect_failed`(连接而非认证失败)
/// - HMD/SteamVR  → `no_hmd`
/// - 其余         → `startup_error`
fn emit_runtime_startup_event(manifest: &OverlayManifest, error: &StartupError) {
    let event_type = match error {
        StartupError::BridgeAuth(_) => "auth_failed",
        StartupError::Bridge(_) => "connect_failed",
        StartupError::SteamVrNotInstalled
        | StartupError::SteamVrNotRunning
        | StartupError::HmdNotFound
        | StartupError::OpenVrInit(_) => "no_hmd",
        _ => "startup_error",
    };
    emit_event_json(&serde_json::json!({
        "type": event_type,
        "reason": error.failure_reason(),
        "detail": error.to_string(),
        "overlay_instance_id": manifest.overlay_instance_id,
    }));
}

impl OverlayRuntime {
    /// Shell loop: drive OpenVR submit + compositor heartbeat through the core.
    async fn run_with_shell(
        &mut self,
        mut overlay: OpenVrOverlay,
        renderer: &CaptionRenderer,
        bridge: &mut BridgeClient,
        logger: &OverlayLogger,
        overlay_instance_id: &str,
    ) -> Result<(), StartupError> {
        // Initial render + ready once with the real instance id. The single
        // ready-emission path lives in `submit_frame_if_needed`, which reads
        // the id from runtime state (set below) — no hardcoded-id trap.
        self.set_instance_id(overlay_instance_id);
        // 启动即应用初始校准 (首个快照到达前位置就正确)。
        overlay
            .apply_calibration(&self.state.calibration())
            .map_err(|e| StartupError::OpenVr(e.to_string()))?;
        let mut submitter = ShellSubmitter(&mut overlay);
        self.submit_frame_if_needed(renderer, &mut submitter, bridge, logger)
            .await
            .map_err(|e| StartupError::OpenVr(e.to_string()))?;

        self.run_event_loop(bridge, renderer, &mut submitter, logger)
            .await
            .map_err(|e| match e {
                RuntimeFailure::OpenVr(_) | RuntimeFailure::Renderer(_) => {
                    StartupError::OpenVr(e.to_string())
                }
                RuntimeFailure::RuntimeDisconnected => StartupError::RuntimeDisconnected,
            })?;

        overlay.compositor_heartbeat();
        Ok(())
    }
}

struct ShellSubmitter<'a>(&'a mut OpenVrOverlay);

impl crate::openvr::OverlayFrameSubmitter for ShellSubmitter<'_> {
    fn submit_frame(
        &mut self,
        frame: &crate::renderer::RenderedFrame,
    ) -> Result<(), crate::openvr::OpenVrError> {
        self.0.submit_frame(frame)
    }

    fn set_overlay_visible(&mut self, visible: bool) -> Result<(), crate::openvr::OpenVrError> {
        self.0.set_overlay_visible(visible)
    }

    fn apply_calibration(
        &mut self,
        calibration: &crate::state::OverlayCalibration,
    ) -> Result<(), crate::openvr::OpenVrError> {
        self.0.apply_calibration(calibration)
    }
}

fn parse_config_arg(args: &[String]) -> Option<&str> {
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg == "--config" {
            return iter.next().map(|s| s.as_str());
        }
    }
    None
}

fn default_manifest() -> OverlayManifest {
    OverlayManifest {
        contract_version: crate::manifest::EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "phase1-default".into(),
        bridge_url: "ws://127.0.0.1:1".into(),
        session_token: String::new(),
        parent_pid: 0,
        startup_deadline_ms: 3000,
        log_dir: std::env::temp_dir()
            .join("rinbridge-overlay-phase1")
            .display()
            .to_string(),
        log_level: "INFO".into(),
        locale: "zh-CN".into(),
        logging_mode: crate::logging::OverlayLoggingMode::Basic,
    }
}

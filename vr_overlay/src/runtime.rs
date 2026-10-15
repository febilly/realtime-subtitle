use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::{Duration, Instant};

use thiserror::Error;

use crate::hud::HudFrame;
use crate::logging::OverlayLogger;
use crate::manifest::{self, OverlayManifest};
use crate::openvr::{self, OpenVrOverlay, OverlayFrameSubmitter};
use crate::projection::{self, LiveRowDirective, Projection};
use crate::protocol::{is_desktop_ws_url, DesktopProtocol};
use crate::renderer::{CaptionRenderError, CaptionRenderer};
use crate::state::OverlayCalibration;
use crate::transcript::{CaptionEvent, LiveInputRow, SpeakerKey, TranscriptState};
use crate::views::VrViewSettings;

#[derive(Debug, Error)]
pub enum StartupError {
    #[error("manifest contract mismatch: {0}")]
    ContractMismatch(String),
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
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::ContractMismatch(_) => 10,
            Self::SteamVrNotInstalled
            | Self::SteamVrNotRunning
            | Self::HmdNotFound
            | Self::OpenVrInit(_) => 20,
            Self::RendererInit(_) => 21,
            Self::Bridge(_) | Self::RuntimeDisconnected | Self::OpenVr(_) | Self::Manifest(_) => 1,
        }
    }

    pub fn failure_reason(&self) -> &'static str {
        match self {
            Self::ContractMismatch(_) => "contract_mismatch",
            Self::SteamVrNotInstalled => "steamvr_not_installed",
            Self::SteamVrNotRunning => "steamvr_not_running",
            Self::HmdNotFound => "hmd_not_found",
            Self::OpenVrInit(_) => "openvr_init_failed",
            Self::RendererInit(_) => "renderer_init_failed",
            Self::Bridge(_) => "bridge_error",
            Self::RuntimeDisconnected => "runtime_disconnected",
            Self::OpenVr(_) => "openvr_error",
            Self::Manifest(_) => "manifest_error",
        }
    }
}

impl From<openvr::OpenVrError> for StartupError {
    fn from(error: openvr::OpenVrError) -> Self {
        Self::OpenVr(error.to_string())
    }
}

impl From<CaptionRenderError> for StartupError {
    fn from(error: CaptionRenderError) -> Self {
        Self::RendererInit(error.to_string())
    }
}

impl From<openvr::OpenVrStartupPreflightError> for StartupError {
    fn from(error: openvr::OpenVrStartupPreflightError) -> Self {
        match error {
            openvr::OpenVrStartupPreflightError::SteamVrNotInstalled => Self::SteamVrNotInstalled,
            openvr::OpenVrStartupPreflightError::SteamVrNotRunning => Self::SteamVrNotRunning,
            openvr::OpenVrStartupPreflightError::HmdNotFound => Self::HmdNotFound,
            openvr::OpenVrStartupPreflightError::Init(message) => Self::OpenVrInit(message),
        }
    }
}

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
    pub fn failure_reason(&self) -> &'static str {
        match self {
            Self::OpenVr(_) => "openvr_error",
            Self::Renderer(_) => "renderer_error",
            Self::RuntimeDisconnected => "runtime_disconnected",
        }
    }
}

impl From<openvr::OpenVrError> for RuntimeFailure {
    fn from(error: openvr::OpenVrError) -> Self {
        Self::OpenVr(error.to_string())
    }
}

impl From<CaptionRenderError> for RuntimeFailure {
    fn from(error: CaptionRenderError) -> Self {
        Self::Renderer(error.to_string())
    }
}

pub trait Clock {
    fn now(&self) -> Instant;
}

pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}

pub trait ParentLiveness {
    fn alive(&self) -> bool;
}

pub struct ProcessLiveness {
    pub parent_pid: u32,
}

impl ParentLiveness for ProcessLiveness {
    fn alive(&self) -> bool {
        if self.parent_pid == 0 {
            return false;
        }

        #[cfg(windows)]
        {
            use windows::Win32::Foundation::CloseHandle;
            use windows::Win32::System::Threading::{
                GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
            };

            let Ok(handle) =
                (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, self.parent_pid) })
            else {
                return false;
            };
            let mut exit_code = 0;
            const STILL_ACTIVE: u32 = 259;
            let alive = unsafe { GetExitCodeProcess(handle, &mut exit_code).is_ok() }
                && exit_code == STILL_ACTIVE;
            let _ = unsafe { CloseHandle(handle) };
            alive
        }

        #[cfg(not(windows))]
        {
            true
        }
    }
}

pub const SILENCE_BEFORE_FADE: Duration = Duration::from_millis(4000);
pub const FADE_DURATION: Duration = Duration::from_millis(1200);
pub const FADE_STEP: Duration = Duration::from_millis(50);
pub const PARENT_POLL_INTERVAL: Duration = Duration::from_millis(2000);

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct VisibilityTick {
    pub set_visible: Option<bool>,
    pub alpha: Option<f32>,
}

pub struct VisibilityController {
    visible: bool,
    last_activity: Instant,
    fade_started: Option<Instant>,
    last_alpha: f32,
    pending_show: bool,
    pending_hide: bool,
    pending_alpha: bool,
}

impl VisibilityController {
    pub fn new(now: Instant) -> Self {
        Self {
            visible: false,
            last_activity: now,
            fade_started: None,
            last_alpha: 0.0,
            pending_show: false,
            pending_hide: false,
            pending_alpha: false,
        }
    }

    pub fn on_activity(&mut self, now: Instant) {
        self.last_activity = now;
        self.fade_started = None;
        if !self.visible {
            self.visible = true;
            self.pending_hide = false;
            self.pending_show = true;
        }
        if self.last_alpha != 1.0 {
            self.last_alpha = 1.0;
            self.pending_alpha = true;
        }
    }

    pub fn tick(&mut self, now: Instant) -> VisibilityTick {
        let mut tick = VisibilityTick::default();
        if self.pending_hide {
            self.pending_hide = false;
            tick.set_visible = Some(false);
        }
        if self.pending_show {
            self.pending_show = false;
            tick.set_visible = Some(true);
        }
        if !self.visible {
            if self.pending_alpha {
                self.pending_alpha = false;
                tick.alpha = Some(self.last_alpha);
            }
            return tick;
        }

        if self.fade_started.is_none()
            && now.saturating_duration_since(self.last_activity) >= SILENCE_BEFORE_FADE
        {
            self.fade_started = Some(self.last_activity + SILENCE_BEFORE_FADE);
        }
        let Some(fade_started) = self.fade_started else {
            if self.pending_alpha {
                self.pending_alpha = false;
                tick.alpha = Some(self.last_alpha);
            }
            return tick;
        };

        let elapsed = now.saturating_duration_since(fade_started);
        if elapsed >= FADE_DURATION {
            self.last_alpha = 0.0;
            self.pending_alpha = false;
            self.fade_started = None;
            self.visible = false;
            tick.alpha = Some(0.0);
            tick.set_visible = Some(false);
            return tick;
        }

        let progress = elapsed.as_secs_f32() / FADE_DURATION.as_secs_f32();
        let alpha = (1.0 - progress).clamp(0.0, 1.0);
        if (alpha - self.last_alpha).abs() > f32::EPSILON {
            self.last_alpha = alpha;
            self.pending_alpha = false;
            tick.alpha = Some(alpha);
        }
        tick
    }

    /// Clear the visibility state while preserving the compositor operations
    /// needed to make an already-visible overlay disappear on the next tick.
    pub fn reset(&mut self, now: Instant) {
        let was_visible = self.visible;
        let had_alpha = self.last_alpha > 0.0;
        self.visible = false;
        self.last_activity = now;
        self.fade_started = None;
        self.pending_show = false;
        self.pending_hide = was_visible;
        self.last_alpha = 0.0;
        self.pending_alpha = had_alpha;
    }

    pub fn next_deadline(&self) -> Option<Instant> {
        self.next_deadline_at(Instant::now())
    }

    pub fn next_deadline_at(&self, now: Instant) -> Option<Instant> {
        if !self.visible {
            return None;
        }
        if let Some(fade_started) = self.fade_started {
            let elapsed = now.saturating_duration_since(fade_started);
            if elapsed >= FADE_DURATION {
                return Some(now);
            }
            let completed_steps = elapsed.as_nanos() / FADE_STEP.as_nanos();
            return Some(fade_started + FADE_STEP * (completed_steps as u32 + 1));
        }
        let silence_deadline = self.last_activity + SILENCE_BEFORE_FADE;
        Some(if silence_deadline <= now {
            now
        } else {
            silence_deadline
        })
    }

    pub fn is_visible(&self) -> bool {
        self.visible
    }
}

/// The sole runtime state machine for the v7 desktop websocket path.
pub struct RuntimeCoordinator {
    transcript: TranscriptState,
    settings: VrViewSettings,
    visibility: VisibilityController,
    now: Instant,
    last_frame: HudFrame,
    submitted_frame: bool,
    submit_count: usize,
    pending_live_dismiss: Option<(SpeakerKey, Option<String>)>,
    ready: bool,
    should_exit: bool,
    test_parent_alive: bool,
    next_parent_poll: Instant,
    startup_deadline: Option<Instant>,
}

impl RuntimeCoordinator {
    pub fn new(now: Instant, settings: VrViewSettings) -> Self {
        let settings = if settings.validate().is_ok() {
            settings
        } else {
            VrViewSettings::default()
        };
        Self {
            transcript: TranscriptState::default(),
            settings,
            visibility: VisibilityController::new(now),
            now,
            last_frame: HudFrame::default(),
            submitted_frame: false,
            submit_count: 0,
            pending_live_dismiss: None,
            ready: false,
            should_exit: false,
            test_parent_alive: true,
            next_parent_poll: now + PARENT_POLL_INTERVAL,
            startup_deadline: None,
        }
    }

    pub fn apply_event(&mut self, event: CaptionEvent, now: Instant) {
        self.now = now;
        let source_activity =
            matches!(&event, CaptionEvent::SourceLive(value) if !value.text.is_empty());
        let potentially_visible_update = matches!(&event, CaptionEvent::SourceCommitted(value) if !value.text.is_empty())
            || matches!(&event, CaptionEvent::TargetDraft(value) if !value.text.is_empty())
            || matches!(&event, CaptionEvent::TargetCommitted(value) if !value.text.is_empty())
            || matches!(&event, CaptionEvent::RefinedTarget(value) if !value.text.is_empty());
        let frame_before = potentially_visible_update.then(|| self.project_readonly(now).frame);
        let clears_visibility = matches!(
            &event,
            CaptionEvent::Clear {
                preserve_existing: false
            }
        );

        if let CaptionEvent::ViewSettingsChanged(settings) = &event {
            if settings.validate().is_ok() {
                self.settings = *settings;
            }
            return;
        }
        if matches!(event, CaptionEvent::Shutdown) {
            self.should_exit = true;
            return;
        }

        self.transcript.apply(&event, now);
        if clears_visibility {
            self.visibility.reset(now);
            return;
        }
        let projection = self.project_readonly(now);
        let visible_frame_changed = frame_before
            .as_ref()
            .is_some_and(|before| before != &projection.frame && frame_has_text(&projection.frame));
        if source_activity || visible_frame_changed {
            self.visibility.on_activity(now);
        }
    }

    pub fn project(&self, now: Instant) -> HudFrame {
        self.project_readonly(now).frame
    }

    fn project_readonly(&self, now: Instant) -> Projection {
        projection::project(&self.transcript, &self.settings, now)
    }

    pub fn render(&mut self, now: Instant) -> bool {
        self.now = now;
        let Some(frame) = self.pending_frame(now) else {
            return false;
        };
        self.commit_frame(frame);
        true
    }

    /// Get a frame that needs a successful render+submit. This method does not
    /// mutate transcript state, so a failed renderer or submit can retry it.
    pub fn pending_frame(&mut self, now: Instant) -> Option<HudFrame> {
        self.now = now;
        let projection = self.project_readonly(now);
        let frame = projection.frame;
        if !self.submitted_frame || !frame.visually_equal(&self.last_frame) {
            self.pending_live_dismiss = match projection.live_row {
                LiveRowDirective::Dismiss {
                    speaker,
                    sentence_id,
                } => Some((speaker, sentence_id)),
                LiveRowDirective::None | LiveRowDirective::Show => None,
            };
            Some(frame)
        } else {
            None
        }
    }

    /// Commit only after both renderer and OpenVR submission have succeeded.
    pub fn commit_frame(&mut self, frame: HudFrame) {
        if let Some((speaker, sentence_id)) = self.pending_live_dismiss.take() {
            self.transcript
                .dismiss_settled_live(&speaker, sentence_id.as_deref());
        }
        self.last_frame = frame;
        self.submitted_frame = true;
        self.submit_count += 1;
    }

    pub fn tick(&mut self, now: Instant) -> VisibilityTick {
        self.now = now;
        self.visibility.tick(now)
    }

    pub fn next_deadline(&self, now: Instant) -> Option<Instant> {
        let live_deadline = match &self.transcript.live_input {
            LiveInputRow::Settled { closed_at, .. } => {
                let deadline = *closed_at + projection::LIVE_SOURCE_HOLD;
                (now < deadline).then_some(deadline)
            }
            _ => None,
        };
        [
            live_deadline,
            self.visibility.next_deadline_at(now),
            Some(self.next_parent_poll),
        ]
        .into_iter()
        .flatten()
        .min()
    }

    pub fn on_parent_poll(&mut self, now: Instant, parent: &impl ParentLiveness) -> bool {
        self.now = now;
        self.next_parent_poll = now + PARENT_POLL_INTERVAL;
        if !parent.alive() {
            self.should_exit = true;
        }
        self.should_exit
    }

    pub fn parent_poll_due(&self, now: Instant) -> bool {
        now >= self.next_parent_poll
    }

    pub fn on_disconnect(&mut self) {
        self.transcript.clear(false);
        self.last_frame = HudFrame::default();
        self.submitted_frame = false;
        self.pending_live_dismiss = None;
        self.ready = false;
        self.visibility = VisibilityController::new(self.now);
    }

    pub fn start(&mut self, now: Instant) -> bool {
        self.now = now;
        self.render(now);
        self.ready = true;
        true
    }

    pub fn set_startup_deadline(&mut self, now: Instant, timeout: Duration) {
        self.startup_deadline = Some(now + timeout);
    }

    pub fn startup_deadline_expired(&self, now: Instant) -> bool {
        self.startup_deadline
            .is_some_and(|deadline| now >= deadline)
    }

    pub fn transcript(&self) -> &TranscriptState {
        &self.transcript
    }

    pub fn settings(&self) -> VrViewSettings {
        self.settings
    }

    pub fn last_frame(&self) -> &HudFrame {
        &self.last_frame
    }

    pub fn submit_count(&self) -> usize {
        self.submit_count
    }

    pub fn ready(&self) -> bool {
        self.ready
    }

    pub fn should_exit(&self) -> bool {
        self.should_exit
    }

    pub fn for_test() -> Self {
        Self::new(Instant::now(), VrViewSettings::default())
    }

    pub fn for_test_with_parent(alive: bool) -> Self {
        let mut coordinator = Self::for_test();
        coordinator.test_parent_alive = alive;
        coordinator
    }

    pub fn now_for_test(&self) -> Instant {
        self.now
    }

    pub fn push_for_test(&mut self, event: CaptionEvent, now: Instant) {
        self.apply_event(event, now);
    }

    pub fn render_for_test(&mut self, now: Instant) {
        self.render(now);
    }

    pub fn drain_and_render_for_test(&mut self, now: Instant) {
        self.render(now);
    }

    pub fn tick_for_test(&mut self, now: Instant) {
        self.tick(now);
    }

    pub fn next_wake_for_test(&self, now: Instant) -> Option<Instant> {
        match &self.transcript.live_input {
            LiveInputRow::Settled { closed_at, .. } => {
                let deadline = *closed_at + projection::LIVE_SOURCE_HOLD;
                (now < deadline).then_some(deadline)
            }
            _ => None,
        }
    }

    pub fn transcript_for_test(&self) -> &TranscriptState {
        &self.transcript
    }

    pub fn last_frame_for_test(&self) -> &HudFrame {
        &self.last_frame
    }

    pub fn submit_count_for_test(&self) -> usize {
        self.submit_count
    }

    pub fn apply_settings_for_test(&mut self, settings: VrViewSettings) {
        self.apply_event(CaptionEvent::ViewSettingsChanged(settings), self.now);
    }

    pub fn settings_for_test(&self) -> VrViewSettings {
        self.settings
    }

    pub fn poll_parent_for_test(&mut self, now: Instant) {
        self.next_parent_poll = now + PARENT_POLL_INTERVAL;
        if !self.test_parent_alive {
            self.should_exit = true;
        }
    }

    pub fn should_exit_for_test(&self) -> bool {
        self.should_exit
    }

    pub fn start_for_test(&mut self, now: Instant) {
        self.start(now);
    }

    pub fn ready_for_test(&self) -> bool {
        self.ready
    }

    pub fn set_deadline_for_test(&mut self, timeout: Duration) {
        self.set_startup_deadline(self.now, timeout);
    }

    pub fn startup_tick_for_test(&self, elapsed: Duration) -> Result<(), ()> {
        if self.startup_deadline_expired(self.now + elapsed) {
            Err(())
        } else {
            Ok(())
        }
    }

    pub fn on_disconnect_for_test(&mut self) {
        self.on_disconnect();
    }
}

fn frame_has_text(frame: &HudFrame) -> bool {
    frame.slots.iter().flatten().any(|row| !row.text.is_empty())
}

const HMD_RETRY_DEADLINE: Duration = Duration::from_secs(10);
const HMD_RETRY_INTERVAL: Duration = Duration::from_secs(1);
const CONNECT_RETRY_INITIAL: Duration = Duration::from_millis(100);
const CONNECT_RETRY_MAX: Duration = Duration::from_secs(1);
const RECONNECT_RETRY_MAX: Duration = Duration::from_secs(5);

pub async fn run_cli(args: &[String]) -> i32 {
    if args.len() <= 1 {
        eprintln!("usage: RinBridgeOverlay --config <path> [--check-startup-contract]");
        return 2;
    }
    if args.iter().any(|arg| arg == "--check-startup-contract") {
        println!(
            "{}",
            serde_json::json!({ "contract_version": manifest::EXPECTED_CONTRACT_VERSION })
        );
        return 0;
    }

    if let Ok(exe) = std::env::current_exe() {
        let (size, mtime) = std::fs::metadata(&exe)
            .ok()
            .map(|meta| {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
                    .map_or(0, |duration| duration.as_secs());
                (meta.len(), mtime)
            })
            .unwrap_or((0, 0));
        eprintln!(
            "[overlay][BUILD] exe={} size={} mtime_unix={} version={} contract={}",
            exe.display(),
            size,
            mtime,
            env!("CARGO_PKG_VERSION"),
            manifest::EXPECTED_CONTRACT_VERSION
        );
    }

    let manifest = match parse_config_arg(args) {
        Some(path) => match manifest::load_manifest(path) {
            Ok(manifest) => manifest,
            Err(error) => {
                emit_startup_error(None, error.failure_reason(), &error.to_string());
                eprintln!("[overlay][ERROR] manifest load failed: {error}");
                return error.exit_code();
            }
        },
        None => {
            let error = StartupError::Manifest("missing --config path".into());
            emit_startup_error(None, error.failure_reason(), &error.to_string());
            return error.exit_code();
        }
    };

    if let Err(error) = manifest::validate_manifest(&manifest) {
        emit_startup_error(
            Some(&manifest.overlay_instance_id),
            error.failure_reason(),
            &error.to_string(),
        );
        eprintln!("[overlay][ERROR] manifest validation failed: {error}");
        return error.exit_code();
    }
    run_with_manifest(manifest).await
}

pub async fn run_with_manifest(manifest: OverlayManifest) -> i32 {
    match run_overlay_inner(&manifest).await {
        Ok(()) => 0,
        Err(error) => {
            emit_runtime_startup_event(&manifest, &error);
            eprintln!("[overlay][ERROR] runtime fatal: {error}");
            error.exit_code()
        }
    }
}

async fn run_overlay_inner(manifest: &OverlayManifest) -> Result<(), StartupError> {
    if !is_desktop_ws_url(&manifest.bridge_url) {
        return Err(StartupError::Bridge(format!(
            "bridge url path must be /ws: {}",
            manifest.bridge_url
        )));
    }

    let parent = ProcessLiveness {
        parent_pid: manifest.parent_pid,
    };
    let startup_deadline =
        Instant::now() + Duration::from_millis(u64::from(manifest.startup_deadline_ms));
    let mut protocol = connect_desktop_until(&parent, manifest, startup_deadline).await?;
    wait_for_hmd_preflight_with_parent(&parent).await?;

    let logger = OverlayLogger::open(&manifest.log_dir, manifest.logging_mode)
        .await
        .map_err(|error| StartupError::Manifest(error.to_string()))?;
    logger
        .info(format!(
            "[overlay] starting instance={} bridge={}",
            manifest.overlay_instance_id, manifest.bridge_url
        ))
        .await
        .ok();

    let mut overlay = OpenVrOverlay::new(&manifest.overlay_instance_id)?;
    overlay.apply_calibration(&manifest.calibration)?;
    let renderer = CaptionRenderer::new()?;
    renderer.set_presentation(crate::renderer::CaptionPresentation {
        text_scale: manifest.calibration.text_scale,
        background_alpha: manifest.calibration.background_alpha,
    });

    let now = Instant::now();
    let mut coordinator = RuntimeCoordinator::new(now, manifest.view_settings);
    let initial = renderer.render_hud_frame(&HudFrame::default())?;
    let mut submitter = ShellSubmitter(&mut overlay);
    submitter.submit_frame(&initial.frame)?;
    submitter.set_overlay_alpha(0.0)?;
    submitter.set_overlay_visible(false)?;
    coordinator.start(now);
    let mut submit_sequence = 1u64;
    logger
        .diagnostic(&[
            ("event_kind", "startup_empty_frame"),
            ("dirty_slots", "0"),
            ("render_ms", "0"),
            ("submit_sequence", "1"),
        ])
        .await
        .ok();
    emit_event_json(&serde_json::json!({
        "type": "overlay_ready",
        "overlay_instance_id": manifest.overlay_instance_id,
    }));

    let mut reconnect_delay = Duration::from_millis(250);
    loop {
        if coordinator.should_exit() {
            break;
        }
        let current = Instant::now();
        if coordinator.parent_poll_due(current) && coordinator.on_parent_poll(current, &parent) {
            break;
        }

        let wake = coordinator
            .next_deadline(current)
            .unwrap_or(current + PARENT_POLL_INTERVAL);
        let message = tokio::select! {
            message = protocol.next_event() => Some(message),
            _ = tokio::time::sleep_until(tokio::time::Instant::from_std(wake)) => None,
        };

        let mut disconnected = false;
        match message {
            Some(Ok(event)) => {
                log_caption_event(&logger, &event).await;
                coordinator.apply_event(event, Instant::now());
                while let Some(next) = protocol.try_next_event().await {
                    match next {
                        Ok(event) => {
                            log_caption_event(&logger, &event).await;
                            coordinator.apply_event(event, Instant::now());
                        }
                        Err(_) => {
                            disconnected = true;
                            break;
                        }
                    }
                    if coordinator.should_exit() {
                        break;
                    }
                }
            }
            Some(Err(_)) => disconnected = true,
            None => {
                let now = Instant::now();
                if coordinator.parent_poll_due(now) && coordinator.on_parent_poll(now, &parent) {
                    break;
                }
            }
        }

        if coordinator.should_exit() {
            break;
        }
        if disconnected {
            coordinator.on_disconnect();
            submitter.set_overlay_alpha(0.0)?;
            submitter.set_overlay_visible(false)?;
            protocol =
                reconnect_desktop_after_disconnect(&parent, manifest, &logger, reconnect_delay)
                    .await?;
            reconnect_delay = Duration::from_millis(250);
        } else {
            flush_desktop_state(
                &mut coordinator,
                &renderer,
                &mut submitter,
                &logger,
                &mut submit_sequence,
            )
            .await?;
        }
    }

    submitter.set_overlay_alpha(0.0)?;
    submitter.set_overlay_visible(false)?;
    overlay.compositor_heartbeat();
    Ok(())
}

async fn connect_desktop_until(
    parent: &impl ParentLiveness,
    manifest: &OverlayManifest,
    deadline: Instant,
) -> Result<DesktopProtocol, StartupError> {
    let mut delay = CONNECT_RETRY_INITIAL;
    loop {
        if !parent.alive() {
            return Err(StartupError::RuntimeDisconnected);
        }
        match DesktopProtocol::connect(manifest).await {
            Ok(protocol) => return Ok(protocol),
            Err(crate::protocol::BridgeError::UnsupportedPath(path)) => {
                return Err(StartupError::Bridge(format!(
                    "bridge url path must be /ws: {path}"
                )))
            }
            Err(error) if Instant::now() >= deadline => {
                return Err(StartupError::Bridge(error.to_string()))
            }
            Err(_) => {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(StartupError::Bridge("startup deadline expired".into()));
                }
                tokio::time::sleep(delay.min(remaining)).await;
                delay = (delay * 2).min(CONNECT_RETRY_MAX);
            }
        }
    }
}

async fn reconnect_desktop_after_disconnect(
    parent: &impl ParentLiveness,
    manifest: &OverlayManifest,
    logger: &OverlayLogger,
    initial_delay: Duration,
) -> Result<DesktopProtocol, StartupError> {
    let mut delay = initial_delay;
    loop {
        if !parent.alive() {
            return Err(StartupError::RuntimeDisconnected);
        }
        tokio::time::sleep(delay).await;
        match DesktopProtocol::connect(manifest).await {
            Ok(protocol) => return Ok(protocol),
            Err(crate::protocol::BridgeError::UnsupportedPath(path)) => {
                return Err(StartupError::Bridge(format!(
                    "bridge url path must be /ws: {path}"
                )))
            }
            Err(error) => {
                let next_delay = (delay * 2).min(RECONNECT_RETRY_MAX);
                logger
                    .info(format!(
                        "[overlay] reconnect failed: {error}; retrying in {}ms",
                        next_delay.as_millis()
                    ))
                    .await
                    .ok();
                delay = next_delay;
            }
        }
    }
}

async fn flush_desktop_state(
    coordinator: &mut RuntimeCoordinator,
    renderer: &CaptionRenderer,
    submitter: &mut impl OverlayFrameSubmitter,
    logger: &OverlayLogger,
    submit_sequence: &mut u64,
) -> Result<(), StartupError> {
    let now = Instant::now();
    let visibility = coordinator.tick(now);
    if let Some(frame) = coordinator.pending_frame(now) {
        let dirty_slots = coordinator
            .last_frame()
            .slots
            .iter()
            .zip(frame.slots.iter())
            .filter(|(old, new)| match (old, new) {
                (Some(old), Some(new)) => !old.visually_equal(new),
                (None, None) => false,
                _ => true,
            })
            .count();
        let render_started = Instant::now();
        let rendered = renderer.render_hud_frame(&frame)?;
        let dirty_slots = dirty_slots.to_string();
        let render_ms = render_started.elapsed().as_millis().to_string();
        let next_sequence = (*submit_sequence + 1).to_string();
        logger
            .diagnostic(&[
                ("event_kind", "render"),
                ("dirty_slots", dirty_slots.as_str()),
                ("render_ms", render_ms.as_str()),
            ])
            .await
            .ok();
        submitter.submit_frame(&rendered.frame)?;
        coordinator.commit_frame(frame);
        *submit_sequence += 1;
        logger
            .diagnostic(&[
                ("event_kind", "submit"),
                ("submit_sequence", &next_sequence),
            ])
            .await
            .ok();
    }
    if let Some(visible) = visibility.set_visible {
        submitter.set_overlay_visible(visible)?;
    }
    if let Some(alpha) = visibility.alpha {
        submitter.set_overlay_alpha(alpha)?;
    }
    Ok(())
}

async fn log_caption_event(logger: &OverlayLogger, event: &CaptionEvent) {
    let kind = match event {
        CaptionEvent::SourceLive(_) => "source_live",
        CaptionEvent::SourceCommitted(_) => "source_committed",
        CaptionEvent::SourceEnd { .. } => "source_end",
        CaptionEvent::TargetDraft(_) => "target_draft",
        CaptionEvent::TargetCommitted(_) => "target_committed",
        CaptionEvent::RefinedTarget(_) => "refined_target",
        CaptionEvent::Clear { .. } => "clear",
        CaptionEvent::ViewSettingsChanged(_) => "view_settings",
        CaptionEvent::Shutdown => "shutdown",
        CaptionEvent::Activity => "activity",
    };
    let speaker_hash = event_speaker(event)
        .map(stable_hash)
        .unwrap_or_else(|| "-".to_string());
    let sentence_ordinal = event_sentence_id(event)
        .map(stable_hash)
        .unwrap_or_else(|| "-".to_string());
    let preview = event_text(event)
        .map(|text| {
            text.chars()
                .take(80)
                .map(|ch| if ch.is_control() { ' ' } else { ch })
                .collect::<String>()
        })
        .unwrap_or_default();
    logger
        .diagnostic(&[
            ("event_kind", kind),
            ("speaker_hash", speaker_hash.as_str()),
            ("sentence_ordinal", sentence_ordinal.as_str()),
            ("preview", preview.as_str()),
        ])
        .await
        .ok();
}

fn stable_hash<T: Hash>(value: T) -> String {
    let mut hasher = DefaultHasher::new();
    value.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

fn event_speaker(event: &CaptionEvent) -> Option<&SpeakerKey> {
    match event {
        CaptionEvent::SourceLive(value) => Some(&value.speaker),
        CaptionEvent::SourceCommitted(value) => Some(&value.speaker),
        CaptionEvent::SourceEnd { speaker, .. } => Some(speaker),
        CaptionEvent::TargetDraft(value) | CaptionEvent::TargetCommitted(value) => {
            Some(&value.speaker)
        }
        CaptionEvent::RefinedTarget(_)
        | CaptionEvent::Clear { .. }
        | CaptionEvent::ViewSettingsChanged(_)
        | CaptionEvent::Shutdown
        | CaptionEvent::Activity => None,
    }
}

fn event_sentence_id(event: &CaptionEvent) -> Option<&str> {
    match event {
        CaptionEvent::SourceLive(value) => value.sentence_id.as_deref(),
        CaptionEvent::SourceCommitted(value) => value.sentence_id.as_deref(),
        CaptionEvent::SourceEnd { sentence_id, .. } => sentence_id.as_deref(),
        CaptionEvent::TargetDraft(value) | CaptionEvent::TargetCommitted(value) => {
            value.sentence_id.as_deref()
        }
        CaptionEvent::RefinedTarget(value) => Some(value.sentence_id.as_str()),
        CaptionEvent::Clear { .. }
        | CaptionEvent::ViewSettingsChanged(_)
        | CaptionEvent::Shutdown
        | CaptionEvent::Activity => None,
    }
}

fn event_text(event: &CaptionEvent) -> Option<&str> {
    match event {
        CaptionEvent::SourceLive(value) => Some(value.text.as_str()),
        CaptionEvent::SourceCommitted(value) => Some(value.text.as_str()),
        CaptionEvent::TargetDraft(value) | CaptionEvent::TargetCommitted(value) => {
            Some(value.text.as_str())
        }
        CaptionEvent::RefinedTarget(value) => Some(value.text.as_str()),
        CaptionEvent::SourceEnd { .. }
        | CaptionEvent::Clear { .. }
        | CaptionEvent::ViewSettingsChanged(_)
        | CaptionEvent::Shutdown
        | CaptionEvent::Activity => None,
    }
}

async fn wait_for_hmd_preflight_with_parent(
    parent: &impl ParentLiveness,
) -> Result<(), StartupError> {
    let deadline = Instant::now() + HMD_RETRY_DEADLINE;
    loop {
        if !parent.alive() {
            return Err(StartupError::RuntimeDisconnected);
        }
        match openvr::perform_startup_preflight() {
            Ok(()) => return Ok(()),
            Err(error) if Instant::now() >= deadline => return Err(error.into()),
            Err(_) => tokio::time::sleep(HMD_RETRY_INTERVAL).await,
        }
    }
}

fn emit_event_json(payload: &serde_json::Value) {
    eprintln!("EVENT {payload}");
}

fn emit_startup_error(instance_id: Option<&str>, reason: &str, detail: &str) {
    emit_event_json(&serde_json::json!({
        "type": "startup_error",
        "reason": reason,
        "detail": detail,
        "overlay_instance_id": instance_id,
    }));
}

fn emit_runtime_startup_event(manifest: &OverlayManifest, error: &StartupError) {
    let event_type = match error {
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

struct ShellSubmitter<'a>(&'a mut OpenVrOverlay);

impl OverlayFrameSubmitter for ShellSubmitter<'_> {
    fn submit_frame(
        &mut self,
        frame: &crate::renderer::RenderedFrame,
    ) -> Result<(), crate::openvr::OpenVrError> {
        self.0.submit_frame(frame)
    }

    fn set_overlay_alpha(&mut self, alpha: f32) -> Result<(), crate::openvr::OpenVrError> {
        self.0.set_overlay_alpha(alpha)
    }

    fn set_overlay_visible(&mut self, visible: bool) -> Result<(), crate::openvr::OpenVrError> {
        self.0.set_overlay_visible(visible)
    }

    fn apply_calibration(
        &mut self,
        calibration: &OverlayCalibration,
    ) -> Result<(), crate::openvr::OpenVrError> {
        self.0.apply_calibration(calibration)
    }
}

fn parse_config_arg(args: &[String]) -> Option<&str> {
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg == "--config" {
            return iter.next().map(String::as_str);
        }
    }
    None
}

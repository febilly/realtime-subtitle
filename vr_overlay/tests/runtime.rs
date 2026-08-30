use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::process::Command;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

use rinbridge_overlay::logging::OverlayLogger;
use rinbridge_overlay::runtime::SnapshotApplyOutcome;
use rinbridge_overlay::{
    run_with_manifest, submit_texture, validate_manifest, BridgeClient, CaptionBlock,
    CaptionChannel, CaptionRenderer, FakeOpenVr, OpenVrError, OverlayBridgeEvent,
    OverlayCalibration, OverlayFrameSubmitter, OverlayLoggingMode, OverlayManifest,
    OverlayPresentationBlock, OverlayPresentationBlockVariant, OverlayPresentationCalibration,
    OverlayPresentationSnapshot, OverlayRuntime, RenderedFrame, RuntimeFailure, StartupError,
    EXPECTED_CONTRACT_VERSION,
};

fn test_manifest() -> OverlayManifest {
    OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "overlay-test".into(),
        bridge_url: "ws://127.0.0.1:1".into(),
        session_token: "expected-token".into(),
        parent_pid: 1,
        startup_deadline_ms: 3000,
        log_dir: std::env::temp_dir()
            .join("rinbridge-overlay-tests")
            .display()
            .to_string(),
        log_level: "INFO".into(),
        locale: "en".into(),
        logging_mode: OverlayLoggingMode::Basic,
    }
}

fn unique_log_dir(name: &str) -> String {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_nanos();
    std::env::temp_dir()
        .join(format!("rinbridge-overlay-tests-{name}-{nonce}"))
        .display()
        .to_string()
}

fn unique_temp_file(name: &str, extension: &str) -> std::path::PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_nanos();
    std::env::temp_dir().join(format!("rinbridge-overlay-{name}-{nonce}.{extension}"))
}

fn overlay_binary() -> &'static str {
    env!("CARGO_BIN_EXE_RinBridgeOverlay")
}

fn parse_event_payloads(stderr: &[u8]) -> Vec<serde_json::Value> {
    String::from_utf8_lossy(stderr)
        .lines()
        .filter_map(|line| line.strip_prefix("EVENT "))
        .filter_map(|payload| serde_json::from_str(payload).ok())
        .collect()
}

fn block(
    id: &str,
    channel: &str,
    primary_text: &str,
    secondary_text: &str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        occupant_key: id.to_string(),
        appearance_seq: 1,
        channel: channel.to_string(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: primary_text.to_string(),
        secondary_text: secondary_text.to_string(),
        secondary_enabled,
        primary_language: None,
        secondary_language: None,
        update_id: None,
        origin_wall_clock_ms: None,
        session_scope: None,
    }
}

fn slot_block(
    id: &str,
    occupant_key: &str,
    appearance_seq: u64,
    channel: &str,
    primary_text: &str,
    secondary_text: &str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        occupant_key: occupant_key.to_string(),
        appearance_seq,
        channel: channel.to_string(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: primary_text.to_string(),
        secondary_text: secondary_text.to_string(),
        secondary_enabled,
        primary_language: None,
        secondary_language: None,
        update_id: None,
        origin_wall_clock_ms: None,
        session_scope: None,
    }
}

fn active_self_block(id: &str, primary_text: &str) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        occupant_key: id.to_string(),
        appearance_seq: 1,
        channel: "self".to_string(),
        block_variant: OverlayPresentationBlockVariant::ActiveSelf,
        primary_text: primary_text.to_string(),
        secondary_text: String::new(),
        secondary_enabled: true,
        primary_language: None,
        secondary_language: None,
        update_id: None,
        origin_wall_clock_ms: None,
        session_scope: None,
    }
}

static SCRIPTED_BRIDGE_TEST_LOCK: OnceLock<tokio::sync::Mutex<()>> = OnceLock::new();

enum BridgeAction {
    WaitMs(u64),
    SendSnapshot(serde_json::Value),
    SendShutdown,
}

async fn run_overlay_binary_with_scripted_bridge(
    name: &str,
    initial_snapshot: serde_json::Value,
    actions: Vec<BridgeAction>,
) -> std::process::Output {
    let _guard = SCRIPTED_BRIDGE_TEST_LOCK
        .get_or_init(|| tokio::sync::Mutex::new(()))
        .lock()
        .await;
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _auth = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": initial_snapshot,
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        while let Some(message) = ws.next().await {
            let Ok(Message::Text(text)) = message else {
                continue;
            };
            let payload: serde_json::Value = serde_json::from_str(&text).unwrap();
            if payload["type"] == "overlay_ready" {
                break;
            }
        }

        for action in actions {
            match action {
                BridgeAction::WaitMs(delay_ms) => {
                    tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                }
                BridgeAction::SendSnapshot(snapshot) => {
                    if ws
                        .send(Message::Text(
                            json!({
                                "type": "snapshot",
                                "payload": snapshot,
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .is_err()
                    {
                        return;
                    }
                }
                BridgeAction::SendShutdown => {
                    if ws
                        .send(Message::Text(
                            json!({"type": "shutdown"}).to_string().into(),
                        ))
                        .await
                        .is_err()
                    {
                        return;
                    }
                    tokio::time::sleep(Duration::from_millis(50)).await;
                }
            }
        }
    });

    let manifest_path = unique_temp_file(name, "json");
    let manifest = OverlayManifest {
        bridge_url: format!("ws://{}", address),
        logging_mode: OverlayLoggingMode::Detailed,
        ..test_manifest()
    };
    std::fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();

    let manifest_path_for_process = manifest_path.clone();
    let output = tokio::time::timeout(Duration::from_secs(10), async move {
        tokio::task::spawn_blocking(move || {
            Command::new(overlay_binary())
                .arg("--config")
                .arg(&manifest_path_for_process)
                .output()
                .unwrap()
        })
        .await
        .unwrap()
    })
    .await
    .unwrap();

    server.await.unwrap();
    let _ = std::fs::remove_file(manifest_path);
    output
}

#[derive(Default)]
struct RecordingSubmitter {
    calls: usize,
    fail: bool,
    operations: Vec<&'static str>,
    visibility_changes: Vec<bool>,
    last_visible: Option<bool>,
    applied_calibrations: Vec<f32>,
}

impl RecordingSubmitter {
    fn failing() -> Self {
        Self {
            calls: 0,
            fail: true,
            operations: Vec::new(),
            visibility_changes: Vec::new(),
            last_visible: None,
            applied_calibrations: Vec::new(),
        }
    }
}

impl OverlayFrameSubmitter for RecordingSubmitter {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        self.calls += 1;
        let operation = if frame.layout().visible_blocks.is_empty() {
            "submit:empty"
        } else {
            "submit:text"
        };
        self.operations.push(operation);
        if self.fail {
            return Err(OpenVrError::Submit("submit failed".into()));
        }
        assert_eq!(frame.width(), 4096);
        assert_eq!(frame.height(), 1056);
        Ok(())
    }

    fn set_overlay_visible(&mut self, visible: bool) -> Result<(), OpenVrError> {
        self.operations.push(if visible { "show" } else { "hide" });
        self.last_visible = Some(visible);
        self.visibility_changes.push(visible);
        Ok(())
    }

    fn apply_calibration(&mut self, calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        self.applied_calibrations.push(calibration.offset_y);
        Ok(())
    }
}

async fn connect_test_bridge() -> (
    BridgeClient,
    tokio::task::JoinHandle<Vec<serde_json::Value>>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");
        assert_eq!(auth_payload["session_token"], "expected-token");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let mut messages = Vec::new();
        while let Some(message) = ws.next().await {
            let Ok(Message::Text(text)) = message else {
                break;
            };
            messages.push(serde_json::from_str(&text).unwrap());
        }

        messages
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    assert!(snapshot.blocks.is_empty());
    (client, server)
}

async fn test_logger(name: &str) -> OverlayLogger {
    OverlayLogger::open(unique_log_dir(name), OverlayLoggingMode::Detailed)
        .await
        .unwrap()
}

#[test]
fn runtime_accepts_app_version_mismatch_when_contract_version_matches() {
    let manifest = OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: "0.0.1-test".into(),
        ..test_manifest()
    };

    let result = validate_manifest(&manifest);

    assert!(result.is_ok());
}

#[test]
fn runtime_expected_contract_version_includes_language_metadata_boundary() {
    assert_eq!(EXPECTED_CONTRACT_VERSION, 6);
}

#[test]
fn runtime_returns_standardized_startup_failure_codes_before_ready() {
    assert_eq!(StartupError::ContractMismatch("bad".into()).exit_code(), 10);
    assert_eq!(StartupError::BridgeAuth("bad token".into()).exit_code(), 12);
    assert_eq!(StartupError::SteamVrNotInstalled.exit_code(), 20);
    assert_eq!(StartupError::SteamVrNotRunning.exit_code(), 20);
    assert_eq!(StartupError::HmdNotFound.exit_code(), 20);
    assert_eq!(
        StartupError::OpenVrInit("steamvr missing".into()).exit_code(),
        20
    );
    assert_eq!(
        StartupError::RendererInit("d3d init failed".into()).exit_code(),
        21
    );
}

#[test]
fn runtime_exposes_specific_preflight_failure_reasons() {
    assert_eq!(
        StartupError::SteamVrNotInstalled.failure_reason(),
        "steamvr_not_installed"
    );
    assert_eq!(
        StartupError::SteamVrNotRunning.failure_reason(),
        "steamvr_not_running"
    );
    assert_eq!(StartupError::HmdNotFound.failure_reason(), "hmd_not_found");
}

#[tokio::test]
async fn runtime_stops_cleanly_on_shutdown_event() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());

    runtime
        .handle_event(OverlayBridgeEvent::Shutdown)
        .await
        .unwrap();

    assert!(runtime.is_stopped());
}

#[tokio::test]
async fn runtime_reports_bridge_loss_as_runtime_disconnect_after_ready() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    runtime.mark_ready_for_test();

    let err = runtime.handle_bridge_loss_for_test().await.unwrap_err();

    assert_eq!(err.failure_reason(), "runtime_disconnected");
}

#[tokio::test]
async fn runtime_applies_new_snapshot_calibration_to_state() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration {
            anchor: "head_locked".into(),
            offset_x: 0.15,
            offset_y: -0.2,
            distance: 1.2,
            text_scale: 1.1,
            background_alpha: 0.4,
        },
        blocks: vec![],
    });

    assert_eq!(runtime.state().calibration().distance, 1.2);
    assert_eq!(runtime.state().calibration().background_alpha, 0.4);
}

#[tokio::test]
async fn runtime_applies_snapshot_calibration_to_submitter() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();
        let _ = ws.next().await; // auth
                                 // 首条 = 初始快照 (connect 消费, 默认校准)
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": OverlayPresentationSnapshot {
                    revision: 0,
                    calibration: OverlayPresentationCalibration::default(),
                    blocks: vec![],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
        // 第二条: 校准变化 → 循环内 apply_snapshot → drain_redraw 落 submitter
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": OverlayPresentationSnapshot {
                    revision: 1,
                    calibration: OverlayPresentationCalibration {
                        offset_y: -0.55,
                        ..Default::default()
                    },
                    blocks: vec![],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);
    let (mut bridge, _initial) = BridgeClient::connect(&manifest).await.unwrap();

    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::default();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("calibration-apply").await;

    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    // 快照的校准必须落到 submitter (OpenVR 变换), 不能只改 state。
    assert_eq!(submitter.applied_calibrations, vec![-0.55]);
    server.await.unwrap();
}

#[tokio::test]
async fn runtime_emits_overlay_ready_only_after_first_texture_submit() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-success").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::default();

    assert!(!runtime.ready_sent());

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    drop(bridge);
    let messages = server.await.unwrap();

    assert_eq!(submitter.calls, 1);
    assert!(runtime.ready_sent());
    assert!(messages
        .iter()
        .any(|message| message["type"] == "overlay_ready"));
}

#[tokio::test]
async fn bridge_client_close_sends_close_frame() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        while let Some(message) = ws.next().await {
            match message.unwrap() {
                Message::Close(_) => return true,
                Message::Text(_) | Message::Binary(_) | Message::Ping(_) | Message::Pong(_) => {}
                Message::Frame(_) => {}
            }
        }

        false
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    assert!(snapshot.blocks.is_empty());

    client.close().await.unwrap();

    assert!(server.await.unwrap());
}

#[tokio::test]
async fn runtime_caption_blocks_keep_channel_metadata_for_color_only_rendering() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "안녕", true),
            block("peer:2", "peer", "세상", "world", false),
        ],
    });

    let blocks = runtime.caption_blocks();
    let channels = blocks
        .iter()
        .map(|block| {
            (
                block.id.as_str(),
                (
                    block.channel,
                    block.primary_text.as_str(),
                    block.secondary_enabled,
                ),
            )
        })
        .collect::<std::collections::BTreeMap<_, _>>();

    assert_eq!(
        channels.get("self:1"),
        Some(&(Some(CaptionChannel::SelfChannel), "hello", true))
    );
    assert_eq!(
        channels.get("peer:2"),
        Some(&(Some(CaptionChannel::PeerChannel), "세상", false))
    );
}

#[tokio::test]
async fn runtime_caption_blocks_carry_primary_and_secondary_languages_from_slots() {
    let mut localized = block("peer:localized", "peer", "日本語", "번역", true);
    localized.primary_language = Some("ja".into());
    localized.secondary_language = Some("ko".into());
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 4,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![localized],
    });

    let blocks = runtime.caption_blocks();

    assert_eq!(blocks[0].primary_language.as_deref(), Some("ja"));
    assert_eq!(blocks[0].secondary_language.as_deref(), Some("ko"));
}

#[test]
fn runtime_language_only_snapshot_redraws_without_slot_identity_reset() {
    let mut initial = slot_block(
        "self:language",
        "self:language",
        7,
        "self",
        "こんにちは",
        "",
        true,
    );
    initial.primary_language = Some("ko".into());
    initial.update_id = Some("update-stays".into());
    let mut updated = initial.clone();
    updated.primary_language = Some("ja".into());
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![initial],
    });
    runtime.clear_redraw_flag();
    let original_slot = runtime.state().scene().slots()[0]
        .as_ref()
        .expect("initial slot should exist")
        .clone();

    let outcome = runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![updated],
    });

    assert!(matches!(
        outcome,
        SnapshotApplyOutcome::Applied {
            visual_changed: true,
            redraw_requested: true,
            ..
        }
    ));
    let slot = runtime.state().scene().slots()[0]
        .as_ref()
        .expect("updated slot should stay assigned");
    assert_eq!(slot.slot_index, original_slot.slot_index);
    assert_eq!(slot.slot_entry_order, original_slot.slot_entry_order);
    assert_eq!(slot.occupant_key, original_slot.occupant_key);
    assert_eq!(slot.appearance_seq, original_slot.appearance_seq);
    assert_eq!(slot.update_id, original_slot.update_id);
    assert_eq!(slot.primary_language.as_deref(), Some("ja"));
    assert_eq!(
        runtime.caption_blocks()[0].primary_language.as_deref(),
        Some("ja")
    );
}

#[test]
fn runtime_seeds_initial_snapshot_with_static_block_visual_state() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block("self:1", "self:1", 1, "self", "hello", "", true)],
    });

    let blocks = runtime.caption_blocks();
    assert_eq!(blocks.len(), 1);
    assert_eq!(blocks[0].offset_y_px, 0.0);
    assert_eq!(blocks[0].height_scale, 1.0);
}

#[test]
fn runtime_new_snapshot_keeps_blocks_static_after_seeded_start() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block("self:1", "self:1", 1, "self", "hello", "", true)],
    });

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    });

    let peer_block = runtime
        .caption_blocks()
        .into_iter()
        .find(|block| block.id == "peer:2")
        .expect("new peer block should render");
    assert_eq!(peer_block.offset_y_px, 0.0);
    assert_eq!(peer_block.height_scale, 1.0);
}

#[test]
fn runtime_keeps_slot_two_top_fixed_when_slot_one_secondary_changes() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "one", "", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    });
    let first = runtime.caption_blocks();

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "one", "번역", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    });

    let second = runtime.caption_blocks();
    assert_eq!(first[1].slot_top_px, second[1].slot_top_px);
}

#[test]
fn runtime_clears_missing_snapshot_blocks_immediately() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block("self:1", "self:1", 1, "self", "hello", "", true)],
    });

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    });

    assert!(runtime.caption_blocks().is_empty());
}

#[test]
fn runtime_keeps_active_self_and_finalized_rows_visible_within_two_slot_cap() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("peer:2", "peer:2", 2, "peer", "second", "", true),
            OverlayPresentationBlock {
                id: "self:active".into(),
                occupant_key: "self:merge-1".into(),
                appearance_seq: 3,
                channel: "self".into(),
                block_variant: OverlayPresentationBlockVariant::ActiveSelf,
                primary_text: "speaking".into(),
                secondary_text: String::new(),
                secondary_enabled: true,
                primary_language: None,
                secondary_language: None,
                update_id: None,
                origin_wall_clock_ms: None,
                session_scope: None,
            },
        ],
    });

    assert_eq!(
        runtime
            .caption_blocks()
            .iter()
            .map(|block| block.id.as_str())
            .collect::<Vec<_>>(),
        vec!["peer:2", "self:active"]
    );
}

#[test]
fn runtime_keeps_fixed_slot_visual_state_when_secondary_slot_changes() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "", false),
            slot_block("peer:2", "peer:2", 2, "peer", "second", "", false),
        ],
    });

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "translated", true),
            slot_block("peer:2", "peer:2", 2, "peer", "second", "", false),
        ],
    });

    let second = runtime
        .caption_blocks()
        .into_iter()
        .find(|block| block.id == "peer:2")
        .expect("peer block should remain visible");
    let first = runtime
        .caption_blocks()
        .into_iter()
        .find(|block| block.id == "self:1")
        .expect("self block should remain visible");

    assert_eq!(second.offset_y_px, 0.0);
    assert_eq!(first.height_scale, 1.0);
}

#[test]
fn runtime_renderer_uses_fixed_slot_bounds_when_secondary_slot_changes() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "", false),
            slot_block("peer:2", "peer:2", 2, "peer", "second", "", false),
        ],
    });
    let initial = renderer.render_blocks(runtime.caption_blocks()).unwrap();

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "translated", true),
            slot_block("peer:2", "peer:2", 2, "peer", "second", "", false),
        ],
    });

    let updated = renderer.render_blocks(runtime.caption_blocks()).unwrap();
    let initial_peer = initial
        .layout()
        .visible_blocks
        .iter()
        .find(|block| block.id == "peer:2")
        .expect("initial peer block should render");
    let updated_self = updated
        .layout()
        .visible_blocks
        .iter()
        .find(|block| block.id == "self:1")
        .expect("updated self block should render");
    let updated_peer = updated
        .layout()
        .visible_blocks
        .iter()
        .find(|block| block.id == "peer:2")
        .expect("updated peer block should render");

    assert_eq!(
        updated_self.bounds.top_px,
        initial.layout().visible_blocks[0].bounds.top_px
    );
    assert_eq!(updated_peer.bounds.top_px, initial_peer.bounds.top_px);
}

#[cfg(windows)]
#[test]
fn runtime_active_self_frames_do_not_hit_finalized_block_cache() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![active_self_block("self:active", "speaking now")],
    });

    renderer.render_blocks(runtime.caption_blocks()).unwrap();
    let second = renderer.render_blocks(runtime.caption_blocks()).unwrap();

    assert_eq!(second.diagnostics().block_cache_hits, 0);
    assert!(second.diagnostics().line_cache_hits >= 1);
}

#[test]
fn runtime_does_not_render_duplicate_row_when_same_id_reappears_during_exit() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    });

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    });
    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello again", "", true)],
    });

    assert_eq!(
        runtime
            .caption_blocks()
            .iter()
            .map(|block| block.id.as_str())
            .collect::<Vec<_>>(),
        vec!["self:1"]
    );
}

#[tokio::test]
async fn runtime_does_not_emit_overlay_ready_when_first_texture_submit_fails() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-failure").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::failing();

    let err = runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap_err();

    drop(bridge);
    let messages = server.await.unwrap();

    assert_eq!(submitter.calls, 1);
    assert!(matches!(err, RuntimeFailure::OpenVr(_)));
    assert!(!runtime.ready_sent());
    assert!(!messages
        .iter()
        .any(|message| message["type"] == "overlay_ready"));
}

#[tokio::test]
async fn runtime_submits_same_peer_refresh_target_when_session_scope_nonce_changes() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("peer-refresh-nonce-submit").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    let peer_refresh_1 = OverlayPresentationBlock {
        id: "peer:turn-1".into(),
        occupant_key: "peer:turn-1".into(),
        appearance_seq: 1,
        channel: "peer".into(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: "translated peer line".into(),
        secondary_text: "source peer line".into(),
        secondary_enabled: true,
        primary_language: None,
        secondary_language: None,
        update_id: Some("peer-update-1".into()),
        origin_wall_clock_ms: None,
        session_scope: Some("peer_presentation_refresh=1".into()),
    };
    let peer_refresh_2 = OverlayPresentationBlock {
        session_scope: Some("peer_presentation_refresh=2".into()),
        ..peer_refresh_1.clone()
    };

    let first_outcome = runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![peer_refresh_1],
    });
    assert!(matches!(
        first_outcome,
        rinbridge_overlay::runtime::SnapshotApplyOutcome::Applied {
            visual_changed: true,
            redraw_requested: true,
            ..
        }
    ));
    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    let second_outcome = runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![peer_refresh_2],
    });
    assert!(matches!(
        second_outcome,
        rinbridge_overlay::runtime::SnapshotApplyOutcome::Applied {
            visual_changed: true,
            redraw_requested: true,
            ..
        }
    ));
    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    drop(bridge);
    let _messages = server.await.unwrap();

    assert_eq!(submitter.calls, 3);
    assert_eq!(
        submitter.operations,
        vec!["submit:empty", "submit:text", "show", "submit:text"]
    );
}

#[tokio::test]
async fn runtime_hides_overlay_after_empty_state_stays_idle_past_delay() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(submitter.visibility_changes.contains(&false));
}

#[tokio::test]
async fn runtime_cancels_pending_idle_hide_when_new_text_arrives() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(250)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "back again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide-cancel").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(!submitter.visibility_changes.contains(&false));
}

#[tokio::test]
async fn runtime_shows_overlay_again_when_text_returns_after_idle_hide() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "visible again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(50)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide-restore").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(submitter
        .visibility_changes
        .windows(2)
        .any(|pair| pair == [false, true]));
}

#[tokio::test]
async fn runtime_submits_text_frame_before_revealing_overlay_after_idle_hide() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "visible again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(50)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("reveal-order-after-hide").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    let hide_index = submitter
        .operations
        .iter()
        .rposition(|operation| *operation == "hide")
        .expect("expected idle hide before reveal");
    assert_eq!(
        &submitter.operations[hide_index + 1..hide_index + 3],
        &["submit:text", "show"]
    );
}

#[tokio::test]
async fn bridge_client_authenticates_and_receives_initial_snapshot() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");
        assert_eq!(auth_payload["session_token"], "expected-token");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (_client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();

    server.await.unwrap();
    assert!(snapshot.blocks.is_empty());
}

#[tokio::test]
async fn bridge_client_receives_runtime_logging_mode_updates() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
        ws.send(Message::Text(
            json!({
                "type": "runtime_control",
                "payload": {"logging_mode": "detailed"},
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    assert!(snapshot.blocks.is_empty());

    let message = client.next_message().await.unwrap();

    assert!(matches!(
        message,
        rinbridge_overlay::OverlayBridgeEvent::Control(control)
            if control.logging_mode == OverlayLoggingMode::Detailed
    ));
    server.await.unwrap();
}

#[tokio::test]
#[ignore = "child-process timing race under parallel cargo; covered by src/runtime.rs unit tests"]
async fn runtime_emits_snapshot_slot_correlation_and_overlay_visible_update_rendered_logs() {
    let output = run_overlay_binary_with_scripted_bridge(
        "slot-correlation-visible-update-rendered",
        json!({
            "revision": 1,
            "calibration": OverlayPresentationCalibration::default(),
            "blocks": [
                {
                    "id": "self:1",
                    "occupant_key": "self:1",
                    "appearance_seq": 1,
                    "channel": "self",
                    "block_variant": "finalized",
                    "primary_text": "hello",
                    "secondary_text": "",
                    "secondary_enabled": true,
                    "update_id": "upd-self-1",
                    "origin_wall_clock_ms": 1712345678901u64,
                    "session_scope": "session:self"
                }
            ]
        }),
        vec![
            BridgeAction::SendSnapshot(json!({
                "revision": 2,
                "calibration": OverlayPresentationCalibration::default(),
                "blocks": [
                    {
                        "id": "self:1",
                        "occupant_key": "self:1",
                        "appearance_seq": 1,
                        "channel": "self",
                        "block_variant": "finalized",
                        "primary_text": "hello again",
                        "secondary_text": "translated",
                        "secondary_enabled": true,
                        "update_id": "upd-self-2",
                        "origin_wall_clock_ms": 1712345678955u64,
                        "session_scope": "session:self"
                    }
                ]
            })),
            BridgeAction::WaitMs(200),
            BridgeAction::SendShutdown,
        ],
    )
    .await;

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("snapshot_slot_correlation"));
    assert!(stdout.contains("update_ids=[upd-self-2]"));
    assert!(stdout.contains("session_scope=session:self"));
    assert!(stdout.contains("presenter_order=0"));
    assert!(stdout.contains("slot_index=0"));
    assert!(stdout.contains("overlay_visible_update_applied"));
    assert!(stdout.contains("overlay_visible_update_rendered"));
}

#[tokio::test]
#[ignore = "child-process timing race under parallel cargo; covered by src/runtime.rs unit tests"]
async fn runtime_emits_two_row_window_closed_log_when_visible_window_collapses() {
    let output = run_overlay_binary_with_scripted_bridge(
        "two-row-window-closed",
        json!({
            "revision": 1,
            "calibration": OverlayPresentationCalibration::default(),
            "blocks": [
                {
                    "id": "self:1",
                    "occupant_key": "self:1",
                    "appearance_seq": 1,
                    "channel": "self",
                    "block_variant": "finalized",
                    "primary_text": "one",
                    "secondary_text": "",
                    "secondary_enabled": true,
                    "update_id": "upd-self-1",
                    "origin_wall_clock_ms": 1712345678901u64,
                    "session_scope": "session:self"
                },
                {
                    "id": "peer:2",
                    "occupant_key": "peer:2",
                    "appearance_seq": 2,
                    "channel": "peer",
                    "block_variant": "finalized",
                    "primary_text": "two",
                    "secondary_text": "",
                    "secondary_enabled": true,
                    "update_id": "upd-peer-2",
                    "origin_wall_clock_ms": 1712345678910u64,
                    "session_scope": "session:peer"
                }
            ]
        }),
        vec![
            BridgeAction::WaitMs(120),
            BridgeAction::SendSnapshot(json!({
                "revision": 2,
                "calibration": OverlayPresentationCalibration::default(),
                "blocks": [
                    {
                        "id": "self:1",
                        "occupant_key": "self:1",
                        "appearance_seq": 1,
                        "channel": "self",
                        "block_variant": "finalized",
                        "primary_text": "one",
                        "secondary_text": "",
                        "secondary_enabled": true,
                        "update_id": "upd-self-1",
                        "origin_wall_clock_ms": 1712345678901u64,
                        "session_scope": "session:self"
                    }
                ]
            })),
            BridgeAction::WaitMs(200),
            BridgeAction::SendShutdown,
        ],
    )
    .await;

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("two_row_window_closed"));
    assert!(stdout.contains("threshold_ms=500"));
    assert!(stdout.contains("too_brief_to_be_perceptibly_stable=true"));
}

#[test]
fn runtime_disconnect_failure_reason_is_stable() {
    assert_eq!(
        RuntimeFailure::RuntimeDisconnected.failure_reason(),
        "runtime_disconnected"
    );
}

#[test]
fn openvr_submission_uses_set_overlay_texture_for_rendered_frames() {
    let openvr = FakeOpenVr::default();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let frame = renderer
        .render_blocks(vec![CaptionBlock::new("peer-1", "hello")])
        .unwrap();

    submit_texture(&openvr, &frame).unwrap();

    assert_eq!(openvr.last_call().as_deref(), Some("SetOverlayTexture"));
}

#[test]
fn check_startup_contract_reports_current_contract_version() {
    let output = Command::new(overlay_binary())
        .arg("--check-startup-contract")
        .output()
        .unwrap();

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["contract_version"], EXPECTED_CONTRACT_VERSION);
}

#[test]
fn validate_manifest_rejects_contract_version_mismatch() {
    let manifest = OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION + 1,
        ..test_manifest()
    };

    let error = validate_manifest(&manifest).unwrap_err();

    assert!(matches!(error, StartupError::ContractMismatch(_)));
}

#[tokio::test]
async fn run_with_manifest_reports_bridge_auth_failures_as_startup_errors() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();
        let _ = ws.next().await;
        ws.send(Message::Text(
            json!({"type": "auth_error"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let log_dir = unique_log_dir("bridge-auth-failure");
    let exit_code = run_with_manifest(OverlayManifest {
        bridge_url: format!("ws://{}", address),
        log_dir,
        ..test_manifest()
    })
    .await;

    server.await.unwrap();
    assert_eq!(exit_code, StartupError::BridgeAuth("x".into()).exit_code());
}

#[test]
fn cli_requires_config_argument_or_supported_flags() {
    let output = Command::new(overlay_binary()).output().unwrap();

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("usage:"));
}

#[test]
fn cli_emits_startup_failure_event_when_manifest_is_missing() {
    let missing_path = unique_temp_file("missing-manifest", "json");
    let output = Command::new(overlay_binary())
        .arg("--config")
        .arg(&missing_path)
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stderr_events = parse_event_payloads(&output.stderr);
    assert!(stderr_events
        .iter()
        .any(|event| event["type"] == "startup_error"));
}

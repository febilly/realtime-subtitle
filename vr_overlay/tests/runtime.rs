use rinbridge_overlay::runtime::{RuntimeFailure, StartupError};
use rinbridge_overlay::{
    run_cli, run_with_manifest, validate_manifest, FakeOpenVr, OverlayCalibration,
    OverlayFrameSubmitter, OverlayLoggingMode, OverlayManifest, VrViewSettings,
    EXPECTED_CONTRACT_VERSION,
};

fn test_manifest() -> OverlayManifest {
    OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "overlay-test".into(),
        bridge_url: "ws://127.0.0.1:1/ws".into(),
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
    fake.set_overlay_alpha(f32::NAN).unwrap();
    assert_eq!(fake.last_alpha(), Some(0.0));
}

#[test]
fn v7_manifest_is_valid_and_uses_the_single_ws_path() {
    let manifest = test_manifest();
    assert!(validate_manifest(&manifest).is_ok());
    assert!(manifest.bridge_url.ends_with("/ws"));
}

#[test]
fn app_version_is_not_a_protocol_gate() {
    let manifest = OverlayManifest {
        app_version: "older-desktop".into(),
        ..test_manifest()
    };
    assert!(validate_manifest(&manifest).is_ok());
}

#[test]
fn startup_errors_have_stable_exit_codes_and_reasons() {
    assert_eq!(StartupError::ContractMismatch("bad".into()).exit_code(), 10);
    assert_eq!(
        StartupError::ContractMismatch("bad".into()).failure_reason(),
        "contract_mismatch"
    );
    assert_eq!(StartupError::SteamVrNotInstalled.exit_code(), 20);
    assert_eq!(
        StartupError::SteamVrNotRunning.failure_reason(),
        "steamvr_not_running"
    );
    assert_eq!(StartupError::HmdNotFound.failure_reason(), "hmd_not_found");
    assert_eq!(StartupError::RendererInit("d3d".into()).exit_code(), 21);
    assert_eq!(
        StartupError::Bridge("offline".into()).failure_reason(),
        "bridge_error"
    );
}

#[test]
fn runtime_failures_have_machine_readable_reasons() {
    assert_eq!(
        RuntimeFailure::OpenVr("x".into()).failure_reason(),
        "openvr_error"
    );
    assert_eq!(
        RuntimeFailure::Renderer("x".into()).failure_reason(),
        "renderer_error"
    );
    assert_eq!(
        RuntimeFailure::RuntimeDisconnected.failure_reason(),
        "runtime_disconnected"
    );
}

#[tokio::test]
async fn removed_snapshot_path_is_rejected_before_connecting() {
    let manifest = OverlayManifest {
        bridge_url: "ws://127.0.0.1:1/vr_ws".into(),
        ..test_manifest()
    };
    assert_eq!(run_with_manifest(manifest).await, 1);
}

#[tokio::test]
async fn cli_requires_a_config_path() {
    let args = vec!["RinBridgeOverlay".into()];
    assert_eq!(run_cli(&args).await, 2);
}

#[tokio::test]
async fn cli_contract_probe_does_not_require_a_manifest_or_vr() {
    let args = vec!["RinBridgeOverlay".into(), "--check-startup-contract".into()];
    assert_eq!(run_cli(&args).await, 0);
}

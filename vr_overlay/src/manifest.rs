use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::logging::OverlayLoggingMode;
use crate::runtime::StartupError;
use crate::state::OverlayCalibration;
use crate::views::VrViewSettings;

pub const EXPECTED_CONTRACT_VERSION: u32 = 7;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct OverlayManifest {
    pub contract_version: u32,
    pub app_version: String,
    pub overlay_instance_id: String,
    pub bridge_url: String,
    pub parent_pid: u32,
    pub startup_deadline_ms: u32,
    pub log_dir: String,
    pub log_level: String,
    pub locale: String,
    pub logging_mode: OverlayLoggingMode,
    pub view_settings: VrViewSettings,
    pub calibration: OverlayCalibration,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
struct OverlayManifestSerde {
    contract_version: u32,
    app_version: String,
    overlay_instance_id: String,
    bridge_url: String,
    parent_pid: u32,
    startup_deadline_ms: u32,
    log_dir: String,
    log_level: String,
    locale: String,
    #[serde(default)]
    logging_mode: Option<OverlayLoggingMode>,
    #[serde(default)]
    diagnostics_enabled: Option<bool>,
    #[serde(default)]
    view_settings: VrViewSettings,
    #[serde(default)]
    calibration: OverlayCalibration,
}

impl TryFrom<OverlayManifestSerde> for OverlayManifest {
    type Error = StartupError;

    fn try_from(raw: OverlayManifestSerde) -> Result<Self, Self::Error> {
        let logging_mode = match (raw.logging_mode, raw.diagnostics_enabled) {
            (Some(mode), _) => mode,
            (None, Some(true)) => OverlayLoggingMode::Detailed,
            (None, Some(false)) => OverlayLoggingMode::Basic,
            (None, None) => {
                return Err(StartupError::Manifest(
                    "missing field `logging_mode`".to_string(),
                ))
            }
        };

        Ok(Self {
            contract_version: raw.contract_version,
            app_version: raw.app_version,
            overlay_instance_id: raw.overlay_instance_id,
            bridge_url: raw.bridge_url,
            parent_pid: raw.parent_pid,
            startup_deadline_ms: raw.startup_deadline_ms,
            log_dir: raw.log_dir,
            log_level: raw.log_level,
            locale: raw.locale,
            logging_mode,
            view_settings: raw.view_settings,
            calibration: raw.calibration,
        })
    }
}

pub fn load_manifest(path: impl AsRef<Path>) -> Result<OverlayManifest, StartupError> {
    let content =
        std::fs::read_to_string(path).map_err(|error| StartupError::Manifest(error.to_string()))?;
    let manifest: OverlayManifestSerde = serde_json::from_str(&content)
        .map_err(|error| StartupError::Manifest(error.to_string()))?;
    manifest.try_into()
}

pub fn validate_manifest(manifest: &OverlayManifest) -> Result<(), StartupError> {
    if manifest.contract_version != EXPECTED_CONTRACT_VERSION {
        return Err(StartupError::ContractMismatch(format!(
            "expected contract_version={} but received {}",
            EXPECTED_CONTRACT_VERSION, manifest.contract_version
        )));
    }
    Ok(())
}

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
        assert_eq!(m.view_settings, VrViewSettings::default());
        assert_eq!(m.calibration, OverlayCalibration::default());
        assert!(validate_manifest(&m).is_ok());
    }

    #[test]
    fn contract_version_six_is_rejected() {
        let raw = raw_v7().replace("\"contract_version\":7", "\"contract_version\":6");
        let m: OverlayManifest = serde_json::from_str::<OverlayManifestSerde>(&raw)
            .unwrap()
            .try_into()
            .unwrap();
        assert!(matches!(
            validate_manifest(&m),
            Err(StartupError::ContractMismatch(_))
        ));
    }

    #[test]
    fn session_token_in_the_envelope_is_rejected() {
        let mut value: serde_json::Value = serde_json::from_str(&raw_v7()).unwrap();
        value["session_token"] = serde_json::json!("secret");
        assert!(serde_json::from_value::<OverlayManifestSerde>(value).is_err());
    }
}

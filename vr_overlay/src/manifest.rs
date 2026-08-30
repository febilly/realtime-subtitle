use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::logging::OverlayLoggingMode;
use crate::runtime::StartupError;

pub const EXPECTED_CONTRACT_VERSION: u32 = 6;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OverlayManifest {
    pub contract_version: u32,
    pub app_version: String,
    pub overlay_instance_id: String,
    pub bridge_url: String,
    pub session_token: String,
    pub parent_pid: u32,
    pub startup_deadline_ms: u32,
    pub log_dir: String,
    pub log_level: String,
    pub locale: String,
    pub logging_mode: OverlayLoggingMode,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
struct OverlayManifestSerde {
    contract_version: u32,
    app_version: String,
    overlay_instance_id: String,
    bridge_url: String,
    session_token: String,
    parent_pid: u32,
    startup_deadline_ms: u32,
    log_dir: String,
    log_level: String,
    locale: String,
    #[serde(default)]
    logging_mode: Option<OverlayLoggingMode>,
    #[serde(default)]
    diagnostics_enabled: Option<bool>,
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
            session_token: raw.session_token,
            parent_pid: raw.parent_pid,
            startup_deadline_ms: raw.startup_deadline_ms,
            log_dir: raw.log_dir,
            log_level: raw.log_level,
            locale: raw.locale,
            logging_mode,
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

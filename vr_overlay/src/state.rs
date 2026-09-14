use serde::{Deserialize, Serialize};

/// Position and display calibration shared by the manifest and OpenVR.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayCalibration {
    #[serde(default = "default_anchor")]
    pub anchor: String,
    #[serde(default)]
    pub offset_x: f32,
    #[serde(default = "default_offset_y")]
    pub offset_y: f32,
    #[serde(default = "default_distance")]
    pub distance: f32,
    #[serde(default = "default_text_scale")]
    pub text_scale: f32,
    #[serde(default = "default_background_alpha")]
    pub background_alpha: f32,
}

impl Default for OverlayCalibration {
    fn default() -> Self {
        Self {
            anchor: default_anchor(),
            offset_x: 0.0,
            offset_y: default_offset_y(),
            distance: default_distance(),
            text_scale: default_text_scale(),
            background_alpha: default_background_alpha(),
        }
    }
}

fn default_anchor() -> String {
    "head_locked".into()
}

fn default_offset_y() -> f32 {
    -0.45
}

fn default_distance() -> f32 {
    1.1
}

fn default_text_scale() -> f32 {
    1.0
}

fn default_background_alpha() -> f32 {
    0.24
}

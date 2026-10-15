use serde::{Deserialize, Serialize};
use thiserror::Error;

/// The three product display projections, mirroring the desktop connection
/// panel's `both` / `original` / `translation` modes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DisplayMode {
    Both,
    Original,
    Translation,
}

/// Desktop-owned view preferences. The desktop connection panel is the sole
/// owner; Rin applies a complete, validated object atomically.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VrViewSettings {
    pub display_mode: DisplayMode,
    pub max_speakers: u8,
    pub bilingual_pair_count: u8,
    pub show_speaker_labels: bool,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
pub enum SettingsError {
    #[error("max_speakers must be 1..=3, got {0}")]
    MaxSpeakers(u8),
    #[error("bilingual_pair_count must be 1..=2, got {0}")]
    BilingualPairCount(u8),
}

impl VrViewSettings {
    pub fn validate(&self) -> Result<(), SettingsError> {
        if !(1..=3).contains(&self.max_speakers) {
            return Err(SettingsError::MaxSpeakers(self.max_speakers));
        }
        if !(1..=2).contains(&self.bilingual_pair_count) {
            return Err(SettingsError::BilingualPairCount(self.bilingual_pair_count));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
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
            VrViewSettings {
                max_speakers: 0,
                ..Default::default()
            },
            VrViewSettings {
                max_speakers: 4,
                ..Default::default()
            },
            VrViewSettings {
                bilingual_pair_count: 0,
                ..Default::default()
            },
            VrViewSettings {
                bilingual_pair_count: 3,
                ..Default::default()
            },
        ] {
            assert!(s.validate().is_err(), "{s:?} must be rejected");
        }
        assert!(VrViewSettings {
            max_speakers: 3,
            bilingual_pair_count: 2,
            ..Default::default()
        }
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
}

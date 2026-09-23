use crate::transcript::SentenceKey;

/// The renderer reserves exactly five physical slots. Slot 4 is always the
/// universal live source row.
pub const HUD_SLOT_COUNT: usize = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HudRowRole {
    UpperPrimary,
    UpperSecondary,
    LiveSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HudRowKind {
    Draft,
    Settled,
}

/// One physical HUD row. `text` is the body only; the speaker label is a
/// separate field and is composed solely by the renderer.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct HudRow {
    pub role: HudRowRole,
    pub kind: HudRowKind,
    pub text: String,
    pub speaker_label: Option<String>,
    pub language: Option<String>,
    pub sentence: Option<SentenceKey>,
}

impl HudRow {
    /// Compare only fields that affect the rendered row. `sentence` is
    /// correlation metadata for projection and is intentionally not visual.
    pub fn visually_equal(&self, other: &Self) -> bool {
        self.role == other.role
            && self.kind == other.kind
            && self.text == other.text
            && self.speaker_label == other.speaker_label
            && self.language == other.language
    }
}

/// Five fixed physical slots. The projector never emits a dense window that
/// would move the live row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HudFrame {
    pub slots: [Option<HudRow>; HUD_SLOT_COUNT],
}

impl HudFrame {
    /// Compare the complete rendered HUD while ignoring per-row correlation
    /// metadata that is not consumed by the renderer.
    pub fn visually_equal(&self, other: &Self) -> bool {
        self.slots
            .iter()
            .zip(other.slots.iter())
            .all(|(left, right)| match (left, right) {
                (Some(left), Some(right)) => left.visually_equal(right),
                (None, None) => true,
                _ => false,
            })
    }
}

impl Default for HudFrame {
    fn default() -> Self {
        Self {
            slots: std::array::from_fn(|_| None),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hud_frame_default_has_five_empty_slots() {
        assert_eq!(HudFrame::default().slots.len(), HUD_SLOT_COUNT);
        assert!(HudFrame::default().slots.iter().all(Option::is_none));
    }
}

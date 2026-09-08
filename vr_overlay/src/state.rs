use serde::{Deserialize, Serialize};

/// Position/display calibration. Shared between overlay placement and protocol.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OverlayCalibration {
    #[serde(default)]
    pub anchor: String,
    #[serde(default)]
    pub offset_x: f32,
    #[serde(default)]
    pub offset_y: f32,
    #[serde(default)]
    pub distance: f32,
    #[serde(default)]
    pub text_scale: f32,
    #[serde(default)]
    pub background_alpha: f32,
}

// ── Protocol types ──────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OverlayPresentationBlockVariant {
    ActiveSelf,
    #[serde(rename = "active_peer")]
    ActivePeer,
    Finalized,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayPresentationBlock {
    pub id: String,
    pub occupant_key: String,
    #[serde(default)]
    pub appearance_seq: u64,
    pub channel: String,
    pub block_variant: OverlayPresentationBlockVariant,
    pub primary_text: String,
    #[serde(default)]
    pub secondary_text: String,
    #[serde(default)]
    pub secondary_enabled: bool,
    #[serde(default)]
    pub primary_language: Option<String>,
    #[serde(default)]
    pub secondary_language: Option<String>,
    #[serde(default)]
    pub update_id: Option<String>,
    #[serde(default)]
    pub origin_wall_clock_ms: Option<u64>,
    #[serde(default)]
    pub session_scope: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OverlayPresentationCalibration {
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

impl OverlayPresentationCalibration {
    pub fn to_overlay_calibration(&self) -> OverlayCalibration {
        OverlayCalibration {
            anchor: self.anchor.clone(),
            offset_x: self.offset_x,
            offset_y: self.offset_y,
            distance: self.distance,
            text_scale: self.text_scale,
            background_alpha: self.background_alpha,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OverlayPresentationSnapshot {
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub calibration: OverlayPresentationCalibration,
    #[serde(default)]
    pub blocks: Vec<OverlayPresentationBlock>,
}

// ── Runtime presentation state (pure core, OpenVR-free) ─────────────────

/// A single assigned presentation slot. Holds the occupant identity that is
/// preserved across snapshots (so a slot's vertical position stays fixed while
/// only its text/secondary content changes), plus its current caption content.
#[derive(Debug, Clone, PartialEq)]
pub struct PresentationSlot {
    /// Fixed 0-based slot index within the scene's two-slot window.
    pub slot_index: usize,
    /// Incrementing entry order used to break ties for new occupants.
    pub slot_entry_order: u64,
    /// Occupant identity (stable across updates for the same speaker line).
    pub occupant_key: String,
    /// Sequence number of the occupant's appearance epoch.
    pub appearance_seq: u64,
    pub update_id: Option<String>,
    pub session_scope: Option<String>,
    /// Current caption content for this slot.
    pub id: String,
    pub primary_text: String,
    pub secondary_text: String,
    pub secondary_enabled: bool,
    pub primary_language: Option<String>,
    pub secondary_language: Option<String>,
    pub channel: Option<crate::renderer::CaptionChannel>,
    pub block_variant: crate::renderer::CaptionBlockVariant,
}

/// The two-slot presentation scene currently driving rendering.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PresentationScene {
    slots: Vec<Option<PresentationSlot>>,
}

impl PresentationScene {
    /// Up to two occupied slots (index 0 = primary row, 1 = secondary row).
    pub fn slots(&self) -> &[Option<PresentationSlot>] {
        &self.slots
    }

    /// Iterate occupied slots in ascending slot order.
    pub fn occupied(&self) -> impl Iterator<Item = &PresentationSlot> {
        self.slots.iter().flatten()
    }

    pub(crate) fn set_slot(&mut self, index: usize, slot: Option<PresentationSlot>) {
        if index >= self.slots.len() {
            self.slots.resize(index + 1, None);
        }
        self.slots[index] = slot;
    }
}

/// Pure runtime presentation state (the `state()` view of `OverlayRuntime`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RuntimeState {
    calibration: OverlayCalibration,
    scene: PresentationScene,
}

impl RuntimeState {
    pub fn calibration(&self) -> &OverlayCalibration {
        &self.calibration
    }

    pub fn scene(&self) -> &PresentationScene {
        &self.scene
    }

    pub(crate) fn set_calibration(&mut self, calibration: OverlayCalibration) {
        self.calibration = calibration;
    }

    pub(crate) fn maybe_replace_scene(&mut self, scene: PresentationScene) -> bool {
        if self.scene != scene {
            self.scene = scene;
            true
        } else {
            false
        }
    }
}

// ── Renderer/state-facing presentation state (OpenVR-free) ─────────────────
//
// The public `OverlayState` drives both the renderer layout step
// (`tests/renderer.rs`) and the protocol/state correlation tests
// (`tests/state.rs`). It applies presentation snapshots into a bounded
// two-row scene, keeps each row pinned to a stable vertical anchor, and can
// replay its current view as a protocol snapshot.

/// Maximum number of concurrently visible slots.
pub const VISIBLE_SLOT_CAP: usize = 2;
/// Vertical stride between consecutive assigned slots (secondary-reserved row
/// height plus block spacing at the default text scale).
pub(crate) const SLOT_ROW_STRIDE_PX: f32 = 504.0;
/// Default top position for the first slot.
pub(crate) const FIRST_SLOT_TOP_PX: f32 = 40.0;

/// A single assigned presentation row, surfaces both the renderer's layout-view
/// (channel/variant/anchor) and the protocol/occupancy correlation metadata.
#[derive(Debug, Clone, PartialEq)]
pub struct OverlayStateSlot {
    pub slot_index: usize,
    /// Incrementing assignment order used to break ties for new occupants.
    pub slot_entry_order: u64,
    /// Stable occupant identity that pins a row across snapshots.
    pub occupant_key: String,
    pub appearance_seq: u64,
    pub update_id: Option<String>,
    pub origin_wall_clock_ms: Option<u64>,
    pub session_scope: Option<String>,
    pub id: String,
    pub primary_text: String,
    pub secondary_text: String,
    pub secondary_enabled: bool,
    pub primary_language: Option<String>,
    pub secondary_language: Option<String>,
    /// Protocol channel name (`"self"` / `"peer"`), as shown to the layout step.
    pub channel: String,
    pub block_variant: OverlayPresentationBlockVariant,
    /// Pinned vertical position (px) the layout engine uses as this row's top.
    pub anchor_top_px: f32,
}

/// The renderer-facing scene: a two-slot window of assigned rows.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayStateScene {
    slots: Vec<Option<OverlayStateSlot>>,
}

impl OverlayStateScene {
    /// Up to `VISIBLE_SLOT_CAP` occupied slots (index 0 = top row).
    pub fn slots(&self) -> &[Option<OverlayStateSlot>] {
        &self.slots
    }
}

/// Public presentation state, seeded by applying `OverlayPresentationSnapshot`s.
#[derive(Debug, Clone, PartialEq)]
pub struct OverlayState {
    revision: Option<u64>,
    calibration: OverlayCalibration,
    scene: OverlayStateScene,
    entry_counter: u64,
}

impl Default for OverlayState {
    fn default() -> Self {
        Self {
            revision: None,
            calibration: default_calibration(),
            scene: OverlayStateScene::default(),
            entry_counter: 0,
        }
    }
}

fn default_calibration() -> OverlayCalibration {
    OverlayCalibration {
        anchor: "head_locked".into(),
        offset_x: 0.0,
        offset_y: -0.45,
        distance: 1.1,
        text_scale: 1.0,
        background_alpha: 0.24,
    }
}

impl OverlayState {
    /// Apply a presentation snapshot, assigning incoming blocks into the
    /// two-slot window. Returns `true` if anything changed (scene or
    /// calibration). Snapshots with a revision not above the latest applied are
    /// treated as a no-op.
    pub fn apply_snapshot(&mut self, incoming: &OverlayPresentationSnapshot) -> bool {
        if let Some(latest) = self.revision {
            if incoming.revision <= latest {
                return false;
            }
        }

        let mut visual_changed = false;

        // Calibration.
        let new_cal = incoming.calibration.to_overlay_calibration();
        if self.calibration != new_cal {
            self.calibration = new_cal;
            visual_changed = true;
        }

        // Scene / slot assignment.
        let blocks: Vec<_> = incoming.blocks.iter().collect();
        let mut scene = OverlayStateScene::default();
        let mut used_incoming = vec![false; blocks.len()];

        // Write-through: keep a slot's row (index, entry order, anchor) when the
        // same occupant+epoch re-appears, updating only its content.
        for slot_index in 0..self.scene.slots().len() {
            let Some(previous) = self.scene.slots().get(slot_index).and_then(Option::as_ref) else {
                continue;
            };
            if let Some(found) = blocks.iter().position(|b| {
                b.occupant_key == previous.occupant_key
                    && b.appearance_seq == previous.appearance_seq
            }) {
                let block = blocks[found];
                let mut updated = previous.clone();
                updated.update_content(block);
                scene.set_slot(slot_index, Some(updated));
                used_incoming[found] = true;
                visual_changed = true;
            } else {
                // Occupant no longer present — free the row.
                scene.set_slot(slot_index, None);
            }
        }

        // Assign new occupants to the first free slot; if the window is full,
        // replace the oldest entry (lowest entry order).
        for (i, block) in blocks.iter().enumerate() {
            if used_incoming[i] {
                continue;
            }
            let free_index = (0..VISIBLE_SLOT_CAP)
                .find(|idx| scene.slots().get(*idx).and_then(Option::as_ref).is_none());
            let slot_index = match free_index {
                Some(idx) => idx,
                None => {
                    let mut oldest: Option<(usize, u64)> = None;
                    for (idx, slot) in scene.slots().iter().enumerate() {
                        if let Some(slot) = slot {
                            if oldest.map_or(true, |(_, order)| slot.slot_entry_order < order) {
                                oldest = Some((idx, slot.slot_entry_order));
                            }
                        }
                    }
                    match oldest {
                        Some((idx, _)) => idx,
                        None => scene.slots().len().min(VISIBLE_SLOT_CAP),
                    }
                }
            };
            let anchor_top_px = FIRST_SLOT_TOP_PX + slot_index as f32 * SLOT_ROW_STRIDE_PX;
            let slot =
                OverlayStateSlot::from_block(block, slot_index, self.entry_counter, anchor_top_px);
            self.entry_counter += 1;
            scene.set_slot(slot_index, Some(slot));
            visual_changed = true;
        }

        if scene != self.scene {
            self.scene = scene;
            visual_changed = true;
        }

        self.revision = Some(incoming.revision);
        visual_changed
    }

    /// Replay the current view as a protocol snapshot (revision + calibration +
    /// ordered blocks), preserving correlation metadata.
    pub fn snapshot(&self) -> OverlayPresentationSnapshot {
        OverlayPresentationSnapshot {
            revision: self.revision.unwrap_or_default(),
            calibration: OverlayPresentationCalibration {
                anchor: self.calibration.anchor.clone(),
                offset_x: self.calibration.offset_x,
                offset_y: self.calibration.offset_y,
                distance: self.calibration.distance,
                text_scale: self.calibration.text_scale,
                background_alpha: self.calibration.background_alpha,
            },
            blocks: self.blocks(),
        }
    }

    /// Occupied blocks in ascending slot order, as protocol presentation blocks.
    pub fn blocks(&self) -> Vec<OverlayPresentationBlock> {
        self.scene
            .slots()
            .iter()
            .flatten()
            .map(ToPresentationBlock::to_block)
            .collect()
    }

    /// Current calibration (position/display settings).
    pub fn calibration(&self) -> &OverlayCalibration {
        &self.calibration
    }

    /// The two-row scene of assigned slots.
    pub fn scene(&self) -> &OverlayStateScene {
        &self.scene
    }
}

impl OverlayStateSlot {
    fn from_block(
        block: &OverlayPresentationBlock,
        slot_index: usize,
        entry_order: u64,
        anchor_top_px: f32,
    ) -> Self {
        Self {
            slot_index,
            slot_entry_order: entry_order,
            occupant_key: block.occupant_key.clone(),
            appearance_seq: block.appearance_seq,
            update_id: block.update_id.clone(),
            origin_wall_clock_ms: block.origin_wall_clock_ms,
            session_scope: block.session_scope.clone(),
            id: block.id.clone(),
            primary_text: block.primary_text.clone(),
            secondary_text: block.secondary_text.clone(),
            secondary_enabled: block.secondary_enabled,
            primary_language: block.primary_language.clone(),
            secondary_language: block.secondary_language.clone(),
            channel: block.channel.clone(),
            block_variant: block.block_variant,
            anchor_top_px,
        }
    }

    /// Update renderer/protocol-visible content without touching the identity
    /// (slot index, entry order, occupant, anchor) that pins the row in place.
    fn update_content(&mut self, block: &OverlayPresentationBlock) {
        self.id = block.id.clone();
        self.primary_text = block.primary_text.clone();
        self.secondary_text = block.secondary_text.clone();
        self.secondary_enabled = block.secondary_enabled;
        self.primary_language = block.primary_language.clone();
        self.secondary_language = block.secondary_language.clone();
        self.channel = block.channel.clone();
        self.block_variant = block.block_variant;
        self.update_id = block.update_id.clone();
        self.origin_wall_clock_ms = block.origin_wall_clock_ms;
        self.session_scope = block.session_scope.clone();
    }
}

impl OverlayStateScene {
    fn set_slot(&mut self, index: usize, slot: Option<OverlayStateSlot>) {
        if index >= self.slots.len() {
            self.slots.resize(index + 1, None);
        }
        self.slots[index] = slot;
    }
}

/// Maps an occupied slot back to its protocol presentation block.
trait ToPresentationBlock {
    fn to_block(&self) -> OverlayPresentationBlock;
}

impl ToPresentationBlock for OverlayStateSlot {
    fn to_block(&self) -> OverlayPresentationBlock {
        OverlayPresentationBlock {
            id: self.id.clone(),
            occupant_key: self.occupant_key.clone(),
            appearance_seq: self.appearance_seq,
            channel: self.channel.clone(),
            block_variant: self.block_variant,
            primary_text: self.primary_text.clone(),
            secondary_text: self.secondary_text.clone(),
            secondary_enabled: self.secondary_enabled,
            primary_language: self.primary_language.clone(),
            secondary_language: self.secondary_language.clone(),
            update_id: self.update_id.clone(),
            origin_wall_clock_ms: self.origin_wall_clock_ms,
            session_scope: self.session_scope.clone(),
        }
    }
}

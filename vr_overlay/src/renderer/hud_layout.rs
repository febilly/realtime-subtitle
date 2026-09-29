//! Fixed-slot HUD layout: builds a `ResolvedFrameLayout` directly from the
//! shared `hud::HudFrame` without going through `CaptionBlock` / `render_blocks`.
//!
//! Geometry is keyed by displayed text (renderer-composed speaker label
//! included), role, language, font size, content width and text scale. The
//! `HudRowKind` (draft/settled) is deliberately excluded from the geometry key:
//! it only selects the draw-time fill color, so a draft-to-settled upgrade
//! reuses geometry but changes the rendered color.
//!
#![allow(dead_code)]

use std::cell::RefCell;
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use crate::hud::{HudFrame, HudRowRole, HUD_SLOT_COUNT};

use super::font_resolver::{FontResolver, TextStyleKey};
use super::layout::{measure_text_width, style_descriptor_for_text};
use super::text_fit::{fit_row_text_with_measure, Truncation};
use super::types::{
    BlockBounds, CaptionBlockVariant, CaptionRenderError, LayoutCacheKey, LineRole,
    ResolvedBlockLayout, ResolvedFrameLayout, ResolvedLineLayout, TextStyleDescriptor,
    VisualBounds, DEFAULT_FONT_SIZE_PX, DEFAULT_SURFACE_HEIGHT_PX, SECONDARY_FONT_SCALE,
    TEXT_OUTLINE_OVERHANG_PX,
};

pub const HUD_FIRST_SLOT_TOP_PX: f32 = 40.0;
pub const HUD_SLOT_STRIDE_PX: f32 = 200.0;
pub const HUD_TEXT_LEFT_PX: f32 = 48.0;

pub fn hud_slot_top_px(slot: usize) -> f32 {
    HUD_FIRST_SLOT_TOP_PX + slot as f32 * HUD_SLOT_STRIDE_PX
}

pub fn hud_content_width_px(surface_width_px: u32) -> f32 {
    (surface_width_px as f32 - HUD_TEXT_LEFT_PX * 2.0).max(1.0)
}

/// Geometry identity for one occupied slot. Excludes `HudRowKind` but includes
/// the resolved `TextStyleKey`, so a different font/text identity can never
/// reuse another line's measured geometry.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct HudGeometryKey {
    pub slot: usize,
    pub display_text: String,
    pub role: HudRowRole,
    pub language: Option<String>,
    pub style_key: TextStyleKey,
    pub font_size_key: u32,
    pub content_width_key: u32,
    pub text_scale_key: u32,
}

#[derive(Debug, Clone)]
struct MeasuredHudLine {
    style_key: TextStyleKey,
    style: TextStyleDescriptor,
    width_px: f32,
    font_size_px: f32,
}

#[derive(Debug, Default)]
pub struct HudLayoutCache {
    entries: HashMap<HudGeometryKey, MeasuredHudLine>,
}

impl HudLayoutCache {
    pub fn new() -> Self {
        Self::default()
    }
}

pub struct BuiltHudLayout {
    pub layout: ResolvedFrameLayout,
    pub geometry_keys: [Option<HudGeometryKey>; HUD_SLOT_COUNT],
    pub visual_signature: u64,
    pub geometry_reused: bool,
}

#[derive(Debug, Clone)]
pub(crate) struct HudTextMeasurement {
    pub style: TextStyleDescriptor,
    pub width_px: f32,
}

pub(crate) trait HudTextMeasurer {
    fn measure(
        &self,
        language: Option<&str>,
        text: &str,
        font_size_px: f32,
        max_width_px: f32,
    ) -> Result<HudTextMeasurement, CaptionRenderError>;
}

struct HeuristicHudTextMeasurer<'a> {
    resolver: &'a FontResolver,
    measure: &'a dyn Fn(&str, &TextStyleDescriptor, f32) -> f32,
}

impl HudTextMeasurer for HeuristicHudTextMeasurer<'_> {
    fn measure(
        &self,
        language: Option<&str>,
        text: &str,
        font_size_px: f32,
        _max_width_px: f32,
    ) -> Result<HudTextMeasurement, CaptionRenderError> {
        let style = style_descriptor_for_text(self.resolver, language, text);
        Ok(HudTextMeasurement {
            width_px: (self.measure)(text, &style, font_size_px),
            style,
        })
    }
}

pub(crate) fn build_hud_layout(
    frame: &HudFrame,
    text_scale: f32,
    surface_width_px: u32,
    resolver: &FontResolver,
    cache: &mut HudLayoutCache,
    measure: &dyn Fn(&str, &TextStyleDescriptor, f32) -> f32,
) -> BuiltHudLayout {
    let measurer = HeuristicHudTextMeasurer { resolver, measure };
    build_hud_layout_with_measurer(frame, text_scale, surface_width_px, cache, &measurer)
        .expect("heuristic HUD measurement cannot fail")
}

pub(crate) fn build_hud_layout_with_measurer(
    frame: &HudFrame,
    text_scale: f32,
    surface_width_px: u32,
    cache: &mut HudLayoutCache,
    measurer: &dyn HudTextMeasurer,
) -> Result<BuiltHudLayout, CaptionRenderError> {
    let text_scale = text_scale.max(0.1);
    let content_width_px = hud_content_width_px(surface_width_px);
    let mut blocks = Vec::new();
    let mut geometry_keys: [Option<HudGeometryKey>; HUD_SLOT_COUNT] = Default::default();
    let mut hasher = DefaultHasher::new();
    let mut occupied = 0usize;
    let mut reused = 0usize;

    for (slot, row) in frame.slots.iter().enumerate() {
        let Some(row) = row else {
            continue;
        };
        occupied += 1;
        let raw_display_text = compose_display_text(row);
        let role_scale = match row.role {
            HudRowRole::UpperPrimary => 1.0,
            HudRowRole::UpperSecondary | HudRowRole::LiveSource => SECONDARY_FONT_SCALE,
        };
        let font_size_px = DEFAULT_FONT_SIZE_PX * text_scale * role_scale;
        let raw_measurement = measurer.measure(
            row.language.as_deref(),
            &raw_display_text,
            font_size_px,
            content_width_px,
        )?;
        let truncation = if row.role == HudRowRole::LiveSource {
            Truncation::Leading
        } else {
            Truncation::Trailing
        };
        let needs_truncation = raw_measurement.width_px > content_width_px;
        let display_text = if !needs_truncation {
            raw_display_text.clone()
        } else {
            let measurement_error = RefCell::new(None);
            let fitted = fit_row_text_with_measure(
                &raw_display_text,
                content_width_px,
                truncation,
                &|candidate| match measurer.measure(
                    row.language.as_deref(),
                    candidate,
                    font_size_px,
                    content_width_px,
                ) {
                    Ok(measurement) => measurement.width_px,
                    Err(error) => {
                        measurement_error.replace(Some(error));
                        f32::INFINITY
                    }
                },
            );
            if let Some(error) = measurement_error.into_inner() {
                return Err(error);
            }
            fitted
        };
        let measured = if !needs_truncation {
            raw_measurement
        } else {
            measurer.measure(
                row.language.as_deref(),
                &display_text,
                font_size_px,
                content_width_px,
            )?
        };
        let key = HudGeometryKey {
            slot,
            display_text: display_text.clone(),
            role: row.role,
            language: row.language.clone(),
            style_key: measured.style.style_key,
            font_size_key: scalar_key(font_size_px),
            content_width_key: content_width_px.round() as u32,
            text_scale_key: scalar_key(text_scale),
        };
        let measured = if let Some(cached) = cache.entries.get(&key) {
            reused += 1;
            cached.clone()
        } else {
            let measured = MeasuredHudLine {
                style_key: measured.style.style_key,
                style: measured.style,
                width_px: measured.width_px,
                font_size_px,
            };
            cache.entries.insert(key.clone(), measured.clone());
            measured
        };

        // Draw-time color is selected solely by `hud_kind`; the line role stays
        // Primary and `opacity` stays 1.0 for both draft and settled.
        let line_role = LineRole::Primary;
        let top_px = hud_slot_top_px(slot);
        let line_visual_bounds = VisualBounds::new(
            HUD_TEXT_LEFT_PX - TEXT_OUTLINE_OVERHANG_PX,
            top_px - TEXT_OUTLINE_OVERHANG_PX,
            HUD_TEXT_LEFT_PX + measured.width_px + TEXT_OUTLINE_OVERHANG_PX,
            top_px + measured.font_size_px * 1.15 + TEXT_OUTLINE_OVERHANG_PX,
        );
        let line = ResolvedLineLayout {
            text: display_text.clone(),
            role: line_role,
            style_key: measured.style_key,
            style: measured.style.clone(),
            width_px: measured.width_px,
            origin_x: HUD_TEXT_LEFT_PX,
            origin_y: top_px,
            font_size_px: measured.font_size_px,
            visual_bounds: line_visual_bounds,
        };
        let block = ResolvedBlockLayout {
            id: format!("slot-{slot}"),
            // Geometry key: constant block_variant and no kind, so draft and
            // settled layouts with the same text share cache identity.
            layout_cache_key: LayoutCacheKey {
                primary_text: display_text,
                secondary_text: String::new(),
                primary_style_key: measured.style_key,
                secondary_style_key: measured.style_key,
                channel: None,
                block_variant: CaptionBlockVariant::Finalized,
                secondary_enabled: false,
                secondary_reserved: false,
                primary_font_size_key: scalar_key(measured.font_size_px),
                secondary_font_size_key: 0,
                content_width_key: content_width_px.round() as u32,
                text_scale_key: scalar_key(text_scale),
            },
            channel: None,
            // Non-cacheable variant forces the per-line draw path, whose line
            // cache key includes the kind-derived `LineRole` (brush color).
            block_variant: CaptionBlockVariant::ActiveSelf,
            primary_lines: vec![line],
            secondary_line: None,
            secondary_reserved: false,
            bounds: BlockBounds::new(
                HUD_TEXT_LEFT_PX,
                top_px,
                HUD_TEXT_LEFT_PX + measured.width_px,
                top_px + measured.font_size_px * 1.2,
            ),
            visual_bounds: line_visual_bounds,
            content_width_px,
            opacity: 1.0,
            hud_kind: Some(row.kind),
            render_offset_y_px: 0.0,
            render_height_scale: 1.0,
            truncated_primary: false,
            truncated_secondary: false,
        };

        key.hash(&mut hasher);
        row.kind.hash(&mut hasher);
        geometry_keys[slot] = Some(key);
        blocks.push(block);
    }

    Ok(BuiltHudLayout {
        layout: ResolvedFrameLayout {
            visible_blocks: blocks,
            dropped_block_ids: Vec::new(),
            surface_width_px,
            surface_height_px: DEFAULT_SURFACE_HEIGHT_PX,
            damage_band: None,
        },
        geometry_keys,
        visual_signature: hasher.finish(),
        geometry_reused: occupied == reused,
    })
}

/// The renderer is the only composer of the speaker label.
fn compose_display_text(row: &crate::hud::HudRow) -> String {
    match row.speaker_label.as_deref() {
        Some(label) if !label.is_empty() => format!("{label} {}", row.text),
        _ => row.text.clone(),
    }
}

fn scalar_key(value: f32) -> u32 {
    (value * 100.0).round() as u32
}

/// Default heuristic measurement used until DirectWrite calibration lands.
pub(crate) fn heuristic_measure(
    text: &str,
    _style: &TextStyleDescriptor,
    font_size_px: f32,
) -> f32 {
    let average_glyph_advance_px = font_size_px * (80.0 / 140.0);
    measure_text_width(text, average_glyph_advance_px)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hud::{HudFrame, HudRow, HudRowKind};

    fn row(role: HudRowRole, kind: HudRowKind, text: &str) -> HudRow {
        HudRow {
            role,
            kind,
            text: text.into(),
            speaker_label: None,
            language: None,
            sentence: None,
        }
    }

    fn frame(entries: &[(usize, HudRow)]) -> HudFrame {
        let mut frame = HudFrame::default();
        for (slot, row) in entries {
            frame.slots[*slot] = Some(row.clone());
        }
        frame
    }

    fn build(frame: &HudFrame, cache: &mut HudLayoutCache) -> BuiltHudLayout {
        build_hud_layout(
            frame,
            1.0,
            4096,
            &FontResolver::default(),
            cache,
            &|text, style, size| heuristic_measure(text, style, size),
        )
    }

    fn custom_measure(
        frame: &HudFrame,
        cache: &mut HudLayoutCache,
        measure: &dyn Fn(&str, &TextStyleDescriptor, f32) -> f32,
    ) -> BuiltHudLayout {
        build_hud_layout(frame, 1.0, 4096, &FontResolver::default(), cache, measure)
    }

    #[test]
    fn sparse_frame_uses_fixed_slot_origins_without_slot_bytes() {
        let frame = frame(&[
            (0, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "a")),
            (2, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "b")),
            (4, row(HudRowRole::LiveSource, HudRowKind::Settled, "c")),
        ]);
        let built = build(&frame, &mut HudLayoutCache::new());
        assert_eq!(built.layout.visible_blocks.len(), 3);
        for (slot, id) in [(0usize, "slot-0"), (2, "slot-2"), (4, "slot-4")] {
            let block = built
                .layout
                .visible_blocks
                .iter()
                .find(|block| block.id == id)
                .unwrap();
            assert_eq!(block.primary_lines[0].origin_x, HUD_TEXT_LEFT_PX);
            assert_eq!(block.primary_lines[0].origin_y, hud_slot_top_px(slot));
        }
    }

    #[test]
    fn empty_slots_do_not_move_other_slots() {
        let sparse = frame(&[
            (0, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "a")),
            (4, row(HudRowRole::LiveSource, HudRowKind::Settled, "live")),
        ]);
        let dense = frame(&[
            (0, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "a")),
            (1, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "b")),
            (4, row(HudRowRole::LiveSource, HudRowKind::Settled, "live")),
        ]);
        let sparse_live = build(&sparse, &mut HudLayoutCache::new())
            .layout
            .visible_blocks
            .into_iter()
            .find(|block| block.id == "slot-4")
            .unwrap();
        let dense_live = build(&dense, &mut HudLayoutCache::new())
            .layout
            .visible_blocks
            .into_iter()
            .find(|block| block.id == "slot-4")
            .unwrap();
        assert_eq!(
            sparse_live.primary_lines[0].origin_y,
            dense_live.primary_lines[0].origin_y
        );
        assert_eq!(
            sparse_live.primary_lines[0].origin_x,
            dense_live.primary_lines[0].origin_x
        );
    }

    #[test]
    fn slot_four_is_reserved_for_the_live_row() {
        let frame = frame(&[(4, row(HudRowRole::LiveSource, HudRowKind::Settled, "live"))]);
        let built = build(&frame, &mut HudLayoutCache::new());
        assert_eq!(built.layout.visible_blocks.len(), 1);
        let block = &built.layout.visible_blocks[0];
        assert_eq!(block.id, "slot-4");
        assert_eq!(block.primary_lines[0].origin_y, hud_slot_top_px(4));
        assert_eq!(
            built.geometry_keys[4].as_ref().unwrap().role,
            HudRowRole::LiveSource
        );
    }

    #[test]
    fn speaker_label_is_composed_by_the_renderer_and_changes_the_geometry_key() {
        let mut labeled = row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello");
        labeled.speaker_label = Some("S1".into());
        let with_label = build(&frame(&[(3, labeled)]), &mut HudLayoutCache::new());
        let without = build(
            &frame(&[(
                3,
                row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello"),
            )]),
            &mut HudLayoutCache::new(),
        );
        assert_eq!(
            with_label.layout.visible_blocks[0].primary_lines[0].text,
            "S1 hello"
        );
        assert_ne!(
            with_label.layout.visible_blocks[0].block_cache_key(),
            without.layout.visible_blocks[0].block_cache_key()
        );
    }

    #[test]
    fn draft_and_settled_share_the_geometry_key_but_not_the_draw_color() {
        let draft = build(
            &frame(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Draft, "bon"))]),
            &mut HudLayoutCache::new(),
        );
        let settled = build(
            &frame(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "bon"))]),
            &mut HudLayoutCache::new(),
        );
        assert_eq!(
            draft.layout.visible_blocks[0].block_cache_key(),
            settled.layout.visible_blocks[0].block_cache_key()
        );
        assert_eq!(draft.layout.visible_blocks[0].opacity, 1.0);
        assert_eq!(settled.layout.visible_blocks[0].opacity, 1.0);
        assert_eq!(
            draft.layout.visible_blocks[0].primary_lines[0].role,
            LineRole::Primary
        );
        assert_eq!(
            settled.layout.visible_blocks[0].primary_lines[0].role,
            LineRole::Primary
        );
        assert_eq!(
            draft.layout.visible_blocks[0].hud_kind,
            Some(HudRowKind::Draft)
        );
        assert_eq!(
            settled.layout.visible_blocks[0].hud_kind,
            Some(HudRowKind::Settled)
        );
    }

    #[test]
    fn geometry_key_carries_the_resolved_style_identity() {
        let built = build(
            &frame(&[(
                3,
                row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello"),
            )]),
            &mut HudLayoutCache::new(),
        );
        let key = built.geometry_keys[3].as_ref().unwrap();
        let line = &built.layout.visible_blocks[0].primary_lines[0];
        assert_eq!(key.style_key, line.style_key);
        assert_eq!(key.display_text, line.text);
    }

    #[test]
    fn hud_fill_colors_differ_in_rgb_with_equal_alpha() {
        let draft = crate::renderer::types::hud_fill_color(HudRowKind::Draft);
        let settled = crate::renderer::types::hud_fill_color(HudRowKind::Settled);
        assert_eq!(draft.3, settled.3, "alpha must be equal");
        assert_ne!(
            (draft.0, draft.1, draft.2),
            (settled.0, settled.1, settled.2)
        );
    }

    #[test]
    fn kind_change_reuses_geometry_but_changes_the_visual_signature() {
        let mut cache = HudLayoutCache::new();
        let draft_frame = frame(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Draft, "bon"))]);
        let settled_frame =
            frame(&[(3, row(HudRowRole::UpperPrimary, HudRowKind::Settled, "bon"))]);
        let first = build(&draft_frame, &mut cache);
        assert!(
            !first.geometry_reused,
            "first build must populate the geometry cache"
        );
        let second = build(&settled_frame, &mut cache);
        assert!(second.geometry_reused, "same text/role must reuse geometry");
        assert_ne!(first.visual_signature, second.visual_signature);
    }

    #[test]
    fn unchanged_frame_has_a_stable_visual_signature() {
        let mut cache = HudLayoutCache::new();
        let frame = frame(&[(
            3,
            row(HudRowRole::UpperPrimary, HudRowKind::Settled, "same"),
        )]);
        let first = build(&frame, &mut cache);
        let second = build(&frame, &mut cache);
        assert_eq!(first.visual_signature, second.visual_signature);
        assert!(second.geometry_reused);
    }

    #[test]
    fn placement_is_identical_across_measurement_backends() {
        let frame = frame(&[
            (
                3,
                row(HudRowRole::UpperPrimary, HudRowKind::Settled, "hello world"),
            ),
            (
                4,
                row(HudRowRole::LiveSource, HudRowKind::Settled, "live text"),
            ),
        ]);
        let heuristic = build(&frame, &mut HudLayoutCache::new());
        let doubling = custom_measure(&frame, &mut HudLayoutCache::new(), &|text, _style, size| {
            heuristic_measure(text, _style, size) * 2.0
        });
        for slot in [3usize, 4] {
            let a = heuristic
                .layout
                .visible_blocks
                .iter()
                .find(|block| block.id == format!("slot-{slot}"))
                .unwrap();
            let b = doubling
                .layout
                .visible_blocks
                .iter()
                .find(|block| block.id == format!("slot-{slot}"))
                .unwrap();
            assert_eq!(a.primary_lines[0].origin_x, b.primary_lines[0].origin_x);
            assert_eq!(a.primary_lines[0].origin_y, b.primary_lines[0].origin_y);
            assert!((b.primary_lines[0].width_px - a.primary_lines[0].width_px).abs() > 0.0);
        }
    }
}

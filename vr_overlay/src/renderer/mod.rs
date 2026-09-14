mod backend;
pub(crate) mod cache;
mod font_resolver;
mod glyph_run;
mod hud_layout;
mod layout;
mod text_fit;
mod types;

pub use backend::{CaptionRenderer, HudRenderOutcome, RenderedFrame};
#[cfg(windows)]
pub use font_resolver::WindowsBundledFontCollection;
pub use font_resolver::{
    bundled_font_path_from_exe_dir, runtime_bundled_font_path, BundledFaceId, FontFallbackReason,
    FontLanguageBucket, FontResolver, FontSource, FontWeight, ResolvedFontStyle, TextFamilyKey,
    TextLocaleKey, TextStyleKey, BUNDLED_NOTO_CJK_FILE_NAME,
};
pub use hud_layout::{
    hud_content_width_px, hud_slot_top_px, HudGeometryKey, HudLayoutCache, HUD_FIRST_SLOT_TOP_PX,
    HUD_SLOT_STRIDE_PX, HUD_TEXT_LEFT_PX,
};
pub use layout::CaptionLayoutPolicy;
pub use text_fit::{fit_row_text, Truncation};
#[cfg(test)]
pub(crate) use types::{
    effective_background_alpha, fill_color_for_channel, outline_offsets_px, text_script_bucket,
    TextScriptBucket,
};
pub use types::{
    hud_fill_color, BlockBounds, CaptionBlock, CaptionBlockVariant, CaptionChannel,
    CaptionDebugOverlay, CaptionLayoutResult, CaptionLineLayout, CaptionPresentation,
    CaptionRenderError, DamageBand, LayoutCacheKey, LineRole, ResolvedBlockLayout,
    ResolvedFrameLayout, ResolvedLineLayout, TextStyleDescriptor, VisibleCaptionBlock,
    VisualBounds, HUD_DRAFT_FILL_COLOR, HUD_FINAL_FILL_COLOR,
};

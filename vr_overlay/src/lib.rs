pub mod bridge;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod renderer;
pub mod runtime;
pub mod state;

pub use bridge::{BridgeClient, BridgeControl, BridgeError, OverlayBridgeEvent};
pub use logging::{OverlayLogger, OverlayLoggingMode};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{
    submit_texture, FakeOpenVr, OpenVrError, OpenVrOverlay, OverlayFrameSubmitter,
    OverlayPlacementPolicy,
};
#[cfg(windows)]
pub use renderer::WindowsBundledFontCollection;
pub use renderer::{
    bundled_font_path_from_exe_dir, runtime_bundled_font_path, BlockBounds, BundledFaceId,
    CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionDebugOverlay, CaptionLayoutPolicy,
    CaptionLayoutResult, CaptionPresentation, CaptionRenderError, CaptionRenderer, DamageBand,
    FontFallbackReason, FontLanguageBucket, FontResolver, FontSource, FontWeight, RenderedFrame,
    ResolvedFontStyle, TextStyleKey,
};
pub use runtime::{
    run_cli, run_with_manifest, OverlayRuntime, RuntimeFailure, SnapshotApplyOutcome, StartupError,
};
pub use state::{
    OverlayCalibration, OverlayPresentationBlock, OverlayPresentationBlockVariant,
    OverlayPresentationCalibration, OverlayPresentationSnapshot, OverlayState, OverlayStateScene,
    OverlayStateSlot, PresentationScene, PresentationSlot, RuntimeState,
};

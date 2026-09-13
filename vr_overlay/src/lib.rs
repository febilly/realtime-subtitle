pub mod bridge;
mod desktop_caption;
pub mod hud;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod projection;
pub mod protocol;
pub mod renderer;
pub mod runtime;
pub mod state;
pub mod transcript;
pub mod views;

pub use bridge::{BridgeClient, BridgeControl, BridgeError, CaptionUpdate, OverlayBridgeEvent};
pub use hud::{HudFrame, HudRow, HudRowKind, HudRowRole, HUD_SLOT_COUNT};
pub use logging::{OverlayLogger, OverlayLoggingMode};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{
    submit_texture, FakeOpenVr, OpenVrError, OpenVrOverlay, OverlayFrameSubmitter,
    OverlayPlacementPolicy,
};
pub use projection::{project, LiveRowDirective, Projection, LIVE_SOURCE_HOLD};
pub use protocol::{DesktopProtocol, TrackKind};
#[cfg(windows)]
pub use renderer::WindowsBundledFontCollection;
pub use renderer::{
    bundled_font_path_from_exe_dir, fit_row_text, runtime_bundled_font_path, BlockBounds,
    BundledFaceId, CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionDebugOverlay,
    CaptionLayoutPolicy, CaptionLayoutResult, CaptionPresentation, CaptionRenderError,
    CaptionRenderer, DamageBand, FontFallbackReason, FontLanguageBucket, FontResolver, FontSource,
    FontWeight, HudRenderOutcome, RenderedFrame, ResolvedFontStyle, TextStyleKey, Truncation,
};
pub use runtime::{
    run_cli, run_with_manifest, OverlayRuntime, RuntimeFailure, SnapshotApplyOutcome, StartupError,
};
pub use state::{
    OverlayCalibration, OverlayPresentationBlock, OverlayPresentationBlockVariant,
    OverlayPresentationCalibration, OverlayPresentationSnapshot, OverlayState, OverlayStateScene,
    OverlayStateSlot, PresentationScene, PresentationSlot, RuntimeState,
};
pub use transcript::{
    CaptionEvent, CommittedSource, LiveInputRow, LiveSourceSnapshot, Refinement, SentenceKey,
    SentenceRecord, SpeakerKey, TargetUpdate, TextTrack, TrackPhase, TranscriptState,
};
pub use views::{DisplayMode, SettingsError, VrViewSettings};

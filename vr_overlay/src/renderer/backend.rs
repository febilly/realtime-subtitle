use std::cell::RefCell;
use std::ffi::c_void;
#[cfg(windows)]
use std::mem::ManuallyDrop;
#[cfg(windows)]
use std::time::Instant;

#[cfg(windows)]
use windows::core::{Interface, PCWSTR};
#[cfg(windows)]
use windows::Win32::Foundation::HMODULE;
#[cfg(windows)]
use windows::Win32::Graphics::Direct2D::{
    Common::{
        D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_COMPOSITE_MODE_SOURCE_OVER,
        D2D1_PIXEL_FORMAT, D2D_RECT_F,
    },
    D2D1CreateFactory, ID2D1Bitmap1, ID2D1DeviceContext, ID2D1Factory1, ID2D1Layer,
    ID2D1SolidColorBrush, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE, D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
    D2D1_BITMAP_OPTIONS_TARGET, D2D1_BITMAP_PROPERTIES1, D2D1_DEVICE_CONTEXT_OPTIONS_NONE,
    D2D1_FACTORY_TYPE_SINGLE_THREADED, D2D1_INTERPOLATION_MODE_LINEAR, D2D1_LAYER_OPTIONS1_NONE,
    D2D1_LAYER_PARAMETERS1, D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE,
};
#[cfg(windows)]
use windows::Win32::Graphics::Direct3D::{
    D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP, D3D_FEATURE_LEVEL_10_0, D3D_FEATURE_LEVEL_10_1,
    D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
};
#[cfg(windows)]
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Query, ID3D11Texture2D,
    D3D11_BIND_RENDER_TARGET, D3D11_BIND_SHADER_RESOURCE, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
    D3D11_QUERY_DESC, D3D11_QUERY_EVENT, D3D11_RESOURCE_MISC_SHARED, D3D11_SDK_VERSION,
    D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
};
#[cfg(windows)]
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteFactory2, IDWriteFontCollection,
    IDWriteFontFallback, IDWriteFontFamily, IDWriteTextFormat, IDWriteTextFormat1,
    IDWriteTextLayout, IDWriteTextLayout2, DWRITE_FACTORY_TYPE_SHARED, DWRITE_FONT_STRETCH_NORMAL,
    DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT, DWRITE_FONT_WEIGHT_MEDIUM,
    DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_TEXT_ALIGNMENT_CENTER,
    DWRITE_WORD_WRAPPING_NO_WRAP,
};
#[cfg(windows)]
use windows::Win32::Graphics::Dxgi::{
    Common::{DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC},
    IDXGIDevice, IDXGISurface,
};
#[cfg(windows)]
use windows_numerics::{Matrix3x2, Vector2};

use super::cache::{CachedBlockVisual, CachedLineVisual, RenderCaches};
#[cfg(windows)]
use super::font_resolver::{
    runtime_bundled_font_path, system_ui_language_hint, FontResolver, FontWeight,
    ResolvedFontStyle, TextStyleKey, WindowsBundledFontCollection,
};
use super::font_resolver::{FontLanguageBucket, FontSource};
use super::glyph_run::render_text_layout_to_command_list;
#[cfg(windows)]
use super::layout::DirectWriteLayoutEngine;
use super::layout::{resolved_layout_has_drawable_text, CaptionLayoutPolicy};
#[cfg(windows)]
use super::types::{
    contains_cjk, effective_background_alpha, fill_color_for_channel, outline_offsets_px,
    secondary_fill_color_for_channel, BlockBounds, BlockCacheKey, CaptionBlockVariant,
    CaptionChannel, CaptionDebugOverlay, CaptionLayoutResult, CaptionPresentation,
    CaptionRenderError, DamageBand, LineCacheKey, LineRole, RenderDiagnostics, ResolvedBlockLayout,
    ResolvedFrameLayout, ResolvedLineLayout, ResolvedTextStyle, StyleBucketSourceCount,
    TextStyleDescriptor, DEFAULT_FONT_SIZE_PX, DEFAULT_SURFACE_HEIGHT_PX, DEFAULT_SURFACE_WIDTH_PX,
    SECONDARY_FONT_SCALE, TEXT_OUTLINE_COLOR,
};

const TEXT_FORMAT_CACHE_CAP: usize = 64;
const DAMAGE_BAND_SAFETY_MARGIN_PX: f32 = 32.0;
const DEBUG_OVERLAY_DAMAGE_BOTTOM_PX: f32 = 80.0;
#[cfg(windows)]
const CJK_WARMUP_SAMPLES: [(&str, &str); 4] = [
    ("ko", "한글"),
    ("ja", "日本語"),
    ("zh-CN", "中文"),
    ("zh-TW", "繁體"),
];

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FirstCjkLayoutDiagnosticOutcome {
    NoTiming,
    AlreadyLogged,
    Success,
    Failure,
}

#[cfg(windows)]
fn first_cjk_layout_diagnostic_outcome<T>(
    timing: Option<T>,
    already_logged: bool,
    directwrite_succeeded: bool,
) -> FirstCjkLayoutDiagnosticOutcome {
    if timing.is_none() {
        return FirstCjkLayoutDiagnosticOutcome::NoTiming;
    }
    if already_logged {
        return FirstCjkLayoutDiagnosticOutcome::AlreadyLogged;
    }
    if directwrite_succeeded {
        FirstCjkLayoutDiagnosticOutcome::Success
    } else {
        FirstCjkLayoutDiagnosticOutcome::Failure
    }
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy, Default)]
struct FontWarmupStats {
    elapsed_ms: u128,
    attempts: u32,
    failures: u32,
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TextFormatCollectionRoute {
    Bundled,
    System,
    FallbackToSystem,
}

pub struct CaptionRenderer {
    policy: CaptionLayoutPolicy,
    presentation: RefCell<CaptionPresentation>,
    backend: RefCell<RenderBackend>,
}

impl CaptionRenderer {
    pub fn new() -> Result<Self, CaptionRenderError> {
        Self::with_policy(CaptionLayoutPolicy::default(), BackendMode::Runtime)
    }

    pub fn new_for_test() -> Result<Self, CaptionRenderError> {
        Self::with_policy(CaptionLayoutPolicy::default(), BackendMode::Test)
    }

    pub fn render_empty_frame(&self) -> Result<RenderedFrame, CaptionRenderError> {
        self.render_empty_frame_with_debug_overlay(None)
    }

    pub fn render_empty_frame_with_debug_overlay(
        &self,
        debug_overlay: Option<CaptionDebugOverlay>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        self.render_blocks_with_debug_overlay(Vec::new(), debug_overlay)
    }

    pub fn render_blocks(
        &self,
        blocks: Vec<super::types::CaptionBlock>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        self.render_blocks_with_debug_overlay(blocks, None)
    }

    pub fn render_blocks_with_debug_overlay(
        &self,
        blocks: Vec<super::types::CaptionBlock>,
        debug_overlay: Option<CaptionDebugOverlay>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        let (width, height) = self.policy.default_surface_size();
        let presentation = self.presentation.borrow().clone();
        self.backend.borrow_mut().render(
            &self.policy,
            &presentation,
            blocks,
            width,
            height,
            debug_overlay,
        )
    }

    fn with_policy(
        policy: CaptionLayoutPolicy,
        backend_mode: BackendMode,
    ) -> Result<Self, CaptionRenderError> {
        Ok(Self {
            policy,
            presentation: RefCell::new(CaptionPresentation::default()),
            backend: RefCell::new(match backend_mode {
                BackendMode::Runtime => RenderBackend::new_runtime()?,
                BackendMode::Test => RenderBackend::new_test()?,
            }),
        })
    }

    pub fn set_presentation(&self, presentation: CaptionPresentation) {
        self.presentation.replace(presentation);
    }
}

enum BackendMode {
    Runtime,
    Test,
}

#[derive(Debug)]
pub struct RenderedFrame {
    width: u32,
    height: u32,
    fully_transparent: bool,
    layout: CaptionLayoutResult,
    diagnostics: RenderDiagnostics,
    texture: TextureHandle,
    debug_overlay: Option<CaptionDebugOverlay>,
}

impl RenderedFrame {
    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    pub fn is_fully_transparent(&self) -> bool {
        self.fully_transparent
    }

    pub fn texture_ptr(&self) -> Option<*mut c_void> {
        Some(self.texture.as_ptr())
    }

    pub fn layout(&self) -> &CaptionLayoutResult {
        &self.layout
    }

    pub fn diagnostics(&self) -> &RenderDiagnostics {
        &self.diagnostics
    }

    pub fn debug_overlay_label(&self) -> Option<&str> {
        self.debug_overlay.as_ref().map(CaptionDebugOverlay::label)
    }

    #[cfg(windows)]
    pub fn d3d11_texture(&self) -> Option<&ID3D11Texture2D> {
        self.texture.d3d11_texture()
    }
}

#[derive(Debug)]
enum TextureHandle {
    #[cfg(windows)]
    D3D11(ID3D11Texture2D),
    #[cfg(not(windows))]
    Test(TestTextureHandle),
}

impl TextureHandle {
    fn as_ptr(&self) -> *mut c_void {
        match self {
            #[cfg(windows)]
            Self::D3D11(texture) => texture.as_raw(),
            #[cfg(not(windows))]
            Self::Test(texture) => texture.as_ptr(),
        }
    }

    #[cfg(windows)]
    fn d3d11_texture(&self) -> Option<&ID3D11Texture2D> {
        match self {
            Self::D3D11(texture) => Some(texture),
        }
    }
}

#[cfg(not(windows))]
#[derive(Debug)]
struct TestTextureHandle {
    marker: Box<u8>,
}

#[cfg(not(windows))]
impl TestTextureHandle {
    fn new() -> Self {
        Self {
            marker: Box::new(1),
        }
    }

    fn as_ptr(&self) -> *mut c_void {
        (&*self.marker as *const u8 as *mut u8).cast()
    }
}

enum RenderBackend {
    #[cfg(windows)]
    Windows(WindowsCaptionRenderer),
    #[cfg(not(windows))]
    Test(TestCaptionRenderer),
}

impl RenderBackend {
    fn new_runtime() -> Result<Self, CaptionRenderError> {
        #[cfg(windows)]
        {
            return WindowsCaptionRenderer::new().map(Self::Windows);
        }

        #[cfg(not(windows))]
        {
            Err(CaptionRenderError::Init(
                "the Direct3D11 caption renderer is only available on Windows".into(),
            ))
        }
    }

    fn new_test() -> Result<Self, CaptionRenderError> {
        #[cfg(windows)]
        {
            return WindowsCaptionRenderer::new().map(Self::Windows);
        }

        #[cfg(not(windows))]
        {
            Ok(Self::Test(TestCaptionRenderer::default()))
        }
    }

    fn render(
        &mut self,
        policy: &CaptionLayoutPolicy,
        presentation: &CaptionPresentation,
        blocks: Vec<super::types::CaptionBlock>,
        width: u32,
        height: u32,
        debug_overlay: Option<CaptionDebugOverlay>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        match self {
            #[cfg(windows)]
            Self::Windows(renderer) => {
                renderer.render(policy, presentation, blocks, width, height, debug_overlay)
            }
            #[cfg(not(windows))]
            Self::Test(renderer) => {
                let _ = presentation;
                let layout =
                    policy.resolve_blocks_for_presentation(blocks, width, height, presentation);
                renderer.render(layout, debug_overlay)
            }
        }
    }
}

#[cfg(not(windows))]
#[derive(Default)]
struct TestCaptionRenderer {
    previous_layout: Option<ResolvedFrameLayout>,
}

#[cfg(not(windows))]
impl TestCaptionRenderer {
    fn render(
        &mut self,
        layout: ResolvedFrameLayout,
        debug_overlay: Option<CaptionDebugOverlay>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        let fully_transparent = !resolved_layout_has_drawable_text(&layout);
        let debug_overlay = if fully_transparent {
            None
        } else {
            debug_overlay
        };
        let public_layout: CaptionLayoutResult = layout.clone().into();
        let mut diagnostics = RenderDiagnostics::default();
        diagnostics.style_bucket_source_counts = style_bucket_source_counts(&layout);

        Ok(RenderedFrame {
            width: public_layout.surface_width_px,
            height: public_layout.surface_height_px,
            fully_transparent,
            layout: public_layout,
            diagnostics,
            texture: TextureHandle::Test(TestTextureHandle::new()),
            debug_overlay,
        })
    }
}

#[cfg(windows)]
struct WindowsCaptionRenderer {
    d2d_factory: ID2D1Factory1,
    dwrite_factory: IDWriteFactory,
    system_font_collection: IDWriteFontCollection,
    system_font_fallback: IDWriteFontFallback,
    font_resolver: FontResolver,
    bundled_font_collection: Option<WindowsBundledFontCollection>,
    layout_engine: DirectWriteLayoutEngine,
    d2d_context: ID2D1DeviceContext,
    cache_outline_brush: ID2D1SolidColorBrush,
    cache_self_text_brush: ID2D1SolidColorBrush,
    cache_peer_text_brush: ID2D1SolidColorBrush,
    cache_peer_secondary_text_brush: ID2D1SolidColorBrush,
    target_bitmap: ID2D1Bitmap1,
    texture: ID3D11Texture2D,
    caches: RenderCaches,
    previous_layout: Option<ResolvedFrameLayout>,
    _d3d_device: ID3D11Device,
    _d3d_context: ID3D11DeviceContext,
    _gpu_sync_query: ID3D11Query,
    first_cjk_layout_logged: bool,
    first_cjk_command_list_logged: bool,
    first_cjk_line_visual_logged: bool,
    frame_text_format_cache_hits: u32,
    frame_text_format_cache_misses: u32,
    font_warmup_attempts: u32,
    font_warmup_failures: u32,
    previous_debug_overlay_visible: bool,
}

#[cfg(windows)]
impl WindowsCaptionRenderer {
    fn new() -> Result<Self, CaptionRenderError> {
        let renderer_init_started = Instant::now();
        let (device, d3d_context) = create_d3d_device()?;
        // GPU 同步事件查询: 绘制完成后等待 GPU 真正执行完, 再向 SteamVR
        // 提交纹理 (否则 compositor 的跨进程拷贝可能先于我们的绘制执行,
        // 读到全透明内容)。
        let mut gpu_sync_query = None;
        let query_desc = D3D11_QUERY_DESC {
            Query: D3D11_QUERY_EVENT,
            MiscFlags: 0,
        };
        unsafe {
            device
                .CreateQuery(&query_desc, Some(&mut gpu_sync_query))
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
        }
        let gpu_sync_query = gpu_sync_query
            .ok_or_else(|| CaptionRenderError::Init("gpu sync query missing".into()))?;
        let d2d_factory = create_d2d_factory()?;
        let dwrite_factory = create_dwrite_factory()?;
        let factory2: IDWriteFactory2 = dwrite_factory
            .cast()
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
        let system_font_collection = get_system_font_collection(&dwrite_factory)?;
        let system_font_fallback = unsafe {
            factory2
                .GetSystemFontFallback()
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let (font_resolver, bundled_font_collection) =
            initialize_bundled_font_collection(&dwrite_factory);
        let layout_engine = DirectWriteLayoutEngine::from_shared_resources(
            &dwrite_factory,
            &system_font_collection,
            &system_font_fallback,
            font_resolver.clone(),
            bundled_font_collection.clone(),
        );
        let texture = create_target_texture(&device)?;
        let d2d_context = create_d2d_context(&device, &d2d_factory)?;
        let dxgi_surface: IDXGISurface = texture
            .cast()
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
        let bitmap_properties = D2D1_BITMAP_PROPERTIES1 {
            pixelFormat: D2D1_PIXEL_FORMAT {
                format: DXGI_FORMAT_B8G8R8A8_UNORM,
                alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED,
            },
            dpiX: 96.0,
            dpiY: 96.0,
            bitmapOptions: D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
            colorContext: ManuallyDrop::new(None),
        };
        let target_bitmap = unsafe {
            d2d_context
                .CreateBitmapFromDxgiSurface(&dxgi_surface, Some(&bitmap_properties))
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        unsafe {
            d2d_context.SetTarget(&target_bitmap);
            d2d_context.SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
        }
        let cache_outline_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(&d2d_color(TEXT_OUTLINE_COLOR), None)
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let cache_self_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &d2d_color(fill_color_for_channel(CaptionChannel::SelfChannel)),
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let cache_peer_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &d2d_color(fill_color_for_channel(CaptionChannel::PeerChannel)),
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let cache_peer_secondary_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &d2d_color(secondary_fill_color_for_channel(
                        CaptionChannel::PeerChannel,
                    )),
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let mut renderer = Self {
            d2d_factory,
            dwrite_factory,
            system_font_collection,
            system_font_fallback,
            font_resolver,
            bundled_font_collection,
            layout_engine,
            d2d_context,
            cache_outline_brush,
            cache_self_text_brush,
            cache_peer_text_brush,
            cache_peer_secondary_text_brush,
            target_bitmap,
            texture,
            caches: RenderCaches::new(TEXT_FORMAT_CACHE_CAP),
            previous_layout: None,
            _d3d_device: device,
            _d3d_context: d3d_context,
            _gpu_sync_query: gpu_sync_query,
            first_cjk_layout_logged: false,
            first_cjk_command_list_logged: false,
            first_cjk_line_visual_logged: false,
            frame_text_format_cache_hits: 0,
            frame_text_format_cache_misses: 0,
            font_warmup_attempts: 0,
            font_warmup_failures: 0,
            previous_debug_overlay_visible: false,
        };
        let warmup_stats = renderer.warm_up_cjk_fonts();
        eprintln!(
            "[overlay][DIAG] renderer_init_total_ms={} font_warmup_ms={}",
            renderer_init_started.elapsed().as_millis(),
            warmup_stats.elapsed_ms
        );
        Ok(renderer)
    }

    fn warm_up_cjk_fonts(&mut self) -> FontWarmupStats {
        let started = Instant::now();
        let sizes = [
            ("primary", DEFAULT_FONT_SIZE_PX),
            ("secondary", DEFAULT_FONT_SIZE_PX * SECONDARY_FONT_SCALE),
        ];
        let mut attempts = 0u32;
        let mut failures = 0u32;

        for (language, sample) in CJK_WARMUP_SAMPLES {
            for (role, font_size_px) in sizes {
                attempts += 1;
                if let Err(error) =
                    self.warm_up_cjk_text_format_layout(language, sample, role, font_size_px)
                {
                    failures += 1;
                    eprintln!(
                        "[overlay][WARN] cjk_font_warmup_failure language={} role={} font_size_px={:.2} error={}",
                        language, role, font_size_px, error
                    );
                }
            }
        }

        let elapsed_ms = started.elapsed().as_millis();
        eprintln!(
            "[overlay][DIAG] font_warmup_ms={} warmup_entries={} failures={}",
            elapsed_ms, attempts, failures
        );
        self.font_warmup_attempts = attempts;
        self.font_warmup_failures = failures;
        FontWarmupStats {
            elapsed_ms,
            attempts,
            failures,
        }
    }

    fn warm_up_cjk_text_format_layout(
        &self,
        language: &str,
        sample: &str,
        _role: &str,
        font_size_px: f32,
    ) -> Result<(), CaptionRenderError> {
        let requested_style = self.font_resolver.resolve(Some(language), sample);
        let resolved_style =
            self.resolve_requested_text_style(&CaptionLayoutPolicy::default(), requested_style);
        let (text_format, _) = self.create_text_format_for_resolved_style(
            &resolved_style,
            font_size_px,
            DWRITE_WORD_WRAPPING_NO_WRAP,
        )?;
        let utf16: Vec<u16> = sample.encode_utf16().collect();
        let text_layout = unsafe {
            self.dwrite_factory
                .CreateTextLayout(&utf16, &text_format, 512.0, font_size_px * 1.5)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if let Ok(text_layout_2) = text_layout.cast::<IDWriteTextLayout2>() {
            unsafe {
                text_layout_2
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        let mut metrics = windows::Win32::Graphics::DirectWrite::DWRITE_TEXT_METRICS::default();
        unsafe {
            text_layout
                .GetMetrics(&mut metrics)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
        }
        Ok(())
    }

    fn cache_brush_for_channel(&self, channel: CaptionChannel) -> ID2D1SolidColorBrush {
        match channel {
            CaptionChannel::SelfChannel => self.cache_self_text_brush.clone(),
            CaptionChannel::PeerChannel => self.cache_peer_text_brush.clone(),
        }
    }

    fn line_cache_key(
        &self,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> LineCacheKey {
        LineCacheKey {
            text: line.text.clone(),
            role,
            style_key: line.style_key,
            channel: block.channel,
            block_variant: block.block_variant,
            font_size_key: (line.font_size_px * 100.0).round() as u32,
            content_width_key: block.content_width_px.round() as u32,
            text_scale_key: block.layout_cache_key.text_scale_key,
        }
    }

    fn block_cache_key(&self, block: &ResolvedBlockLayout) -> BlockCacheKey {
        block.block_cache_key()
    }

    fn cacheable_block(&self, block: &ResolvedBlockLayout) -> bool {
        block.block_variant == CaptionBlockVariant::Finalized
    }

    fn draw_cached_command_list_with_state(
        &self,
        command_list: &windows::Win32::Graphics::Direct2D::ID2D1CommandList,
        origin_x: f32,
        origin_y: f32,
        opacity: f32,
        render_height_scale: f32,
    ) -> Result<(), CaptionRenderError> {
        let mut previous_transform = Matrix3x2::default();
        unsafe {
            self.d2d_context.GetTransform(&mut previous_transform);
        }
        let transform = Matrix3x2 {
            M11: 1.0,
            M12: 0.0,
            M21: 0.0,
            M22: render_height_scale,
            M31: origin_x,
            M32: origin_y,
        };
        let mut opacity_layer: Option<ID2D1Layer> = None;

        unsafe {
            self.d2d_context.SetTransform(&transform);
        }
        let draw_result = (|| {
            unsafe {
                if opacity < 1.0 - f32::EPSILON {
                    let layer = self
                        .d2d_context
                        .CreateLayer(None)
                        .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
                    let layer_parameters = D2D1_LAYER_PARAMETERS1 {
                        contentBounds: D2D_RECT_F {
                            left: 0.0,
                            top: 0.0,
                            right: DEFAULT_SURFACE_WIDTH_PX as f32,
                            bottom: DEFAULT_SURFACE_HEIGHT_PX as f32,
                        },
                        geometricMask: ManuallyDrop::new(None),
                        maskAntialiasMode: D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
                        maskTransform: identity_matrix(),
                        opacity,
                        opacityBrush: ManuallyDrop::new(None),
                        layerOptions: D2D1_LAYER_OPTIONS1_NONE,
                    };
                    self.d2d_context.PushLayer(&layer_parameters, &layer);
                    opacity_layer = Some(layer);
                }
                self.d2d_context.DrawImage(
                    command_list,
                    None,
                    None,
                    D2D1_INTERPOLATION_MODE_LINEAR,
                    D2D1_COMPOSITE_MODE_SOURCE_OVER,
                );
            }
            Ok(())
        })();

        unsafe {
            if opacity_layer.is_some() {
                self.d2d_context.PopLayer();
            }
            self.d2d_context.SetTransform(&previous_transform);
        }

        draw_result
    }

    fn build_cached_line_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let channel = block.channel.unwrap_or(CaptionChannel::SelfChannel);
        let fill_brush = match role {
            LineRole::Secondary => self.cache_peer_secondary_text_brush.clone(),
            LineRole::Primary => self.cache_brush_for_channel(channel),
        };
        let outline_brush = self.cache_outline_brush.clone();
        unsafe {
            fill_brush.SetOpacity(1.0);
            outline_brush.SetOpacity(1.0);
        }
        self.build_line_visual_with_brushes(policy, block, line, role, &fill_brush, &outline_brush)
    }

    fn build_line_visual_with_brushes(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
        fill_brush: &ID2D1SolidColorBrush,
        outline_brush: &ID2D1SolidColorBrush,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let line_has_cjk = contains_cjk(line.text.trim());
        let line_visual_started = if line_has_cjk && !self.first_cjk_line_visual_logged {
            Some(Instant::now())
        } else {
            None
        };
        let _ = policy;
        let text_layout = self.create_text_layout_for_line_style(
            &line.style,
            line.text.trim(),
            line.font_size_px,
            block.content_width_px,
            line.font_size_px * 1.15,
        )?;
        let command_list_started = if line_has_cjk && !self.first_cjk_command_list_logged {
            Some(Instant::now())
        } else {
            None
        };
        let glyph_visual = render_text_layout_to_command_list(
            &self.d2d_context,
            &self.d2d_factory,
            &text_layout,
            fill_brush,
            outline_brush,
            outline_offsets_px()[0]
                .0
                .abs()
                .max(outline_offsets_px()[2].1.abs())
                * 2.0,
        )?;
        if let Some(started) = command_list_started {
            self.first_cjk_command_list_logged = true;
            eprintln!(
                "[overlay][DIAG] first_cjk_command_list_ms={} text_len={} font_size_px={:.2}",
                started.elapsed().as_millis(),
                line.text.chars().count(),
                line.font_size_px
            );
        }
        let _ = role;
        if let Some(started) = line_visual_started {
            self.first_cjk_line_visual_logged = true;
            eprintln!(
                "[overlay][DIAG] first_cjk_line_visual_ms={} text_len={} font_size_px={:.2}",
                started.elapsed().as_millis(),
                line.text.chars().count(),
                line.font_size_px
            );
        }
        Ok(CachedLineVisual {
            command_list: glyph_visual.command_list,
            visual_bounds: glyph_visual.visual_bounds,
        })
    }

    fn cached_line_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let key = self.line_cache_key(block, line, role);
        if let Some(cached) = self.caches.line_cache.get(&key) {
            diagnostics.line_cache_hits += 1;
            return Ok(cached.clone());
        }
        diagnostics.line_cache_misses += 1;
        let cached = self.build_cached_line_visual(policy, block, line, role)?;
        self.caches.line_cache.insert(key, cached.clone());
        Ok(cached)
    }

    fn prepared_line_visual(
        &mut self,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let key = self.line_cache_key(block, line, role);
        self.caches.line_cache.get(&key).cloned().ok_or_else(|| {
            CaptionRenderError::Draw(format!(
                "missing prepared line cache for block={} role={role:?}",
                block.id
            ))
        })
    }

    fn build_cached_block_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        _diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedBlockVisual, CaptionRenderError> {
        let previous_target = unsafe { self.d2d_context.GetTarget().ok() };
        let command_list = unsafe {
            self.d2d_context
                .CreateCommandList()
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        unsafe {
            self.d2d_context.SetTarget(&command_list);
            self.d2d_context.BeginDraw();
        }

        let mut visual_bounds: Option<super::types::VisualBounds> = None;
        let build_result = (|| {
            for (role, line) in block_lines(block) {
                if line.text.trim().is_empty() {
                    continue;
                }
                let cached = self.prepared_line_visual(block, line, role)?;
                let offset = Vector2 {
                    X: policy.strip_horizontal_padding_px() as f32,
                    Y: stable_line_origin_y(block, line),
                };
                unsafe {
                    self.d2d_context.DrawImage(
                        &cached.command_list,
                        Some(&offset),
                        None,
                        D2D1_INTERPOLATION_MODE_LINEAR,
                        D2D1_COMPOSITE_MODE_SOURCE_OVER,
                    );
                }
                let translated = super::types::VisualBounds::new(
                    cached.visual_bounds.left_px + offset.X,
                    cached.visual_bounds.top_px + offset.Y,
                    cached.visual_bounds.right_px + offset.X,
                    cached.visual_bounds.bottom_px + offset.Y,
                );
                visual_bounds = Some(match visual_bounds {
                    Some(current) => super::types::VisualBounds::new(
                        current.left_px.min(translated.left_px),
                        current.top_px.min(translated.top_px),
                        current.right_px.max(translated.right_px),
                        current.bottom_px.max(translated.bottom_px),
                    ),
                    None => translated,
                });
            }
            Ok(())
        })();
        let end_draw_result = unsafe {
            self.d2d_context
                .EndDraw(None, None)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))
        };
        unsafe {
            self.d2d_context.SetTarget(previous_target.as_ref());
        }

        match (build_result, end_draw_result) {
            (Err(error), _) => Err(error),
            (Ok(()), Err(error)) => Err(error),
            (Ok(()), Ok(())) => {
                unsafe {
                    command_list
                        .Close()
                        .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
                }
                Ok(CachedBlockVisual {
                    command_list,
                    visual_bounds: visual_bounds
                        .unwrap_or_else(|| super::types::VisualBounds::new(0.0, 0.0, 0.0, 0.0)),
                })
            }
        }
    }

    fn cached_block_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedBlockVisual, CaptionRenderError> {
        let key = self.block_cache_key(block);
        if let Some(cached) = self.caches.block_cache.get(&key) {
            diagnostics.block_cache_hits += 1;
            return Ok(cached.clone());
        }
        diagnostics.block_cache_misses += 1;
        let cached = self.build_cached_block_visual(policy, block, diagnostics)?;
        self.caches.block_cache.insert(key, cached.clone());
        Ok(cached)
    }

    fn build_debug_overlay_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        overlay: &CaptionDebugOverlay,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let label = overlay.label();
        let font_size_px = 34.0;
        let content_width_px = DEFAULT_SURFACE_WIDTH_PX as f32 - 96.0;
        let line_height_px = 44.0;

        let text_layout = self.create_text_layout(
            policy,
            label,
            font_size_px,
            content_width_px,
            line_height_px,
        )?;

        unsafe {
            self.cache_self_text_brush.SetOpacity(0.72);
            self.cache_outline_brush.SetOpacity(0.85);
        }

        let visual = render_text_layout_to_command_list(
            &self.d2d_context,
            &self.d2d_factory,
            &text_layout,
            &self.cache_self_text_brush,
            &self.cache_outline_brush,
            outline_offsets_px()[0]
                .0
                .abs()
                .max(outline_offsets_px()[2].1.abs())
                * 2.0,
        );
        unsafe {
            self.cache_self_text_brush.SetOpacity(1.0);
            self.cache_outline_brush.SetOpacity(1.0);
        }
        let visual = visual?;

        Ok(CachedLineVisual {
            command_list: visual.command_list,
            visual_bounds: visual.visual_bounds,
        })
    }

    fn draw_debug_overlay_visual(&self, visual: &CachedLineVisual) {
        let offset = Vector2 { X: 32.0, Y: 24.0 };
        unsafe {
            self.d2d_context.DrawImage(
                &visual.command_list,
                Some(&offset),
                None,
                D2D1_INTERPOLATION_MODE_LINEAR,
                D2D1_COMPOSITE_MODE_SOURCE_OVER,
            );
        }
    }

    fn prepare_line_visuals(
        &mut self,
        policy: &CaptionLayoutPolicy,
        layout: &ResolvedFrameLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<(), CaptionRenderError> {
        for block in &layout.visible_blocks {
            for (role, line) in block_lines(block) {
                if line.text.trim().is_empty() {
                    continue;
                }
                let _ = self
                    .cached_line_visual(policy, block, line, role, diagnostics)
                    .map_err(|error| prefix_render_error("line_cache_build", error))?;
            }
        }
        Ok(())
    }

    fn prepare_block_visuals(
        &mut self,
        policy: &CaptionLayoutPolicy,
        layout: &ResolvedFrameLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<(), CaptionRenderError> {
        for block in &layout.visible_blocks {
            if !self.cacheable_block(block) {
                continue;
            }
            let _ = self
                .cached_block_visual(policy, block, diagnostics)
                .map_err(|error| prefix_render_error("block_cache_build", error))?;
        }
        Ok(())
    }

    fn record_cache_sizes(&self, diagnostics: &mut RenderDiagnostics) {
        diagnostics.text_format_cache_size = self.caches.text_format_cache.len();
        diagnostics.layout_cache_size = self.caches.layout_cache.len();
        diagnostics.line_cache_size = self.caches.line_cache.len();
        diagnostics.block_cache_size = self.caches.block_cache.len();
        diagnostics.text_format_cache_hits = self.frame_text_format_cache_hits;
        diagnostics.text_format_cache_misses = self.frame_text_format_cache_misses;
        diagnostics.font_warmup_attempts = self.font_warmup_attempts;
        diagnostics.font_warmup_failures = self.font_warmup_failures;
    }

    fn render(
        &mut self,
        policy: &CaptionLayoutPolicy,
        presentation: &CaptionPresentation,
        blocks: Vec<super::types::CaptionBlock>,
        width: u32,
        height: u32,
        debug_overlay: Option<CaptionDebugOverlay>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        self.frame_text_format_cache_hits = 0;
        self.frame_text_format_cache_misses = 0;
        let mut diagnostics = RenderDiagnostics::default();
        let contains_cjk_text = caption_blocks_contain_cjk_style(&blocks);
        let cjk_layout_started = if contains_cjk_text && !self.first_cjk_layout_logged {
            Some(Instant::now())
        } else {
            None
        };
        for block in &blocks {
            let key = policy.layout_cache_key_for_block_windows(
                block,
                width,
                presentation,
                &self.layout_engine,
            );
            if self.caches.layout_cache.contains_key(&key) {
                diagnostics.layout_cache_hits += 1;
            } else {
                diagnostics.layout_cache_misses += 1;
            }
        }
        let layout = match policy.resolve_blocks_for_presentation_windows_cached(
            blocks.clone(),
            width,
            height,
            presentation,
            &self.layout_engine,
            Some(&mut self.caches.layout_cache),
        ) {
            Ok(layout) => {
                diagnostics.directwrite_layout_success_count += 1;
                if first_cjk_layout_diagnostic_outcome(
                    cjk_layout_started.as_ref(),
                    self.first_cjk_layout_logged,
                    true,
                ) == FirstCjkLayoutDiagnosticOutcome::Success
                {
                    self.first_cjk_layout_logged = true;
                    if let Some(started) = cjk_layout_started.as_ref() {
                        eprintln!(
                            "[overlay][DIAG] first_cjk_layout_ms={} layout_cache_size={}",
                            started.elapsed().as_millis(),
                            self.caches.layout_cache.len()
                        );
                    }
                }
                layout
            }
            Err(error) => {
                diagnostics.heuristic_layout_fallback_count += 1;
                if first_cjk_layout_diagnostic_outcome(
                    cjk_layout_started.as_ref(),
                    self.first_cjk_layout_logged,
                    false,
                ) == FirstCjkLayoutDiagnosticOutcome::Failure
                {
                    if let Some(started) = cjk_layout_started.as_ref() {
                        eprintln!(
                            "[overlay][DIAG] first_cjk_layout_failure_ms={} error={error}",
                            started.elapsed().as_millis()
                        );
                    }
                }
                eprintln!(
                    "[overlay][WARN] catastrophic_directwrite_layout_failure stage=layout_cache error={error}"
                );
                policy.resolve_blocks_for_presentation(blocks, width, height, presentation)
            }
        };
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        diagnostics.style_bucket_source_counts = style_bucket_source_counts(&layout);
        let layout_has_drawable_text = resolved_layout_has_drawable_text(&layout);
        let debug_overlay = if layout_has_drawable_text {
            debug_overlay
        } else {
            None
        };
        self.prepare_line_visuals(policy, &layout, &mut diagnostics)?;
        self.prepare_block_visuals(policy, &layout, &mut diagnostics)?;
        let debug_overlay_visual = debug_overlay
            .as_ref()
            .map(|overlay| self.build_debug_overlay_visual(policy, overlay))
            .transpose()?;
        let clear_alpha = effective_background_alpha(layout_has_drawable_text, presentation);
        let mut damage_band = layout.damage_band.unwrap_or(DamageBand {
            top_px: 0.0,
            bottom_px: layout.surface_height_px as f32,
        });
        let should_clear_debug_overlay_band =
            debug_overlay.is_some() || self.previous_debug_overlay_visible;
        if should_clear_debug_overlay_band {
            damage_band.top_px = damage_band.top_px.min(0.0);
            damage_band.bottom_px = damage_band
                .bottom_px
                .max(DEBUG_OVERLAY_DAMAGE_BOTTOM_PX.min(layout.surface_height_px as f32));
            diagnostics.debug_overlay_clear_count += 1;
        }
        unsafe {
            self.d2d_context.SetTarget(&self.target_bitmap);
            self.d2d_context.BeginDraw();
            self.d2d_context.PushAxisAlignedClip(
                &D2D_RECT_F {
                    left: 0.0,
                    top: damage_band.top_px,
                    right: layout.surface_width_px as f32,
                    bottom: damage_band.bottom_px,
                },
                D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
            );
            self.d2d_context.Clear(Some(&D2D1_COLOR_F {
                r: 0.0,
                g: 0.0,
                b: 0.0,
                a: clear_alpha,
            }));
            self.d2d_context.PopAxisAlignedClip();
        }

        unsafe {
            self.d2d_context.PushAxisAlignedClip(
                &D2D_RECT_F {
                    left: 0.0,
                    top: damage_band.top_px,
                    right: layout.surface_width_px as f32,
                    bottom: damage_band.bottom_px,
                },
                D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
            );
        }
        let render_result = (|| {
            for block in &layout.visible_blocks {
                if !bounds_intersect_damage_band(block.visual_bounds.as_block_bounds(), damage_band)
                {
                    continue;
                }

                if self.cacheable_block(block) {
                    let cache_key = self.block_cache_key(block);
                    let cached_block = self
                        .caches
                        .block_cache
                        .get(&cache_key)
                        .cloned()
                        .ok_or_else(|| {
                            CaptionRenderError::Draw(format!(
                                "missing prepared block cache for block={}",
                                block.id
                            ))
                        })?;
                    self.draw_cached_command_list_with_state(
                        &cached_block.command_list,
                        block.bounds.left_px,
                        block.bounds.top_px,
                        block.opacity,
                        block.render_height_scale,
                    )?;
                    continue;
                }

                for (role, line) in block_lines(block) {
                    let trimmed = line.text.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let line_visual = self.prepared_line_visual(block, line, role)?;
                    self.draw_cached_command_list_with_state(
                        &line_visual.command_list,
                        block.bounds.left_px + policy.strip_horizontal_padding_px() as f32,
                        line.origin_y,
                        block.opacity,
                        block.render_height_scale,
                    )?;
                }
            }
            if let Some(debug_overlay_visual) = debug_overlay_visual.as_ref() {
                self.draw_debug_overlay_visual(debug_overlay_visual);
                diagnostics.debug_overlay_draw_count += 1;
            }
            self.record_cache_sizes(&mut diagnostics);
            let public_layout: CaptionLayoutResult = layout.clone().into();
            Ok(RenderedFrame {
                width: public_layout.surface_width_px,
                height: public_layout.surface_height_px,
                fully_transparent: !layout_has_drawable_text,
                layout: public_layout,
                diagnostics,
                texture: TextureHandle::D3D11(self.texture.clone()),
                debug_overlay,
            })
        })()
        .map_err(|error| prefix_render_error("frame_compose", error));
        unsafe {
            self.d2d_context.PopAxisAlignedClip();
        }
        let end_draw_result = unsafe {
            self.d2d_context
                .EndDraw(None, None)
                .map_err(|error| CaptionRenderError::Draw(format!("frame_compose: {}", error)))
        };

        match (render_result, end_draw_result) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Ok(frame), Ok(())) => {
                self.wait_for_gpu();
                self.previous_debug_overlay_visible = frame.debug_overlay.is_some();
                Ok(frame)
            }
        }
    }

    /// 等待 GPU 完成已提交的绘制命令 (事件查询 + Flush + 轮询 GetData)。
    /// 必须在纹理交给 SteamVR compositor 之前调用: compositor 用独立设备
    /// 跨进程拷贝纹理, 若我们的绘制尚未执行, 它拷到的是旧/空内容。
    fn wait_for_gpu(&self) {
        unsafe {
            self._d3d_context.End(&self._gpu_sync_query);
            self._d3d_context.Flush();
        }
        let deadline = Instant::now() + std::time::Duration::from_millis(500);
        loop {
            let done = unsafe { self._d3d_context.GetData(&self._gpu_sync_query, None, 0, 0) }
                .map(|_| true)
                .unwrap_or(false);
            if done || Instant::now() >= deadline {
                break;
            }
            std::thread::yield_now();
        }
    }

    fn create_text_format(
        &mut self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
    ) -> Result<IDWriteTextFormat, CaptionRenderError> {
        let font_size_key = (font_size_px * 100.0).round() as u32;
        let resolved_style =
            self.text_format_style_for_available_collections(self.resolve_text_style(policy, text));
        self.cached_text_format_for_resolved_style(resolved_style, font_size_key, font_size_px)
    }

    fn create_text_format_for_line_style(
        &mut self,
        style: &TextStyleDescriptor,
        font_size_px: f32,
    ) -> Result<IDWriteTextFormat, CaptionRenderError> {
        let resolved_style = resolved_text_style_from_descriptor(style);
        let resolved_style = self.text_format_style_for_available_collections(resolved_style);
        let font_size_key = (font_size_px * 100.0).round() as u32;
        self.cached_text_format_for_resolved_style(resolved_style, font_size_key, font_size_px)
    }

    fn cached_text_format_for_resolved_style(
        &mut self,
        resolved_style: ResolvedTextStyle,
        font_size_key: u32,
        font_size_px: f32,
    ) -> Result<IDWriteTextFormat, CaptionRenderError> {
        let text_format_key = (resolved_style.style_key, font_size_key);
        if let Some(text_format) = self.caches.text_format_cache.get(&text_format_key).cloned() {
            self.frame_text_format_cache_hits += 1;
            return Ok(text_format);
        }
        self.frame_text_format_cache_misses += 1;

        eprintln!(
            "[overlay][DIAG] text_format_cache_miss style_key={:?} font_size_key={} resolved_bucket={:?} source={:?} family={} locale={} cache_size_before={}",
            resolved_style.style_key,
            font_size_key,
            resolved_style.bucket,
            resolved_style.source,
            resolved_style.family_name,
            resolved_style.locale,
            self.caches.text_format_cache.len()
        );
        let (text_format, actual_style_key) = self.create_text_format_for_resolved_style(
            &resolved_style,
            font_size_px,
            DWRITE_WORD_WRAPPING_NO_WRAP,
        )?;
        self.caches
            .text_format_cache
            .insert((actual_style_key, font_size_key), text_format.clone());
        Ok(text_format)
    }

    #[cfg(test)]
    fn text_format_cache_key_for_test(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
    ) -> (TextStyleKey, u32) {
        (
            self.text_format_style_for_available_collections(self.resolve_text_style(policy, text))
                .style_key,
            (font_size_px * 100.0).round() as u32,
        )
    }

    #[cfg(test)]
    fn text_format_cache_key_for_line_test(
        &self,
        line: &ResolvedLineLayout,
    ) -> (TextStyleKey, u32) {
        let resolved_style = resolved_text_style_from_descriptor(&line.style);
        let resolved_style = self.text_format_style_for_available_collections(resolved_style);
        (
            resolved_style.style_key,
            (line.font_size_px * 100.0).round() as u32,
        )
    }

    fn create_text_format_for_resolved_style(
        &self,
        resolved_style: &ResolvedTextStyle,
        font_size_px: f32,
        word_wrapping: windows::Win32::Graphics::DirectWrite::DWRITE_WORD_WRAPPING,
    ) -> Result<(IDWriteTextFormat, TextStyleKey), CaptionRenderError> {
        match text_format_collection_route(
            resolved_style.source,
            self.bundled_font_collection.is_some(),
        ) {
            TextFormatCollectionRoute::FallbackToSystem => {
                eprintln!(
                    "[overlay][WARN] bundled_font_collection_missing_before_text_format family={} locale={}",
                    resolved_style.family_name, resolved_style.locale
                );
                let fallback_style = fallback_resolved_text_style_for_bucket_locale(
                    resolved_style.bucket,
                    resolved_style.locale.clone(),
                );
                return self.create_text_format_for_resolved_style(
                    &fallback_style,
                    font_size_px,
                    word_wrapping,
                );
            }
            TextFormatCollectionRoute::Bundled | TextFormatCollectionRoute::System => {}
        }

        let locale = utf16_null(&resolved_style.locale);
        let face_name = utf16_null(&resolved_style.family_name);
        let create_result = match text_format_collection_route(
            resolved_style.source,
            self.bundled_font_collection.is_some(),
        ) {
            TextFormatCollectionRoute::Bundled => {
                let collection = self
                    .bundled_font_collection
                    .as_ref()
                    .expect("bundled collection route requires bundled collection");
                unsafe {
                    self.dwrite_factory.CreateTextFormat(
                        PCWSTR::from_raw(face_name.as_ptr()),
                        collection.collection(),
                        resolved_style.weight,
                        DWRITE_FONT_STYLE_NORMAL,
                        DWRITE_FONT_STRETCH_NORMAL,
                        font_size_px,
                        PCWSTR::from_raw(locale.as_ptr()),
                    )
                }
            }
            TextFormatCollectionRoute::System => unsafe {
                self.dwrite_factory.CreateTextFormat(
                    PCWSTR::from_raw(face_name.as_ptr()),
                    None,
                    resolved_style.weight,
                    DWRITE_FONT_STYLE_NORMAL,
                    DWRITE_FONT_STRETCH_NORMAL,
                    font_size_px,
                    PCWSTR::from_raw(locale.as_ptr()),
                )
            },
            TextFormatCollectionRoute::FallbackToSystem => unreachable!(
                "FallbackToSystem route returns before DirectWrite text format creation"
            ),
        };
        let text_format = match create_result {
            Ok(text_format) => text_format,
            Err(error) if resolved_style.source != FontSource::SystemFallbackSentinel => {
                eprintln!(
                    "[overlay][WARN] directwrite_style_resolution_failure family={} locale={} error={}",
                    resolved_style.family_name, resolved_style.locale, error
                );
                let fallback = FontResolver::style_resolution_failure_fallback_for_bucket_locale(
                    resolved_style.bucket,
                    resolved_style.locale.clone(),
                );
                let fallback_style = resolved_text_style_from_resolved_font_style(fallback);
                return self.create_text_format_for_resolved_style(
                    &fallback_style,
                    font_size_px,
                    word_wrapping,
                );
            }
            Err(error) => return Err(CaptionRenderError::Draw(error.to_string())),
        };
        unsafe {
            text_format
                .SetWordWrapping(word_wrapping)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            text_format
                .SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            if let Ok(text_format_1) = text_format.cast::<IDWriteTextFormat1>() {
                text_format_1
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        Ok((text_format, resolved_style.style_key))
    }

    fn create_text_layout(
        &mut self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
        max_width_px: f32,
        max_height_px: f32,
    ) -> Result<IDWriteTextLayout, CaptionRenderError> {
        let text_format = self.create_text_format(policy, text, font_size_px)?;
        let utf16: Vec<u16> = text.encode_utf16().collect();
        let text_layout = unsafe {
            self.dwrite_factory
                .CreateTextLayout(&utf16, &text_format, max_width_px, max_height_px)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if let Ok(text_layout_2) = text_layout.cast::<IDWriteTextLayout2>() {
            unsafe {
                text_layout_2
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        Ok(text_layout)
    }

    fn create_text_layout_for_line_style(
        &mut self,
        style: &TextStyleDescriptor,
        text: &str,
        font_size_px: f32,
        max_width_px: f32,
        max_height_px: f32,
    ) -> Result<IDWriteTextLayout, CaptionRenderError> {
        let text_format = self.create_text_format_for_line_style(style, font_size_px)?;
        let utf16: Vec<u16> = text.encode_utf16().collect();
        let text_layout = unsafe {
            self.dwrite_factory
                .CreateTextLayout(&utf16, &text_format, max_width_px, max_height_px)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if let Ok(text_layout_2) = text_layout.cast::<IDWriteTextLayout2>() {
            unsafe {
                text_layout_2
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        Ok(text_layout)
    }

    fn resolve_text_style(&self, policy: &CaptionLayoutPolicy, text: &str) -> ResolvedTextStyle {
        let requested_style = self.font_resolver.resolve(None, text);
        self.resolve_requested_text_style(policy, requested_style)
    }

    fn text_format_style_for_available_collections(
        &self,
        resolved_style: ResolvedTextStyle,
    ) -> ResolvedTextStyle {
        if text_format_collection_route(
            resolved_style.source,
            self.bundled_font_collection.is_some(),
        ) == TextFormatCollectionRoute::FallbackToSystem
        {
            return fallback_resolved_text_style_for_bucket_locale(
                resolved_style.bucket,
                resolved_style.locale,
            );
        }
        resolved_style
    }

    #[cfg(test)]
    fn resolved_text_style_key_for_test(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
    ) -> TextStyleKey {
        self.resolve_text_style(policy, text).style_key
    }

    fn resolve_requested_text_style(
        &self,
        policy: &CaptionLayoutPolicy,
        requested_style: ResolvedFontStyle,
    ) -> ResolvedTextStyle {
        if requested_style.source == FontSource::BundledNotoCjkMedium
            && self.bundled_font_collection.is_some()
        {
            let style_key = requested_style.style_key();
            return ResolvedTextStyle {
                family_name: requested_style.family_name.to_string(),
                weight: dwrite_weight(requested_style.weight),
                locale: requested_style.locale,
                source: requested_style.source,
                bucket: requested_style.bucket,
                style_key,
            };
        }

        for family_name in requested_style
            .system_fallback_families()
            .iter()
            .copied()
            .filter(|candidate| *candidate != "DirectWrite system fallback")
        {
            let family = match self.find_font_family(family_name) {
                Ok(Some(family)) => family,
                Ok(None) => continue,
                Err(error) => {
                    eprintln!(
                        "[overlay][WARN] directwrite_style_resolution_failure family={} locale={} error={}",
                        family_name, requested_style.locale, error
                    );
                    break;
                }
            };
            let Some(weight) = (match resolve_family_weight(&family, policy) {
                Ok(weight) => weight,
                Err(error) => {
                    eprintln!(
                        "[overlay][WARN] directwrite_style_resolution_failure family={} locale={} error={}",
                        family_name, requested_style.locale, error
                    );
                    break;
                }
            }) else {
                continue;
            };
            let weight_key = font_weight_from_dwrite_weight(weight);
            let style_key = TextStyleKey::from_parts(
                requested_style.bucket,
                FontSource::SystemFont,
                None,
                family_name,
                weight_key,
                &requested_style.locale,
            );
            return ResolvedTextStyle {
                family_name: family_name.to_string(),
                weight,
                locale: requested_style.locale,
                source: FontSource::SystemFont,
                bucket: requested_style.bucket,
                style_key,
            };
        }

        let fallback = FontResolver::style_resolution_failure_fallback(requested_style.bucket);
        let style_key = fallback.style_key();
        ResolvedTextStyle {
            family_name: fallback.family_name.to_string(),
            weight: dwrite_weight(fallback.weight),
            locale: fallback.locale,
            source: fallback.source,
            bucket: fallback.bucket,
            style_key,
        }
    }

    fn find_font_family(
        &self,
        family_name: &str,
    ) -> Result<Option<IDWriteFontFamily>, CaptionRenderError> {
        let family_name = utf16_null(family_name);
        let mut index = 0;
        let mut exists = false.into();
        unsafe {
            self.system_font_collection
                .FindFamilyName(
                    PCWSTR::from_raw(family_name.as_ptr()),
                    &mut index,
                    &mut exists,
                )
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            if !exists.as_bool() {
                return Ok(None);
            }
            self.system_font_collection
                .GetFontFamily(index)
                .map(Some)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))
        }
    }
}

#[cfg(windows)]
fn d2d_color(color: (f32, f32, f32, f32)) -> D2D1_COLOR_F {
    D2D1_COLOR_F {
        r: color.0,
        g: color.1,
        b: color.2,
        a: color.3,
    }
}

#[cfg(windows)]
fn create_dwrite_factory() -> Result<IDWriteFactory, CaptionRenderError> {
    unsafe {
        DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))
    }
}

#[cfg(windows)]
fn initialize_bundled_font_collection(
    factory: &IDWriteFactory,
) -> (FontResolver, Option<WindowsBundledFontCollection>) {
    let started = Instant::now();
    let ui_language_hint = system_ui_language_hint();
    let path = match runtime_bundled_font_path() {
        Ok(path) => path,
        Err(error) => {
            let reason = format!("resolve bundled font runtime path: {error}");
            eprintln!("[overlay][WARN] bundled_font_unavailable {reason}");
            log_font_bundle_load(started, "unavailable", &reason);
            return (
                font_resolver_with_bundle_unavailable(reason, ui_language_hint),
                None,
            );
        }
    };
    match WindowsBundledFontCollection::load_with_factory(factory, &path) {
        Ok(collection) => {
            let detail = collection.path().display().to_string();
            log_font_bundle_load(started, "available", &detail);
            (
                font_resolver_with_bundle_available(ui_language_hint),
                Some(collection),
            )
        }
        Err(error) => {
            let reason = format!("{} ({})", path.display(), error);
            eprintln!("[overlay][WARN] bundled_font_unavailable {reason}");
            log_font_bundle_load(started, "unavailable", &reason);
            (
                font_resolver_with_bundle_unavailable(reason, ui_language_hint),
                None,
            )
        }
    }
}

#[cfg(windows)]
fn log_font_bundle_load(started: Instant, status: &str, detail: &str) {
    eprintln!(
        "[overlay][DIAG] font_bundle_load_ms={} status={} detail={}",
        started.elapsed().as_millis(),
        status,
        detail
    );
}

#[cfg(windows)]
fn font_resolver_with_bundle_available(ui_language_hint: Option<String>) -> FontResolver {
    FontResolver::with_bundle_available().with_optional_ui_language_hint(ui_language_hint)
}

#[cfg(windows)]
fn font_resolver_with_bundle_unavailable(
    reason: String,
    ui_language_hint: Option<String>,
) -> FontResolver {
    FontResolver::with_bundle_unavailable(reason).with_optional_ui_language_hint(ui_language_hint)
}

#[cfg(windows)]
fn dwrite_weight(weight: FontWeight) -> DWRITE_FONT_WEIGHT {
    match weight {
        FontWeight::Regular => DWRITE_FONT_WEIGHT_NORMAL,
        FontWeight::Medium => DWRITE_FONT_WEIGHT_MEDIUM,
        FontWeight::SemiBold => DWRITE_FONT_WEIGHT_SEMI_BOLD,
    }
}

#[cfg(windows)]
fn resolved_text_style_from_resolved_font_style(style: ResolvedFontStyle) -> ResolvedTextStyle {
    let style_key = style.style_key();
    ResolvedTextStyle {
        family_name: style.family_name.to_string(),
        weight: dwrite_weight(style.weight),
        locale: style.locale,
        source: style.source,
        bucket: style.bucket,
        style_key,
    }
}

#[cfg(windows)]
fn resolved_text_style_from_descriptor(style: &TextStyleDescriptor) -> ResolvedTextStyle {
    ResolvedTextStyle {
        family_name: style.family_name.clone(),
        weight: dwrite_weight(style.weight),
        locale: style.locale.clone(),
        source: style.source,
        bucket: style.bucket,
        style_key: style.style_key,
    }
}

#[cfg(windows)]
fn fallback_resolved_text_style_for_bucket_locale(
    bucket: super::font_resolver::FontLanguageBucket,
    locale: String,
) -> ResolvedTextStyle {
    resolved_text_style_from_resolved_font_style(
        FontResolver::style_resolution_failure_fallback_for_bucket_locale(bucket, locale),
    )
}

#[cfg(windows)]
fn font_weight_from_dwrite_weight(weight: DWRITE_FONT_WEIGHT) -> FontWeight {
    if weight == DWRITE_FONT_WEIGHT_SEMI_BOLD {
        FontWeight::SemiBold
    } else if weight == DWRITE_FONT_WEIGHT_MEDIUM {
        FontWeight::Medium
    } else {
        FontWeight::Regular
    }
}

#[cfg(windows)]
fn text_format_collection_route(
    source: FontSource,
    bundled_collection_available: bool,
) -> TextFormatCollectionRoute {
    match (source, bundled_collection_available) {
        (FontSource::BundledNotoCjkMedium, true) => TextFormatCollectionRoute::Bundled,
        (FontSource::BundledNotoCjkMedium, false) => TextFormatCollectionRoute::FallbackToSystem,
        _ => TextFormatCollectionRoute::System,
    }
}

#[cfg(all(windows, test))]
fn text_format_collection_route_for_test(
    source: FontSource,
    bundled_collection_available: bool,
) -> TextFormatCollectionRoute {
    text_format_collection_route(source, bundled_collection_available)
}

#[cfg(windows)]
fn get_system_font_collection(
    factory: &IDWriteFactory,
) -> Result<IDWriteFontCollection, CaptionRenderError> {
    let mut collection = None;
    unsafe {
        factory
            .GetSystemFontCollection(&mut collection, false)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }
    collection.ok_or_else(|| CaptionRenderError::Init("system font collection missing".into()))
}

#[cfg(windows)]
fn create_d2d_factory() -> Result<ID2D1Factory1, CaptionRenderError> {
    unsafe {
        D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))
    }
}

#[cfg(windows)]
fn create_d3d_device() -> Result<(ID3D11Device, ID3D11DeviceContext), CaptionRenderError> {
    let feature_levels = [
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    ];

    create_d3d_device_for_driver(D3D_DRIVER_TYPE_HARDWARE, &feature_levels)
        .or_else(|_| create_d3d_device_for_driver(D3D_DRIVER_TYPE_WARP, &feature_levels))
}

#[cfg(windows)]
fn create_d3d_device_for_driver(
    driver_type: windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE,
    feature_levels: &[windows::Win32::Graphics::Direct3D::D3D_FEATURE_LEVEL],
) -> Result<(ID3D11Device, ID3D11DeviceContext), CaptionRenderError> {
    let mut device = None;
    let mut context = None;

    unsafe {
        D3D11CreateDevice(
            None,
            driver_type,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )
        .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }

    let device = device.ok_or_else(|| CaptionRenderError::Init("d3d device missing".into()))?;
    let context =
        context.ok_or_else(|| CaptionRenderError::Init("d3d device context missing".into()))?;
    Ok((device, context))
}

#[cfg(windows)]
fn create_target_texture(device: &ID3D11Device) -> Result<ID3D11Texture2D, CaptionRenderError> {
    let mut texture = None;
    let description = D3D11_TEXTURE2D_DESC {
        Width: DEFAULT_SURFACE_WIDTH_PX,
        Height: DEFAULT_SURFACE_HEIGHT_PX,
        MipLevels: 1,
        ArraySize: 1,
        Format: DXGI_FORMAT_B8G8R8A8_UNORM,
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        Usage: D3D11_USAGE_DEFAULT,
        BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_SHADER_RESOURCE.0) as u32,
        CPUAccessFlags: 0,
        // 共享纹理: SteamVR compositor 以独立 D3D11 设备访问 overlay 纹理,
        // 非共享纹理跨设备拷贝会静默失败 (SteamVR 读到空内容)。
        MiscFlags: D3D11_RESOURCE_MISC_SHARED.0 as u32,
    };

    unsafe {
        device
            .CreateTexture2D(&description, None, Some(&mut texture))
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }

    texture.ok_or_else(|| CaptionRenderError::Init("renderer texture missing".into()))
}

#[cfg(windows)]
fn create_d2d_context(
    device: &ID3D11Device,
    factory: &ID2D1Factory1,
) -> Result<ID2D1DeviceContext, CaptionRenderError> {
    let dxgi_device: IDXGIDevice = device
        .cast()
        .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    let d2d_device = unsafe {
        factory
            .CreateDevice(&dxgi_device)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?
    };
    let d2d_context = unsafe {
        d2d_device
            .CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?
    };
    Ok(d2d_context)
}

#[cfg(windows)]
fn identity_matrix() -> Matrix3x2 {
    Matrix3x2 {
        M11: 1.0,
        M12: 0.0,
        M21: 0.0,
        M22: 1.0,
        M31: 0.0,
        M32: 0.0,
    }
}

#[cfg(windows)]
fn stable_line_origin_y(block: &ResolvedBlockLayout, line: &ResolvedLineLayout) -> f32 {
    (line.origin_y - block.bounds.top_px) / block.render_height_scale.max(f32::EPSILON)
}

#[cfg(windows)]
fn prefix_render_error(stage: &str, error: CaptionRenderError) -> CaptionRenderError {
    match error {
        CaptionRenderError::Init(message) => {
            CaptionRenderError::Init(format!("{stage}: {message}"))
        }
        CaptionRenderError::Draw(message) => {
            CaptionRenderError::Draw(format!("{stage}: {message}"))
        }
    }
}

#[cfg(windows)]
fn block_lines(
    block: &ResolvedBlockLayout,
) -> impl Iterator<Item = (LineRole, &ResolvedLineLayout)> + '_ {
    block
        .primary_lines
        .iter()
        .map(|line| (LineRole::Primary, line))
        .chain(
            block
                .secondary_line
                .iter()
                .map(|line| (LineRole::Secondary, line)),
        )
}

#[cfg(windows)]
fn caption_blocks_contain_cjk_style(blocks: &[super::types::CaptionBlock]) -> bool {
    blocks.iter().any(|block| {
        FontLanguageBucket::for_text(block.primary_language.as_deref(), &block.primary_text)
            != FontLanguageBucket::General
            || (block.secondary_enabled
                && FontLanguageBucket::for_text(
                    block.secondary_language.as_deref(),
                    &block.secondary_text,
                ) != FontLanguageBucket::General)
    })
}

fn prepare_layout_for_render(
    previous_layout: &mut Option<ResolvedFrameLayout>,
    mut layout: ResolvedFrameLayout,
) -> ResolvedFrameLayout {
    layout.damage_band = compute_damage_band(previous_layout.as_ref(), &layout);
    *previous_layout = Some(layout.clone());
    layout
}

fn style_bucket_source_counts(layout: &ResolvedFrameLayout) -> Vec<StyleBucketSourceCount> {
    let mut counts = [[0u32; 3]; 5];
    for block in &layout.visible_blocks {
        for line in block
            .primary_lines
            .iter()
            .chain(block.secondary_line.iter())
            .filter(|line| !line.text.trim().is_empty())
        {
            counts[bucket_index(line.style_key.bucket)][source_index(line.style_key.source)] += 1;
        }
    }

    let buckets = [
        FontLanguageBucket::General,
        FontLanguageBucket::CjkKo,
        FontLanguageBucket::CjkJa,
        FontLanguageBucket::CjkZhHans,
        FontLanguageBucket::CjkZhHant,
    ];
    let sources = [
        FontSource::BundledNotoCjkMedium,
        FontSource::SystemFont,
        FontSource::SystemFallbackSentinel,
    ];
    let mut out = Vec::new();
    for (bucket_i, bucket) in buckets.into_iter().enumerate() {
        for (source_i, source) in sources.into_iter().enumerate() {
            let count = counts[bucket_i][source_i];
            if count > 0 {
                out.push(StyleBucketSourceCount {
                    bucket,
                    source,
                    count,
                });
            }
        }
    }
    out
}

fn bucket_index(bucket: FontLanguageBucket) -> usize {
    match bucket {
        FontLanguageBucket::General => 0,
        FontLanguageBucket::CjkKo => 1,
        FontLanguageBucket::CjkJa => 2,
        FontLanguageBucket::CjkZhHans => 3,
        FontLanguageBucket::CjkZhHant => 4,
    }
}

fn source_index(source: FontSource) -> usize {
    match source {
        FontSource::BundledNotoCjkMedium => 0,
        FontSource::SystemFont => 1,
        FontSource::SystemFallbackSentinel => 2,
    }
}

fn compute_damage_band(
    previous_layout: Option<&ResolvedFrameLayout>,
    next_layout: &ResolvedFrameLayout,
) -> Option<DamageBand> {
    let Some(previous_layout) = previous_layout else {
        return Some(DamageBand {
            top_px: 0.0,
            bottom_px: next_layout.surface_height_px as f32,
        });
    };

    if previous_layout.surface_width_px != next_layout.surface_width_px
        || previous_layout.surface_height_px != next_layout.surface_height_px
    {
        return Some(DamageBand {
            top_px: 0.0,
            bottom_px: next_layout.surface_height_px as f32,
        });
    }

    let previous_bounds = previous_layout
        .visible_blocks
        .iter()
        .map(|block| {
            (
                block.id.as_str(),
                (
                    block.visual_bounds.as_block_bounds(),
                    &block.layout_cache_key,
                ),
            )
        })
        .collect::<std::collections::HashMap<_, _>>();
    let next_bounds = next_layout
        .visible_blocks
        .iter()
        .map(|block| {
            (
                block.id.as_str(),
                (
                    block.visual_bounds.as_block_bounds(),
                    &block.layout_cache_key,
                ),
            )
        })
        .collect::<std::collections::HashMap<_, _>>();

    let mut changed_bounds = Vec::new();
    for (id, (bounds, layout_key)) in &previous_bounds {
        match next_bounds.get(id) {
            Some((next_bounds, next_layout_key))
                if next_bounds == bounds && next_layout_key == layout_key => {}
            Some((next_bounds, _)) => {
                changed_bounds.push(*bounds);
                changed_bounds.push(*next_bounds);
            }
            None => changed_bounds.push(*bounds),
        }
    }
    for (id, (bounds, _)) in &next_bounds {
        if !previous_bounds.contains_key(id) {
            changed_bounds.push(*bounds);
        }
    }

    DamageBand::from_bounds(changed_bounds).map(|damage_band| {
        expand_damage_band_for_render(damage_band, next_layout.surface_height_px)
    })
}

fn expand_damage_band_for_render(damage_band: DamageBand, surface_height_px: u32) -> DamageBand {
    let surface_bottom_px = surface_height_px as f32;
    DamageBand {
        top_px: (damage_band.top_px - DAMAGE_BAND_SAFETY_MARGIN_PX).clamp(0.0, surface_bottom_px),
        bottom_px: (damage_band.bottom_px + DAMAGE_BAND_SAFETY_MARGIN_PX)
            .clamp(0.0, surface_bottom_px),
    }
}

#[cfg_attr(not(windows), allow(dead_code))]
fn bounds_intersect_damage_band(bounds: BlockBounds, damage_band: DamageBand) -> bool {
    bounds.bottom_px >= damage_band.top_px && bounds.top_px <= damage_band.bottom_px
}

#[cfg(test)]
mod tests {
    use super::prepare_layout_for_render;
    use crate::renderer::{
        BlockBounds, CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionLayoutPolicy,
        CaptionPresentation, FontLanguageBucket, FontResolver, FontSource, LayoutCacheKey,
        LineRole, ResolvedBlockLayout, ResolvedFrameLayout, ResolvedLineLayout, TextStyleKey,
        VisualBounds,
    };

    fn style_key(language: &str) -> TextStyleKey {
        FontResolver::with_bundle_available()
            .resolve(Some(language), "漢字")
            .style_key()
    }

    fn layout_key(seed: &str) -> LayoutCacheKey {
        LayoutCacheKey {
            primary_text: seed.to_string(),
            secondary_text: String::new(),
            primary_style_key: style_key("ko"),
            secondary_style_key: style_key("ja"),
            channel: Some(CaptionChannel::PeerChannel),
            block_variant: CaptionBlockVariant::Finalized,
            secondary_enabled: false,
            secondary_reserved: false,
            primary_font_size_key: 132,
            secondary_font_size_key: 82,
            content_width_key: 1024,
            text_scale_key: 1000,
        }
    }

    fn block(id: &str, top_px: f32, bottom_px: f32, key_seed: &str) -> ResolvedBlockLayout {
        let bounds = BlockBounds::new(100.0, top_px, 800.0, bottom_px);
        ResolvedBlockLayout {
            id: id.to_string(),
            layout_cache_key: layout_key(key_seed),
            channel: Some(CaptionChannel::PeerChannel),
            block_variant: CaptionBlockVariant::Finalized,
            primary_lines: Vec::new(),
            secondary_line: None,
            secondary_reserved: false,
            bounds,
            visual_bounds: VisualBounds::new(
                bounds.left_px,
                bounds.top_px,
                bounds.right_px,
                bounds.bottom_px,
            ),
            content_width_px: 700.0,
            opacity: 1.0,
            render_offset_y_px: 0.0,
            render_height_scale: 1.0,
            truncated_primary: false,
            truncated_secondary: false,
        }
    }

    fn frame(blocks: Vec<ResolvedBlockLayout>, height_px: u32) -> ResolvedFrameLayout {
        ResolvedFrameLayout {
            visible_blocks: blocks,
            dropped_block_ids: Vec::new(),
            surface_width_px: 1024,
            surface_height_px: height_px,
            damage_band: None,
        }
    }

    #[test]
    fn changed_damage_band_expands_by_safety_margin() {
        let mut previous_layout = Some(frame(vec![block("peer:1", 100.0, 200.0, "old")], 500));

        let rendered = prepare_layout_for_render(
            &mut previous_layout,
            frame(vec![block("peer:1", 120.0, 220.0, "new")], 500),
        );

        let damage_band = rendered.damage_band.expect("changed block should damage");
        assert_eq!(damage_band.top_px, 68.0);
        assert_eq!(damage_band.bottom_px, 252.0);
    }

    #[test]
    fn expanded_damage_band_clamps_to_surface_bounds() {
        let mut previous_layout = Some(frame(vec![block("peer:1", 4.0, 16.0, "old")], 40));

        let rendered = prepare_layout_for_render(
            &mut previous_layout,
            frame(vec![block("peer:1", 8.0, 24.0, "new")], 40),
        );

        let damage_band = rendered.damage_band.expect("changed block should damage");
        assert_eq!(damage_band.top_px, 0.0);
        assert_eq!(damage_band.bottom_px, 40.0);
    }

    #[test]
    fn expanded_damage_band_clamps_fully_offscreen_top_bounds() {
        let mut previous_layout = Some(frame(vec![block("peer:1", -120.0, -80.0, "old")], 100));

        let rendered = prepare_layout_for_render(
            &mut previous_layout,
            frame(vec![block("peer:1", -110.0, -90.0, "new")], 100),
        );

        let damage_band = rendered.damage_band.expect("changed block should damage");
        assert_eq!(damage_band.top_px, 0.0);
        assert_eq!(damage_band.bottom_px, 0.0);
    }

    #[test]
    fn expanded_damage_band_clamps_fully_offscreen_bottom_bounds() {
        let mut previous_layout = Some(frame(vec![block("peer:1", 180.0, 220.0, "old")], 100));

        let rendered = prepare_layout_for_render(
            &mut previous_layout,
            frame(vec![block("peer:1", 190.0, 230.0, "new")], 100),
        );

        let damage_band = rendered.damage_band.expect("changed block should damage");
        assert_eq!(damage_band.top_px, 100.0);
        assert_eq!(damage_band.bottom_px, 100.0);
    }

    #[test]
    fn first_damage_band_remains_full_surface() {
        let mut previous_layout = None;

        let rendered = prepare_layout_for_render(
            &mut previous_layout,
            frame(vec![block("peer:1", 100.0, 200.0, "new")], 500),
        );

        let damage_band = rendered.damage_band.expect("first frame should damage");
        assert_eq!(damage_band.top_px, 0.0);
        assert_eq!(damage_band.bottom_px, 500.0);
    }

    #[cfg(windows)]
    #[test]
    fn first_cjk_layout_failure_does_not_consume_success_diagnostic() {
        let mut logged = false;

        assert_eq!(
            super::first_cjk_layout_diagnostic_outcome(Some(()), logged, false),
            super::FirstCjkLayoutDiagnosticOutcome::Failure
        );
        assert!(!logged);

        let outcome = super::first_cjk_layout_diagnostic_outcome(Some(()), logged, true);
        if outcome == super::FirstCjkLayoutDiagnosticOutcome::Success {
            logged = true;
        }

        assert_eq!(outcome, super::FirstCjkLayoutDiagnosticOutcome::Success);
        assert!(logged);
        assert_eq!(
            super::first_cjk_layout_diagnostic_outcome(Some(()), logged, true),
            super::FirstCjkLayoutDiagnosticOutcome::AlreadyLogged
        );
    }

    #[cfg(windows)]
    #[test]
    fn production_font_resolver_construction_applies_ui_language_hint_to_bundle_states() {
        let available = super::font_resolver_with_bundle_available(Some("ja-JP".to_string()));
        assert_eq!(
            available.resolve(None, "日本語").bucket,
            FontLanguageBucket::CjkJa
        );

        let unavailable = super::font_resolver_with_bundle_unavailable(
            "missing bundled TTC".to_string(),
            Some("zh-HK".to_string()),
        );
        assert_eq!(
            unavailable.resolve(Some("x-madeup"), "繁體").bucket,
            FontLanguageBucket::CjkZhHant
        );
    }

    #[cfg(windows)]
    #[test]
    fn line_cache_key_and_text_format_key_share_resolved_style_identity() {
        let renderer = super::WindowsCaptionRenderer::new()
            .expect("Windows caption renderer should initialize for style-key test");
        let policy = CaptionLayoutPolicy::default();
        let layout = policy
            .resolve_blocks_for_presentation_windows_cached(
                vec![CaptionBlock::new("latin", "hello style identity")],
                3840,
                1024,
                &CaptionPresentation::default(),
                &renderer.layout_engine,
                None,
            )
            .expect("DirectWrite layout should initialize on Windows");

        let block = &layout.visible_blocks[0];
        let line = block
            .primary_lines
            .first()
            .expect("primary line should be present");
        let resolved_style_key = renderer.resolved_text_style_key_for_test(&policy, &line.text);
        let line_cache_key = renderer.line_cache_key(block, line, line.role);
        let text_format_key =
            renderer.text_format_cache_key_for_test(&policy, &line.text, line.font_size_px);

        assert_eq!(block.layout_cache_key.primary_style_key, resolved_style_key);
        assert_eq!(line.style_key, resolved_style_key);
        assert_eq!(line_cache_key.style_key, resolved_style_key);
        assert_eq!(text_format_key.0, resolved_style_key);
    }

    #[cfg(windows)]
    #[test]
    fn line_text_format_cache_key_uses_measured_line_style_without_reresolving_text() {
        let renderer = super::WindowsCaptionRenderer::new()
            .expect("Windows caption renderer should initialize for style-key test");
        let policy = CaptionLayoutPolicy::default();
        let resolved_line_style =
            FontResolver::with_bundle_unavailable("test style").resolve(Some("ja"), "日本語");
        let line_style_key = resolved_line_style.style_key();
        let line_style = crate::renderer::TextStyleDescriptor::from_parts(
            resolved_line_style.family_name,
            resolved_line_style.weight,
            resolved_line_style.locale,
            resolved_line_style.source,
            resolved_line_style.bucket,
            line_style_key,
        );
        let line = ResolvedLineLayout {
            text: "plain latin text".into(),
            role: LineRole::Primary,
            style_key: line_style_key,
            style: line_style,
            width_px: 120.0,
            origin_x: 0.0,
            origin_y: 0.0,
            font_size_px: 132.0,
            visual_bounds: VisualBounds::new(0.0, 0.0, 120.0, 150.0),
        };

        assert_ne!(
            renderer
                .text_format_cache_key_for_test(&policy, &line.text, line.font_size_px)
                .0,
            line.style_key,
            "test setup requires text-only re-resolution to differ from measured style"
        );
        assert_eq!(
            renderer.text_format_cache_key_for_line_test(&line).0,
            line.style_key
        );
    }

    #[cfg(windows)]
    #[test]
    fn bundled_source_without_collection_routes_to_system_fallback_before_text_format_creation() {
        assert_eq!(
            super::text_format_collection_route_for_test(FontSource::BundledNotoCjkMedium, false),
            super::TextFormatCollectionRoute::FallbackToSystem
        );
        assert_eq!(
            super::text_format_collection_route_for_test(FontSource::BundledNotoCjkMedium, true),
            super::TextFormatCollectionRoute::Bundled
        );
        assert_eq!(
            super::text_format_collection_route_for_test(FontSource::SystemFont, false),
            super::TextFormatCollectionRoute::System
        );
    }
}

#[cfg(windows)]
fn resolve_family_weight(
    family: &IDWriteFontFamily,
    policy: &CaptionLayoutPolicy,
) -> Result<Option<DWRITE_FONT_WEIGHT>, CaptionRenderError> {
    for weight in preferred_weight_chain(policy) {
        let font = unsafe {
            family
                .GetFirstMatchingFont(weight, DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if unsafe { font.GetWeight() } == weight {
            return Ok(Some(weight));
        }
    }
    Ok(None)
}

#[cfg(windows)]
fn preferred_weight_chain(policy: &CaptionLayoutPolicy) -> Vec<DWRITE_FONT_WEIGHT> {
    policy
        .preferred_weights()
        .into_iter()
        .map(|weight| match weight {
            "Semibold" => DWRITE_FONT_WEIGHT_SEMI_BOLD,
            "Medium" => DWRITE_FONT_WEIGHT_MEDIUM,
            _ => DWRITE_FONT_WEIGHT_NORMAL,
        })
        .collect()
}

#[cfg(windows)]
fn utf16_null(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

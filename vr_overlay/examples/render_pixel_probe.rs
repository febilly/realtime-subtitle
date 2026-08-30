//! 诊断工具: 渲染一帧字幕到纹理, 再用 D3D11 读回像素, 验证文字是否真的画进纹理。
//! 运行: cargo run --example render_pixel_probe --release
#![cfg(windows)]

use rinbridge_overlay::{CaptionBlock, CaptionRenderer};

fn read_texture_pixels(renderer: &CaptionRenderer, frame: &rinbridge_overlay::RenderedFrame) {
    let Some(texture) = frame.d3d11_texture() else {
        println!("no d3d11 texture");
        return;
    };
    let width = frame.width();
    let height = frame.height();

    let desc = unsafe {
        let mut desc = std::mem::zeroed();
        texture.GetDesc(&mut desc);
        desc
    };
    println!(
        "texture desc: {}x{} format={:?}",
        desc.Width, desc.Height, desc.Format
    );

    // 从纹理拿回所属设备
    let device = unsafe { texture.GetDevice().expect("device") };
    let ctx = unsafe { device.GetImmediateContext().expect("ctx") };

    let staging_desc = windows::Win32::Graphics::Direct3D11::D3D11_TEXTURE2D_DESC {
        Width: desc.Width,
        Height: desc.Height,
        MipLevels: 1,
        ArraySize: 1,
        Format: desc.Format,
        SampleDesc: windows::Win32::Graphics::Dxgi::Common::DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        Usage: windows::Win32::Graphics::Direct3D11::D3D11_USAGE_STAGING,
        BindFlags: 0,
        CPUAccessFlags: windows::Win32::Graphics::Direct3D11::D3D11_CPU_ACCESS_READ.0 as u32,
        MiscFlags: 0,
    };
    let mut staging: Option<windows::Win32::Graphics::Direct3D11::ID3D11Texture2D> = None;
    unsafe {
        device
            .CreateTexture2D(&staging_desc, None, Some(&mut staging))
            .expect("create staging");
    }
    let staging = staging.expect("staging tex");
    unsafe {
        ctx.CopyResource(&staging, texture);
    }

    let mut mapped = windows::Win32::Graphics::Direct3D11::D3D11_MAPPED_SUBRESOURCE::default();
    let map_result = unsafe {
        ctx.Map(
            &staging,
            0,
            windows::Win32::Graphics::Direct3D11::D3D11_MAP_READ,
            0,
            Some(&mut mapped),
        )
    };
    map_result.expect("map");
    let row_pitch = mapped.RowPitch as usize;
    let bytes = unsafe {
        std::slice::from_raw_parts(mapped.pData as *const u8, row_pitch * height as usize)
    };

    // 统计 alpha 桶 + 非零像素, 抽样几个文字区域
    let mut counts = [0u64; 5];
    let mut nonzero = 0u64;
    let mut max_alpha = 0u8;
    let mut max_rgb = 0u8;
    for y in 0..height as usize {
        let row = &bytes[y * row_pitch..y * row_pitch + width as usize * 4];
        for px in row.chunks_exact(4) {
            let a = px[3];
            let bucket = match a {
                0 => 0,
                1..=63 => 1,
                64..=127 => 2,
                128..=254 => 3,
                _ => 4,
            };
            counts[bucket] += 1;
            if a > 0 {
                nonzero += 1;
                max_alpha = max_alpha.max(a);
            }
            max_rgb = max_rgb.max(px[0]).max(px[1]).max(px[2]);
        }
    }
    unsafe {
        ctx.Unmap(&staging, 0);
    }
    let total = width as u64 * height as u64;
    println!(
        "pixels: {}x{} total={} alpha_buckets(0,1-63,64-127,128-254,255)={:?} nonzero={} max_alpha={} max_rgb={}",
        width, height, total, counts, nonzero, max_alpha, max_rgb
    );
}

fn main() {
    let renderer = CaptionRenderer::new().expect("renderer");

    // 第一帧: 中英双语字幕 (与真实快照一致: 主行=译文中文, 副行=原文英文)
    let blocks = vec![
        CaptionBlock::new("rt:1", "这是一个字幕识别测试")
            .with_primary_language("zh-CN")
            .with_secondary_text("This is a subtitle recognition test.", true)
            .with_secondary_language("en"),
    ];
    let frame = renderer.render_blocks(blocks).expect("render frame 1");
    println!("frame1: {}x{} transparent={}", frame.width(), frame.height(), frame.is_fully_transparent());
    read_texture_pixels(&renderer, &frame);

    // 第二帧: 换一句话 (模拟"卡在第一个句子"场景: 后续帧是否还能更新)
    let blocks2 = vec![
        CaptionBlock::new("rt:2", "快速棕色狐狸跳过了懒狗")
            .with_primary_language("zh-CN")
            .with_secondary_text("The quick brown fox jumps over the lazy dog.", true)
            .with_secondary_language("en"),
    ];
    let frame2 = renderer.render_blocks(blocks2).expect("render frame 2");
    println!(
        "frame2: {}x{} transparent={}",
        frame2.width(),
        frame2.height(),
        frame2.is_fully_transparent()
    );
    read_texture_pixels(&renderer, &frame2);
}

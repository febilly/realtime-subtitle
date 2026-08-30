//! DirectWrite 字体格式/布局探针 — 定位"英文文本冻结"的环境级复现。
//! 单独跑: cargo test --test dwrite_probe -- --nocapture

#![cfg(windows)]

use windows::core::PCWSTR;
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteTextFormat, DWRITE_FACTORY_TYPE_SHARED,
    DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT,
    DWRITE_WORD_WRAPPING_NO_WRAP,
};

fn dwrite_factory() -> IDWriteFactory {
    unsafe { DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED).expect("factory") }
}

fn utf16(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn make_format(
    factory: &IDWriteFactory,
    face: &str,
    weight: i32,
    size: f32,
    locale: &str,
) -> windows::core::Result<IDWriteTextFormat> {
    let face_u = utf16(face);
    let loc_u = utf16(locale);
    unsafe {
        factory.CreateTextFormat(
            PCWSTR::from_raw(face_u.as_ptr()),
            None,
            DWRITE_FONT_WEIGHT(weight),
            DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL,
            size,
            PCWSTR::from_raw(loc_u.as_ptr()),
        )
    }
}

fn probe_format(face: &str, weight: i32, size: f32, locale: &str) {
    println!(">> format face={face:?} weight={weight} size={size} locale={locale:?}");
    let factory = dwrite_factory();
    let format = make_format(&factory, face, weight, size, locale).expect("format");
    println!("<< format ok");
    drop(format);
}

/// Returns Some(true/false) when layout creation completed, None when the
/// format itself was rejected (e.g. garbage locale -> E_INVALIDARG).
fn probe_layout(face: &str, weight: i32, size: f32, locale: &str, text: &str) -> Option<bool> {
    println!(">> layout face={face:?} weight={weight} locale={locale:?} text={text:?}");
    let factory = dwrite_factory();
    let format = match make_format(&factory, face, weight, size, locale) {
        Ok(format) => format,
        Err(err) => {
            println!("<< format rejected: {err:?}");
            return None;
        }
    };
    let text_u = utf16(text);
    let text_slice = &text_u[..text_u.len() - 1];
    let layout = unsafe {
        factory.CreateTextLayout(text_slice, &format, 2000.0, 1000.0)
    };
    let ok = layout.is_ok();
    println!("<< layout ok={ok}");
    if let Ok(layout) = layout {
        // 触发实际测量 (标准两段式: 先查行数, 再取数据)
        unsafe {
            let mut count: u32 = 0;
            let _ = layout.GetLineMetrics(None, &mut count);
            let mut buf = vec![windows::Win32::Graphics::DirectWrite::DWRITE_LINE_METRICS::default(); count as usize];
            let _ = layout.GetLineMetrics(Some(&mut buf), &mut count);
        }
        println!("<< line metrics queried");
    }
    Some(ok)
}

#[test]
fn dwrite_format_probe_matrix() {
    probe_format("Segoe UI", 600, 132.0, "en-US");
    probe_format("Segoe UI", 400, 132.0, "en-US");
    probe_format("Microsoft YaHei UI", 600, 132.0, "zh-CN");
}

#[test]
fn dwrite_layout_probe_matrix() {
    probe_layout("Segoe UI", 600, 132.0, "en-US", "Hello world");
    probe_layout("Segoe UI", 600, 132.0, "en", "Lyrics line here");
    // 合法 locale 必须成功创建 layout
    assert_eq!(probe_layout("Segoe UI", 600, 132.0, "en-US", "Hello world"), Some(true));
    assert_eq!(probe_layout("Segoe UI", 600, 132.0, "en", "Lyrics line here"), Some(true));
    // 垃圾 locale (线上出现的 Other(616491804752692906) 形态):
    // DirectWrite 应以错误拒绝 CreateTextFormat, 探针不得 panic ——
    // 生产路径必须在传入前校验/fallback locale。
    assert_eq!(
        probe_layout("Segoe UI", 600, 132.0, "616491804752692906", "garbage locale"),
        None
    );
    // CJK 对照
    assert_eq!(
        probe_layout("Microsoft YaHei UI", 600, 132.0, "zh-CN", "中文字幕测试"),
        Some(true)
    );
}

//! 诊断工具: 查询 RinBridge overlay 在 OpenVR 侧的实时状态。
//! 运行: cargo run --example overlay_probe --release [overlay_key]
#![cfg(windows)]

use std::ffi::CString;

use openvr_sys::*;

fn main() {
    let key = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "com.rinbridge.overlay.realtime-subtitle-30072".to_string());

    let mut init_error = EVRInitError_VRInitError_None;
    unsafe {
        VR_InitInternal(&mut init_error, EVRApplicationType_VRApplication_Overlay);
    }
    if init_error != EVRInitError_VRInitError_None {
        println!("VR_InitInternal failed: {init_error}");
        return;
    }

    let mut version_bytes: Vec<u8> = b"FnTable:".to_vec();
    version_bytes.extend_from_slice(IVROverlay_Version);

    let mut interface_error = EVRInitError_VRInitError_None;
    let overlay_api = unsafe {
        VR_GetGenericInterface(version_bytes.as_ptr().cast(), &mut interface_error)
            as *mut VR_IVROverlay_FnTable
    };
    if overlay_api.is_null() {
        println!(
            "{} unavailable (error {interface_error})",
            String::from_utf8_lossy(&version_bytes)
        );
        unsafe { VR_ShutdownInternal() };
        return;
    }
    probe(unsafe { &*overlay_api }, &key);
    unsafe { VR_ShutdownInternal() };
}

fn probe(api: &VR_IVROverlay_FnTable, key: &str) {
    let key_c = CString::new(key).expect("key");
    let mut handle: VROverlayHandle_t = 0;
    let find = unsafe { (api.FindOverlay.expect("FindOverlay"))(key_c.as_ptr().cast_mut(), &mut handle) };
    if find != EVROverlayError_VROverlayError_None {
        println!("FindOverlay failed: {find} (key={key})");
        return;
    }
    println!("overlay handle: 0x{handle:X}");

    let visible = unsafe { (api.IsOverlayVisible.expect("IsOverlayVisible"))(handle) };
    println!("IsOverlayVisible -> visible={visible}");

    for (name, flag) in [
        ("IsPremultiplied", VROverlayFlags_IsPremultiplied),
        ("VisibleInDashboard", VROverlayFlags_VisibleInDashboard),
        ("NoDashboardTab", VROverlayFlags_NoDashboardTab),
    ] {
        let mut set = false;
        let err = unsafe { (api.GetOverlayFlag.expect("GetOverlayFlag"))(handle, flag, &mut set) };
        println!("GetOverlayFlag({name}) -> {set} err={err}");
    }

    let mut transform_type = VROverlayTransformType_VROverlayTransform_Invalid;
    let tt_err = unsafe {
        (api.GetOverlayTransformType.expect("GetOverlayTransformType"))(handle, &mut transform_type)
    };
    println!("GetOverlayTransformType -> {transform_type:?} err={tt_err}");

    let mut device = 0u32;
    let mut matrix = HmdMatrix34_t { m: [[0.0; 4]; 3] };
    let td_err = unsafe {
        (api.GetOverlayTransformTrackedDeviceRelative.expect("GetOverlayTransformTrackedDeviceRelative"))(
            handle,
            &mut device,
            &mut matrix,
        )
    };
    println!("TrackedDeviceRelative -> device={device} err={td_err}");
    for row in matrix.m {
        println!(
            "    [{:.3}, {:.3}, {:.3}, {:.3}]",
            row[0], row[1], row[2], row[3]
        );
    }

    let mut width = 0.0f32;
    let w_err = unsafe { (api.GetOverlayWidthInMeters.expect("GetOverlayWidthInMeters"))(handle, &mut width) };
    println!("GetOverlayWidthInMeters -> {width:.4} err={w_err}");

    let mut alpha = 0.0f32;
    let a_err = unsafe { (api.GetOverlayAlpha.expect("GetOverlayAlpha"))(handle, &mut alpha) };
    println!("GetOverlayAlpha -> {alpha:.4} err={a_err}");

    // 读取纹理像素, 统计 alpha 分布, 判断 overlay 内容是否透明
    let mut width = 0u32;
    let mut height = 0u32;
    let size_err = unsafe {
        (api.GetOverlayImageData.expect("GetOverlayImageData"))(
            handle,
            std::ptr::null_mut(),
            0,
            &mut width,
            &mut height,
        )
    };
    println!("GetOverlayImageData sizes -> {width}x{height} err={size_err}");
    if width > 0 && height > 0 {
        let size = width as usize * height as usize * 4;
        let mut buf = vec![0u8; size];
        let mut out_w = 0u32;
        let mut out_h = 0u32;
        let data_err = unsafe {
            (api.GetOverlayImageData.expect("GetOverlayImageData"))(
                handle,
                buf.as_mut_ptr().cast(),
                size as u32,
                &mut out_w,
                &mut out_h,
            )
        };
        println!("GetOverlayImageData read -> {out_w}x{out_h} err={data_err} errno={}", data_err as i32);
        if data_err == EVROverlayError_VROverlayError_None {
            let mut counts = [0u64; 5]; // alpha buckets: 0, 1-63, 64-127, 128-254, 255
            let mut max_rgb = 0u32;
            for px in buf.chunks_exact(4) {
                let a = px[3] as usize;
                let bucket = match a {
                    0 => 0,
                    1..=63 => 1,
                    64..=127 => 2,
                    128..=254 => 3,
                    _ => 4,
                };
                counts[bucket] += 1;
                let rgb = (px[0] as u32).max(px[1] as u32).max(px[2] as u32);
                if rgb > max_rgb {
                    max_rgb = rgb;
                }
            }
            let total = width as u64 * height as u64;
            println!(
                "pixels: {}x{} total={} alpha_buckets(0,1-63,64-127,128-254,255)={:?} max_rgb={}",
                width, height, total, counts, max_rgb
            );
        }
    }
}

#![allow(dead_code)]

use futures_util::SinkExt;
use rinbridge_overlay::transcript::{
    CaptionEvent, CommittedSource, LiveSourceSnapshot, Refinement, SpeakerKey, TargetUpdate,
};
use rinbridge_overlay::{
    OverlayCalibration, OverlayLoggingMode, OverlayManifest, VrViewSettings,
    EXPECTED_CONTRACT_VERSION,
};

pub fn test_manifest_with_url(url: &str) -> OverlayManifest {
    OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "overlay-test".into(),
        bridge_url: url.into(),
        parent_pid: 1,
        startup_deadline_ms: 3000,
        log_dir: std::env::temp_dir()
            .join("rinbridge-overlay-tests")
            .display()
            .to_string(),
        log_level: "INFO".into(),
        locale: "en".into(),
        logging_mode: OverlayLoggingMode::Basic,
        view_settings: VrViewSettings::default(),
        calibration: OverlayCalibration::default(),
    }
}

/// Binds a loopback TcpListener, accepts one WebSocket client, sends each frame
/// as a Text message, then closes.
pub fn spawn_scripted_server(frames: Vec<String>) -> (String, tokio::task::JoinHandle<()>) {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let listener = tokio::net::TcpListener::from_std(listener).unwrap();
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
        for frame in frames {
            ws.send(tokio_tungstenite::tungstenite::Message::Text(frame.into()))
                .await
                .unwrap();
        }
        ws.close(None).await.ok();
    });
    (format!("ws://{addr}/ws"), handle)
}

pub fn src_live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: None,
        text: text.into(),
        language: Some("en".into()),
    })
}

pub fn src_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("en".into()),
    })
}

pub fn src_end(speaker: &str, id: Option<&str>) -> CaptionEvent {
    CaptionEvent::SourceEnd {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: id.map(str::to_owned),
    }
}

pub fn target_draft(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

pub fn target_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

pub fn refine(id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::RefinedTarget(Refinement {
        sentence_id: id.into(),
        text: text.into(),
        language: Some("fr".into()),
    })
}

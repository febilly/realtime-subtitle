mod support;

use rinbridge_overlay::protocol::{BridgeError, DesktopProtocol};
use rinbridge_overlay::transcript::{CaptionEvent, SpeakerKey};
use rinbridge_overlay::views::DisplayMode;
use support::{spawn_scripted_server, test_manifest_with_url};

fn ts_delta(text: &str, id: &str) -> String {
    format!(
        "{{\"type\":\"update\",\"final_tokens\":[{{\"text\":\"{text}\",\"speaker\":\"1\",\"translation_status\":\"original\",\"llm_sentence_id\":\"{id}\",\"is_final\":true}}],\"non_final_tokens\":[]}}"
    )
}

async fn adapter_events(frames: Vec<String>) -> Result<Vec<CaptionEvent>, BridgeError> {
    let (url, server) = spawn_scripted_server(frames);
    let manifest = test_manifest_with_url(&url);
    let mut protocol = DesktopProtocol::connect(&manifest).await?;
    let mut out = Vec::new();
    loop {
        match protocol.next_event().await {
            Ok(event) => out.push(event),
            Err(BridgeError::Disconnected) => break,
            Err(error) => return Err(error),
        }
    }
    server.await.unwrap();
    Ok(out)
}

fn committed_texts(events: &[CaptionEvent]) -> Vec<String> {
    events
        .iter()
        .filter_map(|event| match event {
            CaptionEvent::SourceCommitted(committed) => Some(committed.text.clone()),
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn one_frame_can_yield_multiple_events_through_the_pending_queue() {
    let frames = vec![r#"{"type":"update","final_tokens":[
        {"text":"hi","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
        {"text":"salut","speaker":"1","translation_status":"translation","llm_sentence_id":"A","is_final":true}],
        "non_final_tokens":[{"text":"live","speaker":"2","translation_status":"original","is_final":false}]}"#
        .to_string()];
    let (url, server) = spawn_scripted_server(frames);
    let manifest = test_manifest_with_url(&url);
    let mut protocol = DesktopProtocol::connect(&manifest).await.unwrap();
    let first = protocol.next_event().await.unwrap();
    let mut seen = vec![first];
    for _ in 0..2 {
        seen.push(
            protocol
                .try_next_event()
                .await
                .expect("queued event")
                .unwrap(),
        );
    }
    assert!(seen
        .iter()
        .any(|event| matches!(event, CaptionEvent::SourceCommitted(_))));
    assert!(seen
        .iter()
        .any(|event| matches!(event, CaptionEvent::TargetCommitted(_))));
    assert!(seen
        .iter()
        .any(|event| matches!(event, CaptionEvent::SourceLive(_))));
    let extra = protocol.try_next_event().await;
    assert!(
        extra.is_none() || matches!(extra, Some(Err(_))),
        "no fourth caption event"
    );
    server.await.unwrap();
}

#[tokio::test]
async fn final_source_true_delta_appends_and_replay_dedupes() {
    let frames = vec![
        ts_delta("very", "A"),
        ts_delta("very", "A"),
        r#"{"type":"update","final_tokens":[
            {"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
            {"text":"very","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],
            "non_final_tokens":[]}"#
            .to_string(),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert_eq!(
        committed_texts(&events),
        vec!["very".to_string(), "veryvery".to_string()]
    );
}

#[tokio::test]
async fn final_tokens_segment_by_sentence_speaker_and_language() {
    let frames = vec![r#"{"type":"update","final_tokens":[
        {"text":"hello ","speaker":"1","translation_status":"original","llm_sentence_id":"A","language":"en","is_final":true},
        {"text":"bonjour ","speaker":"2","translation_status":"original","llm_sentence_id":"B","language":"en","is_final":true}],
        "non_final_tokens":[]}"#
        .to_string()];
    let events = adapter_events(frames).await.unwrap();
    let mut by_id = std::collections::BTreeMap::new();
    for event in &events {
        if let CaptionEvent::SourceCommitted(committed) = event {
            by_id.insert(
                committed.sentence_id.clone().unwrap(),
                committed.text.clone(),
            );
        }
    }
    assert_eq!(by_id.get("A").map(String::as_str), Some("hello "));
    assert_eq!(by_id.get("B").map(String::as_str), Some("bonjour "));
}

#[tokio::test]
async fn separator_replaces_each_same_track_accumulator_independently() {
    let frames = vec![
        r#"{"type":"update","final_tokens":[
            {"text":"old-a","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true}],"non_final_tokens":[]}"#.to_string(),
        r#"{"type":"update","final_tokens":[
            {"text":"old-b","speaker":"2","translation_status":"original","llm_sentence_id":"B","is_final":true}],"non_final_tokens":[]}"#.to_string(),
        r#"{"type":"update","final_tokens":[
            {"is_separator":true,"is_final":true},
            {"text":"new-a","speaker":"1","translation_status":"original","llm_sentence_id":"A","is_final":true},
            {"text":"new-b","speaker":"2","translation_status":"original","llm_sentence_id":"B","is_final":true}],"non_final_tokens":[]}"#.to_string(),
    ];
    let events = adapter_events(frames).await.unwrap();
    let committed = committed_texts(&events);
    assert!(committed.contains(&"new-a".to_string()));
    assert!(committed.contains(&"new-b".to_string()));
    assert!(!committed.contains(&"old-bnew-b".to_string()));
}

#[tokio::test]
async fn multi_speaker_non_final_emits_one_live_event_for_the_last_speaker() {
    let frames = vec![r#"{"type":"update","final_tokens":[],"non_final_tokens":[
        {"text":"from one","speaker":"1","translation_status":"original","is_final":false},
        {"text":"from two","speaker":"2","translation_status":"original","is_final":false}]}"#
        .to_string()];
    let events = adapter_events(frames).await.unwrap();
    let live: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            CaptionEvent::SourceLive(snapshot) => Some(snapshot.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(live.len(), 1);
    assert_eq!(live[0].speaker, SpeakerKey::Diarized("2".into()));
    assert_eq!(live[0].text, "from two");
}

#[tokio::test]
async fn refinement_maps_to_a_sentence_keyed_event() {
    let frames = vec![r#"{"type":"refine_result","sentence_id":"A","source":"src","original_translation":"draft","refined_translation":"refined","no_change":false}"#
        .to_string()];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|event| matches!(
        event,
        CaptionEvent::RefinedTarget(refinement)
            if refinement.text == "refined" && refinement.sentence_id == "A"
    )));
}

#[tokio::test]
async fn explicit_end_token_becomes_source_end() {
    let frames = vec![r#"{"type":"update","final_tokens":[{"text":"<end>","speaker":"1","translation_status":"original","is_final":true}],"non_final_tokens":[]}"#
        .to_string()];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|event| matches!(
        event,
        CaptionEvent::SourceEnd { speaker, sentence_id: None }
            if *speaker == SpeakerKey::Diarized("1".into())
    )));
}

#[tokio::test]
async fn malformed_frames_are_ignored_without_breaking_state() {
    let frames = vec![
        ts_delta("ok", "A"),
        r#"{"type":"update","final_tokens":"not-an-array"}"#.to_string(),
        "this is not json".to_string(),
        ts_delta(" more", "A"),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert_eq!(
        committed_texts(&events),
        vec!["ok".to_string(), "ok more".to_string()]
    );
}

#[tokio::test]
async fn view_settings_and_clear_map_to_events() {
    let frames = vec![
        r#"{"type":"clear","preserve_existing":true}"#.to_string(),
        r#"{"type":"vr_view_settings","display_mode":"original","max_speakers":2,"bilingual_pair_count":1,"show_speaker_labels":false}"#
            .to_string(),
    ];
    let events = adapter_events(frames).await.unwrap();
    assert!(events.iter().any(|event| matches!(
        event,
        CaptionEvent::Clear {
            preserve_existing: true
        }
    )));
    assert!(events.iter().any(|event| matches!(
        event,
        CaptionEvent::ViewSettingsChanged(settings)
            if settings.display_mode == DisplayMode::Original && settings.max_speakers == 2
    )));
}

#[tokio::test]
async fn shutdown_maps_to_a_control_event_without_touching_transcript_state() {
    let events = adapter_events(vec![r#"{"type":"shutdown"}"#.to_string()])
        .await
        .unwrap();
    assert!(events
        .iter()
        .any(|event| matches!(event, CaptionEvent::Shutdown)));
}

#[tokio::test]
async fn non_ws_manifest_path_is_rejected() {
    let manifest = test_manifest_with_url("ws://127.0.0.1:1/vr_ws");
    assert!(matches!(
        DesktopProtocol::connect(&manifest).await,
        Err(BridgeError::UnsupportedPath(_))
    ));
}

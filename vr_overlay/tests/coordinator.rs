use std::time::{Duration, Instant};

use rinbridge_overlay::hud::HudRowRole;
use rinbridge_overlay::runtime::{
    RuntimeCoordinator, VisibilityController, FADE_DURATION, SILENCE_BEFORE_FADE,
};
use rinbridge_overlay::transcript::{
    CaptionEvent, CommittedSource, LiveInputRow, LiveSourceSnapshot, SpeakerKey, TargetUpdate,
};
use rinbridge_overlay::views::{DisplayMode, VrViewSettings};

#[test]
fn visibility_wakes_with_show_and_alpha_in_one_tick() {
    let t = Instant::now();
    let mut visibility = VisibilityController::new(t);
    visibility.on_activity(t);

    let wake = visibility.tick(t);
    assert_eq!(wake.set_visible, Some(true));
    assert_eq!(wake.alpha, Some(1.0));
    assert!(visibility.is_visible());

    let hide = visibility.tick(t + SILENCE_BEFORE_FADE + FADE_DURATION);
    assert_eq!(hide.alpha, Some(0.0));
    assert_eq!(hide.set_visible, Some(false));
    assert!(!visibility.is_visible());

    let wake_time = t + SILENCE_BEFORE_FADE + Duration::from_millis(1300);
    visibility.on_activity(wake_time);
    let wake = visibility.tick(wake_time);
    assert_eq!(wake.set_visible, Some(true), "wake must show the overlay");
    assert_eq!(wake.alpha, Some(1.0));
    assert!(visibility.is_visible());
}

#[test]
fn visibility_deadline_stops_after_fade_and_rearms_only_on_activity() {
    let t = Instant::now();
    let mut visibility = VisibilityController::new(t);
    visibility.on_activity(t);

    assert_eq!(
        visibility.next_deadline_at(t),
        Some(t + SILENCE_BEFORE_FADE)
    );
    let fade_start = t + SILENCE_BEFORE_FADE;
    visibility.tick(fade_start);
    assert_eq!(
        visibility.next_deadline_at(fade_start),
        Some(fade_start + rinbridge_overlay::runtime::FADE_STEP)
    );

    visibility.tick(t + SILENCE_BEFORE_FADE + FADE_DURATION);
    assert_eq!(
        visibility.next_deadline_at(t + SILENCE_BEFORE_FADE + FADE_DURATION),
        None
    );
}

#[test]
fn clear_without_preserving_content_hides_the_overlay_on_next_tick() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let now = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "caption"), now);
    assert_eq!(coordinator.tick(now).set_visible, Some(true));
    coordinator.push_for_test(
        CaptionEvent::Clear {
            preserve_existing: false,
        },
        now + Duration::from_millis(1),
    );
    let visibility = coordinator.tick(now + Duration::from_millis(1));
    assert_eq!(visibility.set_visible, Some(false));
    assert_eq!(visibility.alpha, Some(0.0));
}

fn speaker(id: &str) -> SpeakerKey {
    SpeakerKey::Diarized(id.into())
}

fn source_live(id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: speaker(id),
        sentence_id: None,
        text: text.into(),
        language: Some("en".into()),
    })
}

fn source_commit(id: &str, sentence_id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: speaker(id),
        sentence_id: Some(sentence_id.into()),
        text: text.into(),
        language: Some("en".into()),
    })
}

fn target_draft(id: &str, sentence_id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: speaker(id),
        sentence_id: Some(sentence_id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

#[test]
fn coordinator_consumes_settled_live_after_visible_handoff() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.push_for_test(source_commit("1", "A", "hello world"), t);
    coordinator.push_for_test(
        target_draft("1", "A", "bonjour"),
        t + Duration::from_millis(500),
    );

    assert_eq!(
        coordinator.next_wake_for_test(t + Duration::from_millis(600)),
        Some(t + Duration::from_millis(1200))
    );
    coordinator.tick_for_test(t + Duration::from_millis(1201));
    coordinator.render_for_test(t + Duration::from_millis(1201));
    assert!(matches!(
        coordinator.transcript_for_test().live_input,
        LiveInputRow::Hidden
    ));
    assert_eq!(
        coordinator.next_wake_for_test(t + Duration::from_millis(1201)),
        None
    );
}

#[test]
fn dismissed_live_row_does_not_resurrect_on_mode_switch() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.push_for_test(source_commit("1", "A", "hello world"), t);
    coordinator.push_for_test(
        target_draft("1", "A", "bonjour"),
        t + Duration::from_millis(500),
    );
    coordinator.tick_for_test(t + Duration::from_millis(1201));
    coordinator.apply_settings_for_test(VrViewSettings {
        display_mode: DisplayMode::Original,
        ..Default::default()
    });
    coordinator.render_for_test(t + Duration::from_millis(1300));
    assert!(coordinator.last_frame_for_test().slots[4].is_none());
}

#[test]
fn hold_expiry_without_handoff_keeps_row_until_target_arrives() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.push_for_test(source_commit("1", "A", "hello world"), t);
    coordinator.tick_for_test(t + Duration::from_millis(1201));
    assert!(coordinator.last_frame_for_test().slots[4].is_none());
    coordinator.render_for_test(t + Duration::from_millis(1201));
    assert!(coordinator.last_frame_for_test().slots[4].is_some());
    coordinator.push_for_test(
        target_draft("1", "A", "bonjour"),
        t + Duration::from_millis(1500),
    );
    coordinator.render_for_test(t + Duration::from_millis(1500));
    assert!(coordinator.last_frame_for_test().slots[4].is_none());
}

#[test]
fn unchanged_projection_does_not_submit_a_texture() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "same"), t);
    coordinator.render_for_test(t);
    coordinator.push_for_test(source_live("1", "same"), t);
    coordinator.render_for_test(t);
    assert_eq!(coordinator.submit_count_for_test(), 1);
}

#[test]
fn correlation_metadata_change_does_not_submit_identical_visuals() {
    let mut coordinator = RuntimeCoordinator::for_test();
    coordinator.apply_settings_for_test(VrViewSettings {
        display_mode: DisplayMode::Original,
        ..Default::default()
    });
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_commit("1", "A", "same text"), t);
    coordinator.render_for_test(t);
    coordinator.push_for_test(
        source_commit("1", "B", "same text"),
        t + Duration::from_millis(1),
    );
    coordinator.render_for_test(t + Duration::from_millis(1));

    assert_eq!(coordinator.submit_count_for_test(), 1);
}

#[test]
fn queued_events_coalesce_into_one_render() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello"), t);
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.drain_and_render_for_test(t);
    assert_eq!(coordinator.submit_count_for_test(), 1);
    assert_eq!(
        coordinator.last_frame_for_test().slots[4]
            .as_ref()
            .unwrap()
            .text,
        "hello wor"
    );
}

#[test]
fn pending_handoff_frame_can_retry_without_consuming_live_state() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.push_for_test(source_commit("1", "A", "hello world"), t);
    coordinator.push_for_test(target_draft("1", "A", "bonjour"), t);
    coordinator.tick_for_test(t + Duration::from_millis(1201));

    let first = coordinator
        .pending_frame(t + Duration::from_millis(1201))
        .expect("handoff frame should be dirty");
    assert!(matches!(
        coordinator.transcript_for_test().live_input,
        LiveInputRow::Settled { .. }
    ));

    let retry = coordinator
        .pending_frame(t + Duration::from_millis(1201))
        .expect("failed render must leave the frame dirty");
    assert_eq!(first, retry);
    coordinator.commit_frame(retry);
    assert!(matches!(
        coordinator.transcript_for_test().live_input,
        LiveInputRow::Hidden
    ));
}

#[test]
fn visible_event_does_not_consume_handoff_before_render() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "hello wor"), t);
    coordinator.push_for_test(source_commit("1", "A", "hello world"), t);
    coordinator.push_for_test(target_draft("1", "A", "bonjour"), t);

    coordinator.push_for_test(
        target_draft("1", "A", "bonjour mieux"),
        t + Duration::from_millis(1201),
    );
    assert!(matches!(
        coordinator.transcript_for_test().live_input,
        LiveInputRow::Settled { .. }
    ));
}

#[test]
fn committed_visible_source_wakes_a_hidden_overlay() {
    let mut coordinator = RuntimeCoordinator::for_test();
    coordinator.apply_settings_for_test(VrViewSettings {
        display_mode: DisplayMode::Original,
        ..Default::default()
    });
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_commit("1", "A", "final source"), t);

    let visibility = coordinator.tick(t);
    assert_eq!(visibility.set_visible, Some(true));
    assert_eq!(visibility.alpha, Some(1.0));
}

#[test]
fn target_hidden_by_original_mode_does_not_rearm_silence() {
    let mut coordinator = RuntimeCoordinator::for_test();
    coordinator.apply_settings_for_test(VrViewSettings {
        display_mode: DisplayMode::Original,
        ..Default::default()
    });
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_commit("1", "A", "source"), t);
    coordinator.tick(t);

    coordinator.push_for_test(
        target_draft("1", "A", "invisible target"),
        t + SILENCE_BEFORE_FADE - Duration::from_millis(100),
    );
    let fading = coordinator.tick(t + SILENCE_BEFORE_FADE + Duration::from_millis(100));

    assert!(matches!(fading.alpha, Some(alpha) if alpha < 1.0));
}

#[test]
fn invalid_settings_leave_last_valid_settings_active() {
    let mut coordinator = RuntimeCoordinator::for_test();
    coordinator.apply_settings_for_test(VrViewSettings {
        display_mode: DisplayMode::Translation,
        max_speakers: 3,
        bilingual_pair_count: 1,
        show_speaker_labels: true,
    });
    coordinator.apply_settings_for_test(VrViewSettings {
        max_speakers: 9,
        ..Default::default()
    });
    assert_eq!(
        coordinator.settings_for_test().display_mode,
        DisplayMode::Translation
    );
    assert_eq!(coordinator.settings_for_test().max_speakers, 3);
}

#[test]
fn parent_death_exits_cleanly() {
    let mut coordinator = RuntimeCoordinator::for_test_with_parent(false);
    let now = coordinator.now_for_test();
    coordinator.poll_parent_for_test(now);
    assert!(coordinator.should_exit_for_test());
}

#[test]
fn transparent_empty_frame_is_submitted_before_ready() {
    let mut coordinator = RuntimeCoordinator::for_test();
    coordinator.start_for_test(coordinator.now_for_test());
    assert!(coordinator.ready_for_test());
    assert_eq!(coordinator.submit_count_for_test(), 1);
    assert!(coordinator
        .last_frame_for_test()
        .slots
        .iter()
        .all(Option::is_none));
}

#[test]
fn startup_deadline_expires_without_desktop() {
    let mut coordinator = RuntimeCoordinator::for_test_with_parent(true);
    coordinator.set_deadline_for_test(Duration::from_millis(3000));
    assert!(coordinator
        .startup_tick_for_test(Duration::from_millis(3001))
        .is_err());
}

#[test]
fn reconnect_resets_transcript_state() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_commit("1", "A", "hello"), t);
    coordinator.push_for_test(target_draft("1", "A", "bonjour"), t);
    coordinator.on_disconnect_for_test();
    assert!(coordinator.transcript_for_test().sentences.is_empty());
}

#[test]
fn hud_roles_remain_explicit_in_the_coordinator_output() {
    let mut coordinator = RuntimeCoordinator::for_test();
    let t = coordinator.now_for_test();
    coordinator.push_for_test(source_live("1", "live"), t);
    coordinator.render_for_test(t);
    assert_eq!(
        coordinator.last_frame_for_test().slots[4]
            .as_ref()
            .unwrap()
            .role,
        HudRowRole::LiveSource
    );
}

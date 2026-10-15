mod support;

use rinbridge_overlay::hud::{HudFrame, HudRowKind, HudRowRole};
use rinbridge_overlay::projection::{project, LiveRowDirective, LIVE_SOURCE_HOLD};
use rinbridge_overlay::transcript::{
    CaptionEvent, CommittedSource, LiveSourceSnapshot, SpeakerKey, TargetUpdate, TranscriptState,
};
use rinbridge_overlay::views::{DisplayMode, VrViewSettings};
use std::time::{Duration, Instant};

fn settings(mode: DisplayMode, max: usize, pairs: usize) -> VrViewSettings {
    VrViewSettings {
        display_mode: mode,
        max_speakers: max as u8,
        bilingual_pair_count: pairs as u8,
        show_speaker_labels: true,
    }
}

fn commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceCommitted(CommittedSource {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("en".into()),
    })
}

fn live(speaker: &str, text: &str) -> CaptionEvent {
    CaptionEvent::SourceLive(LiveSourceSnapshot {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: None,
        text: text.into(),
        language: Some("en".into()),
    })
}

fn t_draft(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetDraft(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

fn t_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
    CaptionEvent::TargetCommitted(TargetUpdate {
        speaker: SpeakerKey::Diarized(speaker.into()),
        sentence_id: Some(id.into()),
        text: text.into(),
        language: Some("fr".into()),
    })
}

fn text_at(frame: &HudFrame, index: usize) -> Option<&str> {
    frame.slots[index].as_ref().map(|row| row.text.as_str())
}

#[test]
fn original_a_b_c_a_yields_one_row_per_speaker_updated_and_reordered() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    for (speaker, text) in [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2")] {
        state.apply(&commit(speaker, &format!("{speaker}-{text}"), text), now);
    }
    let projection = project(&state, &settings(DisplayMode::Original, 3, 1), now);
    assert_eq!(projection.frame.slots[0], None);
    assert_eq!(text_at(&projection.frame, 1), Some("b1"));
    assert_eq!(text_at(&projection.frame, 2), Some("c1"));
    assert_eq!(text_at(&projection.frame, 3), Some("a2"));
}

#[test]
fn original_capacity_one_two_three_evicts_least_recent() {
    let expected: [(usize, [Option<&str>; 3]); 3] = [
        (1usize, [None, None, Some("c")]),
        (2usize, [None, Some("b"), Some("c")]),
        (3usize, [Some("a"), Some("b"), Some("c")]),
    ];
    for (capacity, want) in expected {
        let mut state = TranscriptState::default();
        let now = Instant::now();
        for (speaker, text) in [("A", "a"), ("B", "b"), ("C", "c")] {
            state.apply(&commit(speaker, text, text), now);
        }
        let projection = project(&state, &settings(DisplayMode::Original, capacity, 1), now);
        let got = [
            text_at(&projection.frame, 1),
            text_at(&projection.frame, 2),
            text_at(&projection.frame, 3),
        ];
        assert_eq!(got, want, "capacity {capacity}");
    }
}

#[test]
fn translation_capacity_evicts_least_recent_speaker() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    for speaker in ["A", "B", "C"] {
        state.apply(&commit(speaker, speaker, "src"), now);
        state.apply(&t_commit(speaker, speaker, &format!("t{speaker}")), now);
    }
    let projection = project(&state, &settings(DisplayMode::Translation, 1, 1), now);
    assert_eq!(text_at(&projection.frame, 3), Some("tC"));
    assert_eq!(projection.frame.slots[1], None);
    assert_eq!(projection.frame.slots[2], None);
}

#[test]
fn live_input_never_renders_a_speaker_number() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&live("2", "speaking now"), now);

    for mode in [
        DisplayMode::Original,
        DisplayMode::Translation,
        DisplayMode::Both,
    ] {
        let projection = project(&state, &settings(mode, 3, 1), now);
        let row = projection.frame.slots[4].as_ref().unwrap();
        assert_eq!(row.text, "speaking now");
        assert_eq!(row.speaker_label, None);
    }
}

#[test]
fn translation_shows_only_the_newest_target_per_speaker() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&commit("1", "A", "a"), now);
    state.apply(&commit("1", "B", "b"), now);
    state.apply(&t_commit("1", "A", "ay"), now);
    state.apply(&t_commit("1", "B", "bee"), now);
    assert_eq!(
        text_at(
            &project(&state, &settings(DisplayMode::Translation, 3, 1), now).frame,
            3
        ),
        Some("bee")
    );
    state.apply(&t_commit("1", "A", "ay2"), now);
    assert_eq!(
        text_at(
            &project(&state, &settings(DisplayMode::Translation, 3, 1), now).frame,
            3
        ),
        Some("bee")
    );
}

#[test]
fn translation_draft_upgrades_in_place() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&commit("1", "A", "a"), now);
    state.apply(&t_draft("1", "A", "bon"), now);
    let draft = project(&state, &settings(DisplayMode::Translation, 3, 1), now);
    assert_eq!(
        draft.frame.slots[3].as_ref().unwrap().kind,
        HudRowKind::Draft
    );
    state.apply(&t_commit("1", "A", "bonjour"), now);
    let finalized = project(&state, &settings(DisplayMode::Translation, 3, 1), now);
    assert_eq!(text_at(&finalized.frame, 3), Some("bonjour"));
    assert_eq!(
        finalized.frame.slots[3].as_ref().unwrap().kind,
        HudRowKind::Settled
    );
}

#[test]
fn both_mode_one_pair_occupies_slots_two_and_three() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&commit("1", "A", "hello"), now);
    state.apply(&t_draft("1", "A", "bonjour"), now);
    let projection = project(&state, &settings(DisplayMode::Both, 3, 1), now);
    assert_eq!(projection.frame.slots[0], None);
    assert_eq!(projection.frame.slots[1], None);
    assert_eq!(
        projection.frame.slots[2].as_ref().unwrap().role,
        HudRowRole::UpperPrimary
    );
    assert_eq!(text_at(&projection.frame, 2), Some("bonjour"));
    assert_eq!(
        projection.frame.slots[3].as_ref().unwrap().role,
        HudRowRole::UpperSecondary
    );
    assert_eq!(text_at(&projection.frame, 3), Some("hello"));
    assert!(projection
        .frame
        .slots
        .iter()
        .flatten()
        .all(|row| row.speaker_label.is_none()));
}

#[test]
fn both_mode_two_pairs_occupy_slots_zero_through_three() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    for id in ["A", "B"] {
        state.apply(&commit("1", id, id), now);
        state.apply(&t_draft("1", id, &format!("t{id}")), now);
    }
    let projection = project(&state, &settings(DisplayMode::Both, 3, 2), now);
    assert_eq!(text_at(&projection.frame, 0), Some("tA"));
    assert_eq!(text_at(&projection.frame, 1), Some("A"));
    assert_eq!(text_at(&projection.frame, 2), Some("tB"));
    assert_eq!(text_at(&projection.frame, 3), Some("B"));
}

#[test]
fn both_mode_evicts_the_oldest_pair_beyond_capacity() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    for id in ["A", "B", "C"] {
        state.apply(&commit("1", id, id), now);
        state.apply(&t_draft("1", id, &format!("t{id}")), now);
    }
    let projection = project(&state, &settings(DisplayMode::Both, 3, 2), now);
    assert_eq!(text_at(&projection.frame, 0), Some("tB"));
    assert_eq!(text_at(&projection.frame, 1), Some("B"));
    assert_eq!(text_at(&projection.frame, 2), Some("tC"));
    assert_eq!(text_at(&projection.frame, 3), Some("C"));
}

#[test]
fn both_mode_never_crosses_sentence_ids() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&commit("1", "A", "a"), now);
    state.apply(&commit("1", "B", "b"), now);
    state.apply(&t_commit("1", "B", "tb"), now);
    let projection = project(&state, &settings(DisplayMode::Both, 3, 2), now);
    assert_eq!(projection.frame.slots[0], None);
    assert_eq!(projection.frame.slots[1], None);
    assert_eq!(text_at(&projection.frame, 2), Some("tb"));
    assert_eq!(text_at(&projection.frame, 3), Some("b"));
}

#[test]
fn live_row_always_occupies_slot_four() {
    let mut state = TranscriptState::default();
    state.apply(&live("1", "now speaking"), Instant::now());
    for mode in [
        DisplayMode::Original,
        DisplayMode::Translation,
        DisplayMode::Both,
    ] {
        let projection = project(&state, &settings(mode, 3, 1), Instant::now());
        let row = projection.frame.slots[4]
            .as_ref()
            .expect("live row present");
        assert_eq!(row.role, HudRowRole::LiveSource);
        assert_eq!(row.text, "now speaking");
        assert!(projection.frame.slots[0..4].iter().all(Option::is_none));
    }
}

#[test]
fn settled_row_reports_dismiss_after_hold_and_handoff() {
    let mut state = TranscriptState::default();
    let t0 = Instant::now();
    state.apply(&live("1", "hello wor"), t0);
    state.apply(&commit("1", "A", "hello world"), t0);
    state.apply(
        &t_draft("1", "A", "bonjour"),
        t0 + Duration::from_millis(500),
    );
    let early = project(
        &state,
        &settings(DisplayMode::Translation, 3, 1),
        t0 + Duration::from_millis(600),
    );
    assert_eq!(early.live_row, LiveRowDirective::Show);
    assert_eq!(text_at(&early.frame, 4), Some("hello world"));
    let late = project(
        &state,
        &settings(DisplayMode::Translation, 3, 1),
        t0 + LIVE_SOURCE_HOLD + Duration::from_millis(1),
    );
    assert!(matches!(late.live_row, LiveRowDirective::Dismiss { .. }));
    assert_eq!(late.frame.slots[4], None);
}

#[test]
fn no_handoff_keeps_settled_row_visible() {
    let mut state = TranscriptState::default();
    let t0 = Instant::now();
    state.apply(&live("1", "hello wor"), t0);
    state.apply(&commit("1", "A", "hello world"), t0);
    let projection = project(
        &state,
        &settings(DisplayMode::Translation, 3, 1),
        t0 + Duration::from_secs(10),
    );
    assert_eq!(projection.live_row, LiveRowDirective::Show);
    assert_eq!(text_at(&projection.frame, 4), Some("hello world"));
}

#[test]
fn language_propagates_without_rendering_speaker_numbers() {
    let mut state = TranscriptState::default();
    let now = Instant::now();
    state.apply(&commit("1", "A", "hello"), now);
    state.apply(&t_commit("1", "A", "bonjour"), now);
    let both = project(&state, &settings(DisplayMode::Both, 3, 1), now);
    assert_eq!(
        both.frame.slots[2].as_ref().unwrap().language.as_deref(),
        Some("fr")
    );
    assert_eq!(
        both.frame.slots[3].as_ref().unwrap().language.as_deref(),
        Some("en")
    );
    let original = project(&state, &settings(DisplayMode::Original, 3, 1), now);
    let row = original.frame.slots[3].as_ref().unwrap();
    assert_eq!(row.text, "hello");
    assert_eq!(row.speaker_label, None);

    let translation = project(&state, &settings(DisplayMode::Translation, 3, 1), now);
    assert_eq!(
        translation.frame.slots[3].as_ref().unwrap().speaker_label,
        None
    );
}

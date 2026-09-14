use std::collections::VecDeque;
use std::time::Instant;

use crate::views::VrViewSettings;

const LEDGER_CAP: usize = 64;
const PENDING_REFINEMENT_CAP: usize = 64;

/// A session-scoped Soniox diarization value. It is intentionally not a
/// durable human identity.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum SpeakerKey {
    Anonymous,
    Diarized(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum TrackPhase {
    #[default]
    Empty,
    Draft,
    Committed,
    Refined,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub struct TextTrack {
    pub text: String,
    pub phase: TrackPhase,
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct SentenceKey {
    pub local_ordinal: u64,
    pub upstream_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SentenceRecord {
    pub key: SentenceKey,
    pub speaker: SpeakerKey,
    pub source: TextTrack,
    pub target: TextTrack,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LiveSourceSnapshot {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedSource {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetUpdate {
    pub speaker: SpeakerKey,
    pub sentence_id: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

/// A refinement carries no speaker; it is keyed strictly by `sentence_id`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refinement {
    pub sentence_id: String,
    pub text: String,
    pub language: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum LiveInputRow {
    #[default]
    Hidden,
    Streaming(LiveSourceSnapshot),
    Settled {
        snapshot: LiveSourceSnapshot,
        closed_at: Instant,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptionEvent {
    SourceLive(LiveSourceSnapshot),
    SourceCommitted(CommittedSource),
    SourceEnd {
        speaker: SpeakerKey,
        sentence_id: Option<String>,
    },
    TargetDraft(TargetUpdate),
    TargetCommitted(TargetUpdate),
    RefinedTarget(Refinement),
    Clear {
        preserve_existing: bool,
    },
    ViewSettingsChanged(VrViewSettings),
    Shutdown,
    Activity,
}

#[derive(Debug, Default)]
pub struct TranscriptState {
    pub sentences: VecDeque<SentenceRecord>,
    pub live_input: LiveInputRow,
    speaker_recency: VecDeque<SpeakerKey>,
    next_local_ordinal: u64,
    pending_refinements: VecDeque<Refinement>,
}

impl TranscriptState {
    pub fn apply(&mut self, event: &CaptionEvent, now: Instant) {
        match event {
            CaptionEvent::SourceLive(snapshot) => self.apply_source_live(snapshot, now),
            CaptionEvent::SourceCommitted(committed) => self.apply_source_committed(committed, now),
            CaptionEvent::SourceEnd {
                speaker,
                sentence_id,
            } => self.apply_source_end(speaker, sentence_id.as_deref(), now),
            CaptionEvent::TargetDraft(update) => self.apply_target(update, TrackPhase::Draft),
            CaptionEvent::TargetCommitted(update) => {
                self.apply_target(update, TrackPhase::Committed)
            }
            CaptionEvent::RefinedTarget(refinement) => self.apply_refinement(refinement),
            CaptionEvent::Clear { preserve_existing } => {
                self.clear(*preserve_existing);
                return;
            }
            CaptionEvent::ViewSettingsChanged(_)
            | CaptionEvent::Activity
            | CaptionEvent::Shutdown => return,
        }
        self.replay_pending_refinements();
    }

    pub fn clear(&mut self, preserve_existing: bool) {
        if preserve_existing {
            let mut kept = VecDeque::new();
            for mut record in std::mem::take(&mut self.sentences) {
                let source_kept = matches!(
                    record.source.phase,
                    TrackPhase::Committed | TrackPhase::Refined
                );
                let target_kept = matches!(
                    record.target.phase,
                    TrackPhase::Committed | TrackPhase::Refined
                );
                if !source_kept {
                    record.source = TextTrack::default();
                }
                if !target_kept {
                    record.target = TextTrack::default();
                }
                if source_kept || target_kept {
                    kept.push_back(record);
                }
            }
            self.sentences = kept;
        } else {
            self.sentences.clear();
            self.next_local_ordinal = 0;
        }
        self.live_input = LiveInputRow::Hidden;
        if preserve_existing {
            let kept_speakers: Vec<_> = self
                .sentences
                .iter()
                .map(|record| record.speaker.clone())
                .collect();
            self.speaker_recency
                .retain(|speaker| kept_speakers.iter().any(|kept| kept == speaker));
        } else {
            self.speaker_recency.clear();
        }
        self.pending_refinements.clear();
    }

    pub fn speaker_recency(&self) -> &VecDeque<SpeakerKey> {
        &self.speaker_recency
    }

    pub fn sentence_by_upstream_id(&self, id: &str) -> Option<&SentenceRecord> {
        self.find_by_upstream(id)
            .map(|index| &self.sentences[index])
    }

    /// The speaker's most recent record whose source is still a draft.
    pub fn open_record_index(&self, speaker: &SpeakerKey) -> Option<usize> {
        self.sentences.iter().rposition(|record| {
            &record.speaker == speaker && record.source.phase == TrackPhase::Draft
        })
    }

    /// One-shot consumption of a settled live row after a visible handoff.
    pub fn dismiss_settled_live(
        &mut self,
        speaker: &SpeakerKey,
        sentence_id: Option<&str>,
    ) -> bool {
        if let LiveInputRow::Settled { snapshot, .. } = &self.live_input {
            if &snapshot.speaker != speaker {
                return false;
            }
            let matches = match (sentence_id, snapshot.sentence_id.as_deref()) {
                (Some(requested), Some(current)) => requested == current,
                _ => true,
            };
            if matches {
                self.live_input = LiveInputRow::Hidden;
                return true;
            }
        }
        false
    }

    fn apply_source_live(&mut self, snapshot: &LiveSourceSnapshot, _now: Instant) {
        if snapshot.text.is_empty() {
            return;
        }
        let index = match self.open_record_index(&snapshot.speaker) {
            Some(index) => index,
            None => self.push_record(snapshot.speaker.clone(), None),
        };
        self.sentences[index].source = TextTrack {
            text: snapshot.text.clone(),
            phase: TrackPhase::Draft,
            language: snapshot.language.clone(),
        };
        self.touch_recency(&snapshot.speaker);
        self.live_input = LiveInputRow::Streaming(snapshot.clone());
    }

    fn apply_source_committed(&mut self, committed: &CommittedSource, now: Instant) {
        let index = self.resolve_or_create(&committed.speaker, committed.sentence_id.as_deref());
        self.sentences[index].source = TextTrack {
            text: committed.text.clone(),
            phase: TrackPhase::Committed,
            language: committed.language.clone(),
        };
        self.touch_recency(&committed.speaker);
        if let LiveInputRow::Streaming(snapshot) = &self.live_input {
            if snapshot.speaker == committed.speaker
                && !sentence_id_conflicts(
                    snapshot.sentence_id.as_deref(),
                    committed.sentence_id.as_deref(),
                )
            {
                self.live_input = LiveInputRow::Settled {
                    snapshot: LiveSourceSnapshot {
                        speaker: committed.speaker.clone(),
                        sentence_id: committed.sentence_id.clone(),
                        text: committed.text.clone(),
                        language: committed.language.clone(),
                    },
                    closed_at: now,
                };
            }
        }
    }

    fn apply_source_end(&mut self, speaker: &SpeakerKey, sentence_id: Option<&str>, now: Instant) {
        let index = match sentence_id {
            Some(id) => self
                .find_by_upstream(id)
                .or_else(|| self.unbound_open_index(speaker)),
            None => self.open_record_index(speaker),
        };
        if let Some(index) = index {
            let record = &mut self.sentences[index];
            if &record.speaker != speaker {
                return;
            }
            if let Some(id) = sentence_id {
                if record.key.upstream_id.as_deref() != Some(id) {
                    return;
                }
            }
            record.source.phase = TrackPhase::Committed;
            let snapshot = LiveSourceSnapshot {
                speaker: speaker.clone(),
                sentence_id: record.key.upstream_id.clone(),
                text: record.source.text.clone(),
                language: record.source.language.clone(),
            };
            self.live_input = LiveInputRow::Settled {
                snapshot,
                closed_at: now,
            };
            return;
        }
        if let LiveInputRow::Streaming(snapshot) = &self.live_input {
            if &snapshot.speaker == speaker
                && !sentence_id_conflicts(snapshot.sentence_id.as_deref(), sentence_id)
            {
                self.live_input = LiveInputRow::Settled {
                    snapshot: snapshot.clone(),
                    closed_at: now,
                };
            }
        }
    }

    fn apply_target(&mut self, update: &TargetUpdate, phase: TrackPhase) {
        let index = match update.sentence_id.as_deref() {
            Some(id) => Some(self.resolve_or_create(&update.speaker, Some(id))),
            None => self.open_record_index(&update.speaker),
        };
        if let Some(index) = index {
            let current_phase = self.sentences[index].target.phase;
            let is_downgrade = matches!(
                (current_phase, phase),
                (
                    TrackPhase::Committed | TrackPhase::Refined,
                    TrackPhase::Draft
                ) | (TrackPhase::Refined, TrackPhase::Committed)
            );
            if is_downgrade {
                return;
            }
            self.sentences[index].target = TextTrack {
                text: update.text.clone(),
                phase,
                language: update.language.clone(),
            };
        }
    }

    fn apply_refinement(&mut self, refinement: &Refinement) {
        if let Some(index) = self.find_by_upstream(&refinement.sentence_id) {
            self.sentences[index].target = TextTrack {
                text: refinement.text.clone(),
                phase: TrackPhase::Refined,
                language: refinement.language.clone(),
            };
        } else {
            self.pending_refinements.push_back(refinement.clone());
            while self.pending_refinements.len() > PENDING_REFINEMENT_CAP {
                self.pending_refinements.pop_front();
            }
        }
    }

    fn replay_pending_refinements(&mut self) {
        if self.pending_refinements.is_empty() {
            return;
        }
        let pending = std::mem::take(&mut self.pending_refinements);
        for refinement in pending {
            if let Some(index) = self.find_by_upstream(&refinement.sentence_id) {
                self.sentences[index].target = TextTrack {
                    text: refinement.text,
                    phase: TrackPhase::Refined,
                    language: refinement.language,
                };
            } else {
                self.pending_refinements.push_back(refinement);
            }
        }
    }

    fn resolve_or_create(&mut self, speaker: &SpeakerKey, sentence_id: Option<&str>) -> usize {
        if let Some(id) = sentence_id {
            if let Some(index) = self.find_by_upstream_for_speaker(id, speaker) {
                return index;
            }
            if let Some(index) = self.unbound_open_index(speaker) {
                self.sentences[index].key.upstream_id = Some(id.to_string());
                return index;
            }
            return self.push_record(speaker.clone(), Some(id.to_string()));
        }
        match self.open_record_index(speaker) {
            Some(index) => index,
            None => self.push_record(speaker.clone(), None),
        }
    }

    fn push_record(&mut self, speaker: SpeakerKey, upstream_id: Option<String>) -> usize {
        self.next_local_ordinal += 1;
        self.sentences.push_back(SentenceRecord {
            key: SentenceKey {
                local_ordinal: self.next_local_ordinal,
                upstream_id,
            },
            speaker,
            source: TextTrack::default(),
            target: TextTrack::default(),
        });
        while self.sentences.len() > LEDGER_CAP {
            self.sentences.pop_front();
        }
        self.sentences.len() - 1
    }

    fn find_by_upstream(&self, id: &str) -> Option<usize> {
        self.sentences
            .iter()
            .position(|record| record.key.upstream_id.as_deref() == Some(id))
    }

    fn find_by_upstream_for_speaker(&self, id: &str, speaker: &SpeakerKey) -> Option<usize> {
        self.sentences.iter().position(|record| {
            record.key.upstream_id.as_deref() == Some(id) && &record.speaker == speaker
        })
    }

    fn unbound_open_index(&self, speaker: &SpeakerKey) -> Option<usize> {
        self.sentences.iter().rposition(|record| {
            &record.speaker == speaker
                && record.source.phase == TrackPhase::Draft
                && record.key.upstream_id.is_none()
        })
    }

    fn touch_recency(&mut self, speaker: &SpeakerKey) {
        self.speaker_recency.retain(|known| known != speaker);
        self.speaker_recency.push_back(speaker.clone());
    }
}

fn sentence_id_conflicts(streaming: Option<&str>, incoming: Option<&str>) -> bool {
    match (streaming, incoming) {
        (Some(a), Some(b)) => a != b,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    fn s_live(speaker: &str, text: &str) -> CaptionEvent {
        CaptionEvent::SourceLive(LiveSourceSnapshot {
            speaker: SpeakerKey::Diarized(speaker.into()),
            sentence_id: None,
            text: text.into(),
            language: Some("en".into()),
        })
    }

    fn s_commit(speaker: &str, id: &str, text: &str) -> CaptionEvent {
        CaptionEvent::SourceCommitted(CommittedSource {
            speaker: SpeakerKey::Diarized(speaker.into()),
            sentence_id: Some(id.into()),
            text: text.into(),
            language: Some("en".into()),
        })
    }

    fn s_end(speaker: &str, id: Option<&str>) -> CaptionEvent {
        CaptionEvent::SourceEnd {
            speaker: SpeakerKey::Diarized(speaker.into()),
            sentence_id: id.map(str::to_owned),
        }
    }

    fn t_draft(speaker: &str, id: Option<&str>, text: &str) -> CaptionEvent {
        CaptionEvent::TargetDraft(TargetUpdate {
            speaker: SpeakerKey::Diarized(speaker.into()),
            sentence_id: id.map(str::to_owned),
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

    fn refine(id: &str, text: &str) -> CaptionEvent {
        CaptionEvent::RefinedTarget(Refinement {
            sentence_id: id.into(),
            text: text.into(),
            language: Some("fr".into()),
        })
    }

    #[test]
    fn source_live_creates_open_record_and_streams() {
        let mut s = TranscriptState::default();
        s.apply(&s_live("1", "hel"), Instant::now());
        assert_eq!(s.sentences.len(), 1);
        assert_eq!(s.sentences[0].source.phase, TrackPhase::Draft);
        assert!(matches!(&s.live_input, LiveInputRow::Streaming(snap) if snap.text == "hel"));
    }

    #[test]
    fn source_commit_refreshes_recency_and_settles_full_text() {
        let mut s = TranscriptState::default();
        let t0 = Instant::now();
        s.apply(&s_live("1", "hello wor"), t0);
        s.apply(&s_commit("1", "A", "hello world"), t0);
        match &s.live_input {
            LiveInputRow::Settled { snapshot, .. } => assert_eq!(snapshot.text, "hello world"),
            other => panic!("expected Settled, got {other:?}"),
        }
        assert_eq!(
            s.speaker_recency().back(),
            Some(&SpeakerKey::Diarized("1".into()))
        );
        // A later source commit for another speaker refreshes recency to it and
        // must not disturb the settled row owned by speaker 1.
        s.apply(&s_commit("2", "B", "other"), t0);
        assert_eq!(
            s.speaker_recency().back(),
            Some(&SpeakerKey::Diarized("2".into()))
        );
        assert!(
            matches!(&s.live_input, LiveInputRow::Settled { snapshot, .. } if snapshot.speaker == SpeakerKey::Diarized("1".into()))
        );
    }

    #[test]
    fn empty_non_final_does_not_settle_the_live_row() {
        let mut s = TranscriptState::default();
        s.apply(&s_live("1", "hello"), Instant::now());
        s.apply(&s_live("1", ""), Instant::now());
        assert!(matches!(&s.live_input, LiveInputRow::Streaming(snap) if snap.text == "hello"));
    }

    #[test]
    fn source_end_settles_only_the_matching_speaker() {
        let mut s = TranscriptState::default();
        s.apply(&s_live("1", "hello"), Instant::now());
        s.apply(&s_end("2", None), Instant::now());
        assert!(
            matches!(&s.live_input, LiveInputRow::Streaming(_)),
            "wrong speaker must not settle"
        );
        s.apply(&s_end("1", None), Instant::now());
        assert!(
            matches!(&s.live_input, LiveInputRow::Settled { snapshot, .. } if snapshot.text == "hello")
        );
    }

    #[test]
    fn source_end_with_conflicting_id_is_a_noop() {
        let mut s = TranscriptState::default();
        s.apply(&s_commit("1", "A", "a"), Instant::now());
        s.apply(&s_end("1", Some("B")), Instant::now());
        assert!(matches!(&s.live_input, LiveInputRow::Hidden));
    }

    #[test]
    fn target_without_id_binds_to_open_sentence() {
        let mut s = TranscriptState::default();
        s.apply(&s_live("1", "bonjour"), Instant::now());
        s.apply(&t_draft("1", None, "hello"), Instant::now());
        assert_eq!(s.sentences.len(), 1);
        assert_eq!(s.sentences[0].target.text, "hello");
    }

    #[test]
    fn target_without_id_is_dropped_when_no_open_sentence() {
        let mut s = TranscriptState::default();
        s.apply(&s_commit("1", "A", "done"), Instant::now());
        s.apply(&t_draft("1", None, "orphan"), Instant::now());
        assert_eq!(s.sentences.len(), 1);
        assert_eq!(s.sentences[0].target.phase, TrackPhase::Empty);
    }

    #[test]
    fn late_target_for_older_sentence_is_kept_in_the_reducer() {
        let mut s = TranscriptState::default();
        s.apply(&s_commit("1", "A", "a"), Instant::now());
        s.apply(&s_commit("1", "B", "b"), Instant::now());
        s.apply(&t_commit("1", "B", "bee"), Instant::now());
        s.apply(&t_commit("1", "A", "ay"), Instant::now());
        assert_eq!(s.sentence_by_upstream_id("A").unwrap().target.text, "ay");
        assert_eq!(s.sentence_by_upstream_id("B").unwrap().target.text, "bee");
    }

    #[test]
    fn refinement_resolves_by_id_and_replays_when_late() {
        let mut s = TranscriptState::default();
        s.apply(&refine("A", "early"), Instant::now());
        s.apply(&s_commit("1", "A", "a"), Instant::now());
        let record = s.sentence_by_upstream_id("A").unwrap();
        assert_eq!(record.target.phase, TrackPhase::Refined);
        assert_eq!(record.target.text, "early");
    }

    #[test]
    fn language_is_recorded_on_both_tracks() {
        let mut s = TranscriptState::default();
        s.apply(&s_commit("1", "A", "hello"), Instant::now());
        s.apply(&t_commit("1", "A", "bonjour"), Instant::now());
        assert_eq!(s.sentences[0].source.language.as_deref(), Some("en"));
        assert_eq!(s.sentences[0].target.language.as_deref(), Some("fr"));
    }

    #[test]
    fn final_target_wins_over_a_later_same_frame_draft() {
        let mut s = TranscriptState::default();
        let now = Instant::now();
        s.apply(&s_commit("1", "A", "source"), now);
        s.apply(&t_commit("1", "A", "final"), now);
        s.apply(&t_draft("1", Some("A"), "stale draft"), now);
        assert_eq!(s.sentence_by_upstream_id("A").unwrap().target.text, "final");
        assert_eq!(
            s.sentence_by_upstream_id("A").unwrap().target.phase,
            TrackPhase::Committed
        );
    }

    #[test]
    fn preserve_clear_keeps_recency_for_retained_tracks() {
        let mut s = TranscriptState::default();
        let now = Instant::now();
        s.apply(&s_commit("1", "A", "source"), now);
        s.apply(&t_draft("1", Some("A"), "draft"), now);
        s.clear(true);
        assert_eq!(
            s.speaker_recency().back(),
            Some(&SpeakerKey::Diarized("1".into()))
        );
    }

    #[test]
    fn same_sentence_id_does_not_cross_speaker_records() {
        let mut s = TranscriptState::default();
        let now = Instant::now();
        s.apply(&s_commit("1", "same", "speaker one"), now);
        s.apply(&s_commit("2", "same", "speaker two"), now);
        assert_eq!(s.sentences.len(), 2);
        assert_eq!(s.sentences[0].speaker, SpeakerKey::Diarized("1".into()));
        assert_eq!(s.sentences[1].speaker, SpeakerKey::Diarized("2".into()));
    }

    #[test]
    fn recent_ledger_stays_bounded_at_64() {
        let mut s = TranscriptState::default();
        for i in 0..80u64 {
            s.apply(&s_commit("1", &format!("{i}"), "x"), Instant::now());
        }
        assert_eq!(s.sentences.len(), 64);
    }

    #[test]
    fn anonymous_provider_uses_one_speaker_key() {
        let mut s = TranscriptState::default();
        for i in 0..3u64 {
            s.apply(
                &CaptionEvent::SourceCommitted(CommittedSource {
                    speaker: SpeakerKey::Anonymous,
                    sentence_id: Some(format!("{i}")),
                    text: "x".into(),
                    language: None,
                }),
                Instant::now(),
            );
        }
        assert!(s
            .sentences
            .iter()
            .all(|r| r.speaker == SpeakerKey::Anonymous));
        assert_eq!(s.sentences.len(), 3);
    }

    #[test]
    fn clear_true_is_per_track() {
        // committed source + draft target -> source kept, target reset
        let mut s = TranscriptState::default();
        s.apply(&s_commit("1", "A", "a"), Instant::now());
        s.apply(&t_draft("1", Some("A"), "draft"), Instant::now());
        s.clear(true);
        assert_eq!(s.sentences.len(), 1);
        assert_eq!(s.sentences[0].source.phase, TrackPhase::Committed);
        assert_eq!(s.sentences[0].target.phase, TrackPhase::Empty);

        // draft source + committed target -> target kept, source reset
        let mut s = TranscriptState::default();
        s.apply(&s_live("2", "draft src"), Instant::now());
        s.apply(&t_commit("2", "B", "final tgt"), Instant::now());
        s.clear(true);
        assert_eq!(s.sentences.len(), 1);
        assert_eq!(s.sentences[0].source.phase, TrackPhase::Empty);
        assert_eq!(s.sentences[0].target.phase, TrackPhase::Committed);

        // neither settled -> dropped
        let mut s = TranscriptState::default();
        s.apply(&s_live("3", "only draft"), Instant::now());
        s.clear(true);
        assert!(s.sentences.is_empty());
    }

    #[test]
    fn dismiss_settled_live_matches_and_consumes_once() {
        let mut s = TranscriptState::default();
        s.apply(&s_live("1", "hello wor"), Instant::now());
        s.apply(&s_commit("1", "A", "hello world"), Instant::now());
        assert!(!s.dismiss_settled_live(&SpeakerKey::Diarized("2".into()), None));
        assert!(s.dismiss_settled_live(&SpeakerKey::Diarized("1".into()), Some("A")));
        assert!(matches!(s.live_input, LiveInputRow::Hidden));
        assert!(!s.dismiss_settled_live(&SpeakerKey::Diarized("1".into()), Some("A")));
    }
}

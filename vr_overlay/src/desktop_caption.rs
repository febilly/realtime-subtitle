use serde_json::{Map, Value};
use std::collections::{HashMap, VecDeque};

const SOURCE_CORRELATION_CAP: usize = 256;
const PENDING_REFINEMENT_CAP: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DesktopCaptionChange {
    pub(crate) source: Option<String>,
    pub(crate) translation: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum DesktopCaptionOutcome {
    Change(DesktopCaptionChange),
    Clear,
    Noop(DesktopCaptionNoopReason),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DesktopCaptionNoopReason {
    Unrelated,
    Unchanged,
    StaleRefinement,
    UncorrelatedRefinement,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SentenceRef {
    id: String,
    ordinal: u64,
}

#[derive(Debug, Clone)]
struct PendingRefinement {
    payload: Map<String, Value>,
}

#[derive(Debug, Default)]
struct CaptionLineState {
    visible_text: String,
    owner: Option<SentenceRef>,
}

#[derive(Debug, Default)]
struct FinalAccumulator {
    text: String,
    owner: Option<SentenceRef>,
    replace_on_next_token: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LineKind {
    Source,
    Translation,
    Separator,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalTokenFingerprint {
    is_separator: bool,
    line: LineKind,
    text: String,
    sentence_id: Option<String>,
    is_final: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalTokensFingerprint(Vec<FinalTokenFingerprint>);

#[derive(Debug, Default)]
pub(crate) struct DesktopCaptionReducer {
    source: CaptionLineState,
    translation: CaptionLineState,
    source_final: FinalAccumulator,
    translation_final: FinalAccumulator,
    sentence_ordinals: HashMap<String, u64>,
    recent_source_by_sentence: VecDeque<(SentenceRef, String)>,
    next_sentence_ordinal: u64,
    previous_update_final_tokens: Option<FinalTokensFingerprint>,
    pending_refinements: VecDeque<PendingRefinement>,
}

impl DesktopCaptionReducer {
    pub(crate) fn apply_message(&mut self, map: &Map<String, Value>) -> DesktopCaptionOutcome {
        match map.get("type").and_then(Value::as_str) {
            Some("update") => self.apply_update(map),
            Some("refine_result") => {
                self.previous_update_final_tokens = None;
                self.apply_refinement(map)
            }
            Some("clear") => {
                *self = Self::default();
                DesktopCaptionOutcome::Clear
            }
            _ => {
                self.previous_update_final_tokens = None;
                DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unrelated)
            }
        }
    }

    #[cfg(test)]
    fn visible_source(&self) -> &str {
        &self.source.visible_text
    }

    #[cfg(test)]
    fn visible_translation(&self) -> &str {
        &self.translation.visible_text
    }

    #[cfg(test)]
    fn translation_owner_id(&self) -> Option<&str> {
        self.translation
            .owner
            .as_ref()
            .map(|owner| owner.id.as_str())
    }

    fn apply_update(&mut self, map: &Map<String, Value>) -> DesktopCaptionOutcome {
        let final_tokens = map
            .get("final_tokens")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let fingerprint = Self::fingerprint(final_tokens);
        let replayed_finals = self.previous_update_final_tokens.as_ref() == Some(&fingerprint);
        self.previous_update_final_tokens = Some(fingerprint);

        let mut source_final_applied = false;
        let mut translation_final_applied = false;
        let before_source = self.source.visible_text.clone();
        let before_translation = self.translation.visible_text.clone();

        if !replayed_finals {
            for token in final_tokens {
                let Some(token_map) = token.as_object() else {
                    continue;
                };
                if token_map
                    .get("is_separator")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    self.source_final.replace_on_next_token = true;
                    self.translation_final.replace_on_next_token = true;
                    continue;
                }
                if token_map.get("is_final").and_then(Value::as_bool) == Some(false) {
                    continue;
                }
                let Some(text) = token_map.get("text").and_then(Value::as_str) else {
                    continue;
                };
                if text.is_empty() || text == "<end>" {
                    continue;
                }
                let owner = token_map
                    .get("llm_sentence_id")
                    .and_then(Value::as_str)
                    .filter(|id| !id.is_empty())
                    .map(|id| self.sentence_ref(id));
                if token_map.get("translation_status").and_then(Value::as_str)
                    == Some("translation")
                {
                    let accepted = Self::apply_final_token(
                        &mut self.translation_final,
                        &mut self.translation,
                        text,
                        owner,
                    );
                    translation_final_applied |= accepted;
                } else {
                    let accepted = Self::apply_final_token(
                        &mut self.source_final,
                        &mut self.source,
                        text,
                        owner.clone(),
                    );
                    source_final_applied |= accepted;
                    if accepted {
                        if let Some(owner) = self.source_final.owner.clone() {
                            self.remember_source(owner, self.source_final.text.clone());
                        }
                    }
                }
            }
        }

        let non_final_tokens = map
            .get("non_final_tokens")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let (source_draft, source_owner) = self.collect_draft(non_final_tokens, LineKind::Source);
        let (translation_draft, translation_owner) =
            self.collect_draft(non_final_tokens, LineKind::Translation);

        if let Some(owner) = source_owner.clone() {
            if !source_draft.is_empty() {
                self.remember_source(owner, source_draft.clone());
            }
        }

        if !source_final_applied && !source_draft.is_empty() {
            self.source.visible_text = source_draft;
            if source_owner.is_some() || self.source_final.replace_on_next_token {
                self.source.owner = source_owner;
            }
        }
        if !translation_final_applied && !translation_draft.is_empty() {
            self.translation.visible_text = translation_draft;
            if translation_owner.is_some() || self.translation_final.replace_on_next_token {
                self.translation.owner = translation_owner;
            }
        }

        // A refinement can race its source update on the legacy /ws stream.
        // Reconcile it after this frame has registered any sentence owners;
        // otherwise the one-shot refine event would be lost permanently.
        self.replay_pending_refinements();

        Self::visible_outcome(
            before_source,
            before_translation,
            &self.source.visible_text,
            &self.translation.visible_text,
        )
    }

    fn apply_refinement(&mut self, map: &Map<String, Value>) -> DesktopCaptionOutcome {
        let outcome = self.apply_refinement_now(map);
        if matches!(
            outcome,
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::UncorrelatedRefinement)
        ) {
            self.queue_pending_refinement(map);
        }
        outcome
    }

    fn apply_refinement_now(&mut self, map: &Map<String, Value>) -> DesktopCaptionOutcome {
        if map.get("no_change").and_then(Value::as_bool) == Some(true) {
            return DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged);
        }
        let refined = map
            .get("refined_translation")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let original = map
            .get("original_translation")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let translation = if refined.is_empty() {
            original
        } else {
            refined
        };
        if translation.is_empty() {
            return DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged);
        }

        let incoming_owner = match map
            .get("sentence_id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
        {
            Some(id) => match self.sentence_ordinals.get(id).copied() {
                Some(ordinal) => SentenceRef {
                    id: id.to_owned(),
                    ordinal,
                },
                None if self.translation.owner.is_none() && self.matches_visible_source(map) => {
                    self.sentence_ref(id)
                }
                None => {
                    return DesktopCaptionOutcome::Noop(
                        DesktopCaptionNoopReason::UncorrelatedRefinement,
                    )
                }
            },
            None => match self.legacy_refinement_owner(map) {
                Some(owner) => owner,
                None => {
                    return DesktopCaptionOutcome::Noop(
                        DesktopCaptionNoopReason::UncorrelatedRefinement,
                    )
                }
            },
        };

        if self
            .translation
            .owner
            .as_ref()
            .is_some_and(|owner| incoming_owner.ordinal < owner.ordinal)
        {
            return DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::StaleRefinement);
        }

        if self.translation.visible_text == translation
            && self.translation.owner.as_ref() == Some(&incoming_owner)
        {
            return DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged);
        }
        self.translation.visible_text = translation.to_owned();
        self.translation.owner = Some(incoming_owner.clone());
        self.translation_final.text = translation.to_owned();
        self.translation_final.owner = Some(incoming_owner);
        self.translation_final.replace_on_next_token = false;
        DesktopCaptionOutcome::Change(DesktopCaptionChange {
            source: None,
            translation: Some(translation.to_owned()),
        })
    }

    fn queue_pending_refinement(&mut self, map: &Map<String, Value>) {
        let has_sentence_id = map
            .get("sentence_id")
            .and_then(Value::as_str)
            .is_some_and(|id| !id.trim().is_empty());
        let has_source = map
            .get("source")
            .and_then(Value::as_str)
            .is_some_and(|source| !Self::normalize_source(source).is_empty());
        if !has_sentence_id && !has_source {
            return;
        }

        self.pending_refinements.push_back(PendingRefinement {
            payload: map.clone(),
        });
        while self.pending_refinements.len() > PENDING_REFINEMENT_CAP {
            self.pending_refinements.pop_front();
        }
    }

    fn replay_pending_refinements(&mut self) {
        if self.pending_refinements.is_empty() {
            return;
        }

        let pending = std::mem::take(&mut self.pending_refinements);
        for refinement in pending {
            if matches!(
                self.apply_refinement_now(&refinement.payload),
                DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::UncorrelatedRefinement)
            ) {
                self.pending_refinements.push_back(refinement);
            }
        }
    }

    fn apply_final_token(
        accumulator: &mut FinalAccumulator,
        visible: &mut CaptionLineState,
        text: &str,
        owner: Option<SentenceRef>,
    ) -> bool {
        let owner_is_older = match (&owner, &accumulator.owner) {
            (Some(incoming), Some(current)) => incoming.ordinal < current.ordinal,
            _ => false,
        };
        if owner_is_older {
            return false;
        }
        let owner_is_newer = match (&owner, &accumulator.owner) {
            (Some(incoming), Some(current)) => incoming.ordinal > current.ordinal,
            (Some(_), None) => !accumulator.text.is_empty(),
            _ => false,
        };

        let replaced =
            accumulator.replace_on_next_token || owner_is_newer || accumulator.text.is_empty();
        if replaced {
            accumulator.text = text.to_owned();
            accumulator.owner = owner.clone();
            accumulator.replace_on_next_token = false;
        } else if text.starts_with(&accumulator.text) && text.len() > accumulator.text.len() {
            accumulator.text = text.to_owned();
        } else {
            accumulator.text.push_str(text);
        }
        if owner.is_some() {
            accumulator.owner = owner.clone();
        }

        visible.visible_text.clone_from(&accumulator.text);
        if replaced || owner.is_some() {
            visible.owner = owner;
        }
        true
    }

    fn collect_draft(
        &mut self,
        tokens: &[Value],
        wanted: LineKind,
    ) -> (String, Option<SentenceRef>) {
        let mut text = String::new();
        let mut owner = None;
        for token in tokens {
            let Some(map) = token.as_object() else {
                continue;
            };
            let kind =
                if map.get("translation_status").and_then(Value::as_str) == Some("translation") {
                    LineKind::Translation
                } else {
                    LineKind::Source
                };
            if kind != wanted {
                continue;
            }
            let token_text = map.get("text").and_then(Value::as_str).unwrap_or("");
            if token_text.is_empty() || token_text == "<end>" {
                continue;
            }
            text.push_str(token_text);
            if let Some(id) = map
                .get("llm_sentence_id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
            {
                owner = Some(self.sentence_ref(id));
            }
        }
        (text, owner)
    }

    fn sentence_ref(&mut self, id: &str) -> SentenceRef {
        if let Some(ordinal) = self.sentence_ordinals.get(id).copied() {
            return SentenceRef {
                id: id.to_owned(),
                ordinal,
            };
        }
        self.next_sentence_ordinal += 1;
        let owner = SentenceRef {
            id: id.to_owned(),
            ordinal: self.next_sentence_ordinal,
        };
        self.sentence_ordinals.insert(id.to_owned(), owner.ordinal);
        owner
    }

    fn remember_source(&mut self, owner: SentenceRef, source: String) {
        self.recent_source_by_sentence
            .retain(|(known, _)| known.id != owner.id);
        self.recent_source_by_sentence
            .push_back((owner, Self::normalize_source(&source)));
        while self.recent_source_by_sentence.len() > SOURCE_CORRELATION_CAP {
            self.recent_source_by_sentence.pop_front();
        }
    }

    fn matches_visible_source(&self, map: &Map<String, Value>) -> bool {
        let source = map.get("source").and_then(Value::as_str).unwrap_or("");
        !source.trim().is_empty()
            && Self::normalize_source(source) == Self::normalize_source(&self.source.visible_text)
    }

    fn legacy_refinement_owner(&mut self, map: &Map<String, Value>) -> Option<SentenceRef> {
        let source = Self::normalize_source(map.get("source").and_then(Value::as_str)?);
        if source.is_empty() {
            return None;
        }
        if let Some((owner, _)) = self
            .recent_source_by_sentence
            .iter()
            .rev()
            .find(|(_, known_source)| *known_source == source)
        {
            return Some(owner.clone());
        }
        if source != Self::normalize_source(&self.source.visible_text) {
            return None;
        }
        if let Some(owner) = self.source.owner.clone() {
            return Some(owner);
        }
        let owner = self.sentence_ref(&format!("legacy:{source}"));
        self.source.owner = Some(owner.clone());
        Some(owner)
    }

    fn normalize_source(source: &str) -> String {
        source.split_whitespace().collect::<String>().to_lowercase()
    }

    fn visible_outcome(
        before_source: String,
        before_translation: String,
        source: &str,
        translation: &str,
    ) -> DesktopCaptionOutcome {
        let source = (before_source != source).then(|| source.to_owned());
        let translation = (before_translation != translation).then(|| translation.to_owned());
        if source.is_none() && translation.is_none() {
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged)
        } else {
            DesktopCaptionOutcome::Change(DesktopCaptionChange {
                source,
                translation,
            })
        }
    }

    fn fingerprint(tokens: &[Value]) -> FinalTokensFingerprint {
        FinalTokensFingerprint(
            tokens
                .iter()
                .filter_map(Value::as_object)
                .map(|map| {
                    let is_separator = map
                        .get("is_separator")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    let line = if is_separator {
                        LineKind::Separator
                    } else if map.get("translation_status").and_then(Value::as_str)
                        == Some("translation")
                    {
                        LineKind::Translation
                    } else {
                        LineKind::Source
                    };
                    FinalTokenFingerprint {
                        is_separator,
                        line,
                        text: map
                            .get("text")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned(),
                        sentence_id: map
                            .get("llm_sentence_id")
                            .and_then(Value::as_str)
                            .map(str::to_owned),
                        is_final: map
                            .get("is_final")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                    }
                })
                .collect(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn apply(reducer: &mut DesktopCaptionReducer, value: Value) -> DesktopCaptionOutcome {
        reducer.apply_message(value.as_object().expect("desktop frame must be an object"))
    }

    fn change(source: Option<&str>, translation: Option<&str>) -> DesktopCaptionOutcome {
        DesktopCaptionOutcome::Change(DesktopCaptionChange {
            source: source.map(str::to_owned),
            translation: translation.map(str::to_owned),
        })
    }

    fn token(sentence_id: &str, text: &str, translation_status: &str) -> Value {
        json!({
            "text": text,
            "speaker": "1",
            "translation_status": translation_status,
            "llm_sentence_id": sentence_id,
            "is_final": true,
        })
    }

    fn update(final_tokens: Vec<Value>, non_final_tokens: Vec<Value>) -> Value {
        json!({
            "type": "update",
            "final_tokens": final_tokens,
            "non_final_tokens": non_final_tokens,
        })
    }

    fn final_source(sentence_id: &str, text: &str) -> Value {
        update(vec![token(sentence_id, text, "original")], vec![])
    }

    fn final_translation(sentence_id: &str, text: &str) -> Value {
        update(vec![token(sentence_id, text, "translation")], vec![])
    }

    fn separator() -> Value {
        update(
            vec![json!({"is_separator": true, "is_final": true})],
            vec![],
        )
    }

    fn source_draft(text: &str) -> Value {
        update(
            vec![],
            vec![json!({
                "text": text,
                "speaker": "1",
                "translation_status": "original",
                "is_final": false,
            })],
        )
    }

    fn translation_draft(text: &str) -> Value {
        update(
            vec![],
            vec![json!({
                "text": text,
                "speaker": "1",
                "translation_status": "translation",
                "is_final": false,
            })],
        )
    }

    #[test]
    fn desktop_caption_non_final_source_snapshot_replaces_live_source() {
        let mut reducer = DesktopCaptionReducer::default();

        assert_eq!(
            apply(&mut reducer, source_draft("Hello")),
            change(Some("Hello"), None)
        );
        assert_eq!(
            apply(&mut reducer, source_draft("Hello world")),
            change(Some("Hello world"), None)
        );
        assert_eq!(reducer.visible_source(), "Hello world");
    }

    #[test]
    fn desktop_caption_translation_snapshot_does_not_replace_source() {
        let mut reducer = DesktopCaptionReducer::default();

        let _ = apply(&mut reducer, source_draft("source A"));
        assert_eq!(
            apply(&mut reducer, translation_draft("translation A")),
            change(None, Some("translation A"))
        );
        assert_eq!(reducer.visible_source(), "source A");
        assert_eq!(reducer.visible_translation(), "translation A");
    }

    #[test]
    fn desktop_caption_final_token_wins_over_same_frame_draft() {
        let mut reducer = DesktopCaptionReducer::default();
        let _ = apply(&mut reducer, source_draft("A"));

        assert_eq!(
            apply(
                &mut reducer,
                update(
                    vec![token("A", "A", "original")],
                    vec![json!({
                        "text": "A draft",
                        "translation_status": "original",
                        "is_final": false,
                    })],
                ),
            ),
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged)
        );
        assert_eq!(reducer.visible_source(), "A");
    }

    #[test]
    fn desktop_caption_legacy_refine_can_use_non_final_source_owner() {
        let mut reducer = DesktopCaptionReducer::default();
        let _ = apply(
            &mut reducer,
            update(
                vec![],
                vec![
                    json!({
                        "text": "source A",
                        "translation_status": "original",
                        "llm_sentence_id": "A",
                        "is_final": false,
                    }),
                    json!({
                        "text": "draft A",
                        "translation_status": "translation",
                        "llm_sentence_id": "A",
                        "is_final": false,
                    }),
                ],
            ),
        );

        assert_eq!(
            apply(
                &mut reducer,
                json!({
                    "type": "refine_result",
                    "source": "source A",
                    "original_translation": "draft A",
                    "refined_translation": "refined A",
                    "no_change": false,
                }),
            ),
            change(None, Some("refined A"))
        );
    }

    #[test]
    fn desktop_caption_consecutive_identical_final_arrays_are_idempotent() {
        let mut reducer = DesktopCaptionReducer::default();
        let frame = final_source("A", "very");

        assert_eq!(
            apply(&mut reducer, frame.clone()),
            change(Some("very"), None)
        );
        assert_eq!(
            apply(&mut reducer, frame),
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged)
        );
        assert_eq!(reducer.visible_source(), "very");
    }

    #[test]
    fn desktop_caption_equal_deltas_inside_one_frame_are_both_appended() {
        let mut reducer = DesktopCaptionReducer::default();

        assert_eq!(
            apply(
                &mut reducer,
                update(
                    vec![
                        token("A", "very", "original"),
                        token("A", "very", "original"),
                    ],
                    vec![],
                ),
            ),
            change(Some("veryvery"), None)
        );
    }

    #[test]
    fn desktop_caption_strictly_longer_whole_line_replay_replaces_accumulator() {
        let mut reducer = DesktopCaptionReducer::default();

        let _ = apply(&mut reducer, final_source("A", "Hello"));
        assert_eq!(
            apply(&mut reducer, final_source("A", "Hello world")),
            change(Some("Hello world"), None)
        );
    }

    #[test]
    fn desktop_caption_separator_rollover_is_independent_per_line() {
        let mut reducer = DesktopCaptionReducer::default();
        let _ = apply(&mut reducer, final_source("A", "source A"));
        let _ = apply(&mut reducer, final_translation("A", "translation A"));
        let _ = apply(&mut reducer, separator());

        assert_eq!(
            apply(&mut reducer, final_source("B", "source B")),
            change(Some("source B"), None)
        );
        assert_eq!(reducer.visible_source(), "source B");
        assert_eq!(reducer.visible_translation(), "translation A");
        assert_eq!(reducer.translation_owner_id(), Some("A"));
    }

    #[test]
    fn desktop_caption_refine_for_translation_a_is_accepted_after_source_b() {
        let mut reducer = DesktopCaptionReducer::default();

        assert_eq!(
            apply(&mut reducer, final_source("A", "source A")),
            change(Some("source A"), None)
        );
        assert_eq!(
            apply(&mut reducer, final_translation("A", "draft A")),
            change(None, Some("draft A"))
        );
        assert_eq!(
            apply(&mut reducer, separator()),
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::Unchanged)
        );
        assert_eq!(
            apply(&mut reducer, final_source("B", "source B")),
            change(Some("source B"), None)
        );

        assert_eq!(
            apply(
                &mut reducer,
                json!({
                    "type": "refine_result",
                    "sentence_id": "A",
                    "source": "source A",
                    "original_translation": "draft A",
                    "refined_translation": "refined A",
                    "no_change": false,
                }),
            ),
            change(None, Some("refined A"))
        );
        assert_eq!(reducer.visible_source(), "source B");
        assert_eq!(reducer.visible_translation(), "refined A");
        assert_eq!(reducer.translation_owner_id(), Some("A"));
    }

    #[test]
    fn desktop_caption_out_of_order_refinement_is_replayed_after_source_arrives() {
        let mut reducer = DesktopCaptionReducer::default();

        assert_eq!(
            apply(
                &mut reducer,
                json!({
                    "type": "refine_result",
                    "sentence_id": "A",
                    "source": "source A",
                    "original_translation": "draft A",
                    "refined_translation": "refined A",
                    "no_change": false,
                }),
            ),
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::UncorrelatedRefinement)
        );

        assert_eq!(
            apply(&mut reducer, final_source("A", "source A")),
            change(Some("source A"), Some("refined A"))
        );

        let _ = apply(&mut reducer, separator());
        assert_eq!(
            apply(&mut reducer, final_source("B", "source B")),
            change(Some("source B"), None)
        );
        assert_eq!(reducer.visible_translation(), "refined A");
    }

    #[test]
    fn desktop_caption_legacy_refine_for_translation_a_survives_source_b() {
        let mut reducer = DesktopCaptionReducer::default();

        let _ = apply(&mut reducer, final_source("A", "source A"));
        let _ = apply(&mut reducer, separator());
        let _ = apply(&mut reducer, final_source("B", "source B"));

        assert_eq!(
            apply(
                &mut reducer,
                json!({
                    "type": "refine_result",
                    "source": "source A",
                    "original_translation": "draft A",
                    "refined_translation": "refined A",
                    "no_change": false,
                }),
            ),
            change(None, Some("refined A"))
        );
        assert_eq!(reducer.visible_source(), "source B");
        assert_eq!(reducer.visible_translation(), "refined A");
    }

    #[test]
    fn desktop_caption_refine_older_than_translation_owner_is_rejected() {
        let mut reducer = DesktopCaptionReducer::default();
        let _ = apply(&mut reducer, final_source("A", "source A"));
        let _ = apply(&mut reducer, separator());
        let _ = apply(&mut reducer, final_source("B", "source B"));
        let _ = apply(&mut reducer, final_translation("B", "translation B"));

        assert_eq!(
            apply(
                &mut reducer,
                json!({
                    "type": "refine_result",
                    "sentence_id": "A",
                    "source": "source A",
                    "original_translation": "translation A",
                    "refined_translation": "stale A",
                    "no_change": false,
                }),
            ),
            DesktopCaptionOutcome::Noop(DesktopCaptionNoopReason::StaleRefinement)
        );
        assert_eq!(reducer.visible_translation(), "translation B");
    }

    #[test]
    fn desktop_caption_clear_resets_lines_and_sentence_owners() {
        let mut reducer = DesktopCaptionReducer::default();
        let _ = apply(&mut reducer, final_source("A", "source A"));
        let _ = apply(&mut reducer, final_translation("A", "translation A"));

        assert_eq!(
            apply(&mut reducer, json!({"type": "clear"})),
            DesktopCaptionOutcome::Clear
        );
        assert_eq!(reducer.visible_source(), "");
        assert_eq!(reducer.visible_translation(), "");
        assert_eq!(reducer.translation_owner_id(), None);
    }
}

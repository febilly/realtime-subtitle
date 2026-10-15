use std::collections::{BTreeMap, VecDeque};
use std::io::ErrorKind;

use futures_util::StreamExt;
use serde_json::{Map, Value};
use thiserror::Error;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async, MaybeTlsStream, WebSocketStream};

use crate::manifest::OverlayManifest;
use crate::transcript::{
    CaptionEvent, CommittedSource, LiveSourceSnapshot, Refinement, SpeakerKey, TargetUpdate,
};
use crate::views::VrViewSettings;

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("bridge connect failed: {0}")]
    Connect(String),
    #[error("bridge url path must be /ws: {0}")]
    UnsupportedPath(String),
    #[error("bridge protocol error: {0}")]
    Protocol(String),
    #[error("bridge disconnected")]
    Disconnected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum TrackKind {
    Source,
    Translation,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct AccumKey {
    sentence_id: Option<String>,
    speaker: SpeakerKey,
    track: TrackKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FinalTokenFingerprint {
    is_separator: bool,
    track: TrackKind,
    speaker: SpeakerKey,
    sentence_id: Option<String>,
    text: String,
    language: Option<String>,
}

pub struct DesktopProtocol {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    pending: VecDeque<CaptionEvent>,
    accumulators: BTreeMap<AccumKey, String>,
    replace_on_next: BTreeMap<AccumKey, bool>,
    previous_final_fingerprint: Option<Vec<FinalTokenFingerprint>>,
}

impl DesktopProtocol {
    pub async fn connect(manifest: &OverlayManifest) -> Result<Self, BridgeError> {
        if !is_desktop_ws_url(&manifest.bridge_url) {
            return Err(BridgeError::UnsupportedPath(manifest.bridge_url.clone()));
        }
        let (stream, _) = connect_async(&manifest.bridge_url)
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;
        Ok(Self {
            stream,
            pending: VecDeque::new(),
            accumulators: BTreeMap::new(),
            replace_on_next: BTreeMap::new(),
            previous_final_fingerprint: None,
        })
    }

    pub async fn next_event(&mut self) -> Result<CaptionEvent, BridgeError> {
        loop {
            if let Some(event) = self.pending.pop_front() {
                return Ok(event);
            }
            self.fill_pending().await?;
        }
    }

    /// Await-free drain: pops a queued event, else polls the socket once.
    pub async fn try_next_event(&mut self) -> Option<Result<CaptionEvent, BridgeError>> {
        if let Some(event) = self.pending.pop_front() {
            return Some(Ok(event));
        }
        match futures_util::future::poll_immediate(self.fill_pending()).await {
            Some(Ok(())) => self.pending.pop_front().map(Ok),
            Some(Err(error)) => Some(Err(error)),
            None => None,
        }
    }

    async fn fill_pending(&mut self) -> Result<(), BridgeError> {
        loop {
            let message = self.stream.next().await.ok_or(BridgeError::Disconnected)?;
            let message = message.map_err(map_ws_error)?;
            match message {
                Message::Text(text) => {
                    self.process_text(&text);
                    return Ok(());
                }
                Message::Close(_) => return Err(BridgeError::Disconnected),
                Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => continue,
                Message::Binary(_) => {
                    self.pending.push_back(CaptionEvent::Activity);
                    return Ok(());
                }
            }
        }
    }

    fn process_text(&mut self, text: &str) {
        let Ok(value) = serde_json::from_str::<Value>(text) else {
            self.pending.push_back(CaptionEvent::Activity);
            return;
        };
        let Some(map) = value.as_object().cloned() else {
            self.pending.push_back(CaptionEvent::Activity);
            return;
        };
        match map.get("type").and_then(Value::as_str) {
            Some("update") => self.process_update(&map),
            Some("refine_result") => self.process_refine(&map),
            Some("clear") => {
                let preserve_existing = match map.get("preserve_existing") {
                    None => false,
                    Some(value) => match value.as_bool() {
                        Some(value) => value,
                        None => {
                            self.pending.push_back(CaptionEvent::Activity);
                            return;
                        }
                    },
                };
                self.accumulators.clear();
                self.replace_on_next.clear();
                self.previous_final_fingerprint = None;
                self.pending
                    .push_back(CaptionEvent::Clear { preserve_existing });
            }
            Some("shutdown") => self.pending.push_back(CaptionEvent::Shutdown),
            Some("vr_view_settings") => {
                let mut settings_map = map.clone();
                settings_map.remove("type");
                match serde_json::from_value::<VrViewSettings>(Value::Object(settings_map)) {
                    Ok(settings) if settings.validate().is_ok() => {
                        self.pending
                            .push_back(CaptionEvent::ViewSettingsChanged(settings));
                    }
                    _ => self.pending.push_back(CaptionEvent::Activity),
                }
            }
            _ => self.pending.push_back(CaptionEvent::Activity),
        }
        if self.pending.is_empty() {
            self.pending.push_back(CaptionEvent::Activity);
        }
    }

    fn process_update(&mut self, map: &Map<String, Value>) {
        let final_tokens = map
            .get("final_tokens")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let fingerprint = Self::fingerprint(final_tokens);
        let replayed = self.previous_final_fingerprint.as_ref() == Some(&fingerprint);
        self.previous_final_fingerprint = Some(fingerprint);
        if !replayed {
            self.accumulate_final_tokens(final_tokens);
        }
        self.emit_non_final(map);
    }

    /// Final tokens accumulate per `(sentence_id, speaker, track)`. Each frame
    /// contributes the concatenation of that key's tokens; a strictly longer
    /// cumulative prefix replaces the accumulator, otherwise the frame's text
    /// is appended as a true delta.
    fn accumulate_final_tokens(&mut self, tokens: &[Value]) {
        let mut groups: Vec<(AccumKey, String, Option<String>)> = Vec::new();
        for token in tokens {
            let Some(token) = token.as_object() else {
                continue;
            };
            if token
                .get("is_separator")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                self.flush_final_groups(&mut groups);
                let keys = self.accumulators.keys().cloned().collect::<Vec<_>>();
                for key in keys {
                    self.replace_on_next.insert(key, true);
                }
                continue;
            }
            if token.get("is_final").and_then(Value::as_bool) == Some(false) {
                continue;
            }
            let text = token.get("text").and_then(Value::as_str).unwrap_or("");
            let sentence_id = token
                .get("llm_sentence_id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .map(str::to_owned);
            let speaker = speaker_from(token);
            let language = token
                .get("language")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if text == "<end>" {
                self.flush_final_groups(&mut groups);
                self.pending.push_back(CaptionEvent::SourceEnd {
                    speaker,
                    sentence_id,
                });
                continue;
            }
            if text.is_empty() {
                continue;
            }
            let key = AccumKey {
                sentence_id,
                speaker,
                track: track_from(token),
            };
            match groups.iter_mut().find(|(known, _, _)| known == &key) {
                Some((_, accumulated, known_language)) => {
                    accumulated.push_str(text);
                    if known_language.is_none() {
                        *known_language = language;
                    }
                }
                None => groups.push((key, text.to_owned(), language)),
            }
        }

        self.flush_final_groups(&mut groups);
    }

    fn flush_final_groups(&mut self, groups: &mut Vec<(AccumKey, String, Option<String>)>) {
        for (key, text, language) in groups.drain(..) {
            let replace = self.replace_on_next.remove(&key).unwrap_or(false);
            let accumulator = self.accumulators.entry(key.clone()).or_default();
            if replace
                || accumulator.is_empty()
                || (text.starts_with(accumulator.as_str()) && text.len() > accumulator.len())
            {
                accumulator.clone_from(&text);
            } else {
                accumulator.push_str(&text);
            }
            let full_text = accumulator.clone();
            let event = match key.track {
                TrackKind::Source => CaptionEvent::SourceCommitted(CommittedSource {
                    speaker: key.speaker,
                    sentence_id: key.sentence_id,
                    text: full_text,
                    language,
                }),
                TrackKind::Translation => CaptionEvent::TargetCommitted(TargetUpdate {
                    speaker: key.speaker,
                    sentence_id: key.sentence_id,
                    text: full_text,
                    language,
                }),
            };
            self.pending.push_back(event);
        }
    }

    /// `non_final_tokens` is a replaceable snapshot. Exactly one live source
    /// row is emitted for the last contiguous speaker run; translation drafts
    /// are emitted once per speaking speaker.
    fn emit_non_final(&mut self, map: &Map<String, Value>) {
        let tokens = map
            .get("non_final_tokens")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let mut current: Option<(SpeakerKey, String, Option<String>, Option<String>)> = None;
        let mut translation_groups: Vec<(SpeakerKey, String, Option<String>, Option<String>)> =
            Vec::new();
        for token in tokens {
            let Some(token) = token.as_object() else {
                continue;
            };
            let text = token.get("text").and_then(Value::as_str).unwrap_or("");
            if text.is_empty() || text == "<end>" {
                continue;
            }
            let speaker = speaker_from(token);
            let sentence_id = token
                .get("llm_sentence_id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .map(str::to_owned);
            let language = token
                .get("language")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if track_from(token) == TrackKind::Translation {
                match translation_groups
                    .iter_mut()
                    .find(|(known, _, _, _)| known == &speaker)
                {
                    Some((_, accumulated, known_id, known_language)) => {
                        accumulated.push_str(text);
                        if known_id.is_none() {
                            *known_id = sentence_id;
                        }
                        if known_language.is_none() {
                            *known_language = language;
                        }
                    }
                    None => {
                        translation_groups.push((speaker, text.to_owned(), sentence_id, language))
                    }
                }
                continue;
            }
            match current.as_mut() {
                Some((known, accumulated, known_id, known_language)) if known == &speaker => {
                    accumulated.push_str(text);
                    if known_id.is_none() {
                        *known_id = sentence_id;
                    }
                    if known_language.is_none() {
                        *known_language = language;
                    }
                }
                _ => current = Some((speaker, text.to_owned(), sentence_id, language)),
            }
        }
        if let Some((speaker, text, sentence_id, language)) = current {
            self.pending
                .push_back(CaptionEvent::SourceLive(LiveSourceSnapshot {
                    speaker,
                    text,
                    sentence_id,
                    language,
                }));
        }
        for (speaker, text, sentence_id, language) in translation_groups {
            self.pending
                .push_back(CaptionEvent::TargetDraft(TargetUpdate {
                    speaker,
                    text,
                    sentence_id,
                    language,
                }));
        }
    }

    fn process_refine(&mut self, map: &Map<String, Value>) {
        if map.get("no_change").and_then(Value::as_bool) == Some(true) {
            self.pending.push_back(CaptionEvent::Activity);
            return;
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
        let text = if refined.is_empty() {
            original
        } else {
            refined
        };
        let Some(sentence_id) = map
            .get("sentence_id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
        else {
            self.pending.push_back(CaptionEvent::Activity);
            return;
        };
        if text.is_empty() {
            self.pending.push_back(CaptionEvent::Activity);
            return;
        }
        self.pending
            .push_back(CaptionEvent::RefinedTarget(Refinement {
                sentence_id: sentence_id.to_owned(),
                text: text.to_owned(),
                language: None,
            }));
    }

    fn fingerprint(tokens: &[Value]) -> Vec<FinalTokenFingerprint> {
        tokens
            .iter()
            .filter_map(Value::as_object)
            .map(|token| FinalTokenFingerprint {
                is_separator: token
                    .get("is_separator")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                track: track_from(token),
                speaker: speaker_from(token),
                sentence_id: token
                    .get("llm_sentence_id")
                    .and_then(Value::as_str)
                    .filter(|id| !id.is_empty())
                    .map(str::to_owned),
                text: token
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
                language: token
                    .get("language")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            })
            .collect()
    }
}

fn speaker_from(token: &Map<String, Value>) -> SpeakerKey {
    match token.get("speaker").and_then(Value::as_str) {
        Some(speaker) if !speaker.is_empty() => SpeakerKey::Diarized(speaker.to_owned()),
        _ => SpeakerKey::Anonymous,
    }
}

fn track_from(token: &Map<String, Value>) -> TrackKind {
    if token.get("translation_status").and_then(Value::as_str) == Some("translation") {
        TrackKind::Translation
    } else {
        TrackKind::Source
    }
}

fn map_ws_error(error: tokio_tungstenite::tungstenite::Error) -> BridgeError {
    use tokio_tungstenite::tungstenite::Error as WsError;
    match &error {
        WsError::ConnectionClosed | WsError::AlreadyClosed => BridgeError::Disconnected,
        WsError::Io(io)
            if matches!(
                io.kind(),
                ErrorKind::BrokenPipe
                    | ErrorKind::ConnectionAborted
                    | ErrorKind::ConnectionReset
                    | ErrorKind::NotConnected
                    | ErrorKind::UnexpectedEof
            ) =>
        {
            BridgeError::Disconnected
        }
        other => BridgeError::Protocol(other.to_string()),
    }
}

pub(crate) fn is_desktop_ws_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("ws://") else {
        return false;
    };
    let Some(path_offset) = rest.find('/') else {
        return false;
    };
    let authority = &rest[..path_offset];
    let path = &rest[path_offset..];
    let Some((host, port)) = authority.rsplit_once(':') else {
        return false;
    };
    host == "127.0.0.1" && port.parse::<u16>().is_ok_and(|port| port != 0) && path == "/ws"
}

#[cfg(test)]
mod tests {
    use super::is_desktop_ws_url;

    #[test]
    fn desktop_ws_url_matches_only_the_local_unauthenticated_endpoint() {
        assert!(is_desktop_ws_url("ws://127.0.0.1:1/ws"));
        assert!(is_desktop_ws_url("ws://127.0.0.1:65535/ws"));
        assert!(!is_desktop_ws_url("ws://127.0.0.1:1/vr_ws"));
        assert!(!is_desktop_ws_url("ws://127.0.0.1:1/workspace"));
        assert!(!is_desktop_ws_url("ws://127.0.0.1:1"));
        assert!(!is_desktop_ws_url("ws://127.0.0.1:0/ws"));
        assert!(!is_desktop_ws_url("ws://127.0.0.1:1/ws?token=x"));
        assert!(!is_desktop_ws_url("wss://127.0.0.1:1/ws"));
        assert!(!is_desktop_ws_url("ws://192.168.0.2:1/ws"));
        assert!(!is_desktop_ws_url("ws://example.test:1/ws"));
    }
}

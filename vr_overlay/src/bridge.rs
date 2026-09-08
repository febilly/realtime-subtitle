use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::ErrorKind;
use thiserror::Error;
use tokio::net::TcpStream;
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};

use crate::logging::OverlayLoggingMode;
use crate::manifest::OverlayManifest;
use crate::state::OverlayPresentationSnapshot;

/// A caption update for the two independently replaceable subtitle lines.
///
/// `source_updated` and `translation_updated` deliberately remain separate:
/// a source event must never blank or replace the last translation, and a
/// translation event must be rendered immediately without waiting for the
/// next source sentence.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct CaptionUpdate {
    #[serde(default)]
    pub self_text: String,
    #[serde(default)]
    pub peer: String,
    #[serde(default)]
    pub source_updated: bool,
    #[serde(default)]
    pub translation_updated: bool,
    #[serde(default)]
    pub clear: bool,
}

/// A runtime-control request sent over the bridge (e.g. switching VrMode/副标题日志级别).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeControl {
    #[serde(default)]
    pub logging_mode: OverlayLoggingMode,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OverlayBridgeEvent {
    Captions(CaptionUpdate),
    Snapshot(OverlayPresentationSnapshot),
    Control(BridgeControl),
    Shutdown,
    Heartbeat,
    /// A message from the desktop WebSocket that is unrelated to captions.
    Noop,
    /// 桥接认证失败 (reason 来自 auth_error 消息)。
    AuthError(String),
}

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("bridge connect failed: {0}")]
    Connect(String),
    /// 桥接认证失败 — 与普通连接失败区分开, 以便 runtime 映射到
    /// `StartupError::BridgeAuth`(exit 12) 而非 `Bridge`(exit 1)。
    #[error("bridge auth failed: {0}")]
    Auth(String),
    #[error("bridge protocol error: {0}")]
    Protocol(String),
    #[error("bridge disconnected")]
    Disconnected,
}

pub struct BridgeClient {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    snapshot_mode: bool,
    desktop_lines: DesktopCaptionLines,
}

#[derive(Debug, Default)]
struct DesktopCaptionLines {
    source: String,
    translation: String,
    source_needs_replace: bool,
    translation_needs_replace: bool,
}

impl DesktopCaptionLines {
    fn reset(&mut self) {
        self.source.clear();
        self.translation.clear();
        self.source_needs_replace = false;
        self.translation_needs_replace = false;
    }

    fn append_line(current: &mut String, needs_replace: &mut bool, text: &str) {
        if text.is_empty() {
            return;
        }
        if *needs_replace || current.is_empty() {
            current.clear();
            current.push_str(text);
            *needs_replace = false;
            return;
        }
        // The desktop endpoint sends incremental tokens. Ignore an exact
        // replay, which can occur around provider stream rollover, but append
        // ordinary new token text in arrival order.
        if current == text || current.ends_with(text) {
            return;
        }
        if text.starts_with(current.as_str()) {
            // Some providers replay the whole accumulated line after a
            // rollover rather than sending a token delta.
            *current = text.to_string();
            return;
        }
        current.push_str(text);
    }

    fn belongs_to_line(current: &str, needs_replace: bool, incoming: &str) -> bool {
        current.is_empty()
            || needs_replace
            || current == incoming
            || current.starts_with(incoming)
            || incoming.starts_with(current)
    }

    fn apply_tokens(&mut self, tokens: Option<&Value>) -> (bool, bool) {
        let Some(Value::Array(tokens)) = tokens else {
            return (false, false);
        };
        let mut source_updated = false;
        let mut translation_updated = false;
        for token in tokens {
            let Some(map) = token.as_object() else {
                continue;
            };
            if map
                .get("is_separator")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                // A separator closes the current pair. Keep both visible
                // strings until their own next token arrives, but make the
                // next source and translation replace their respective rows
                // independently.
                self.source_needs_replace = true;
                self.translation_needs_replace = true;
                continue;
            }
            let Some(text) = map.get("text").and_then(Value::as_str) else {
                continue;
            };
            if text.is_empty() || map.get("is_final").and_then(Value::as_bool) == Some(false) {
                continue;
            }
            match map.get("translation_status").and_then(Value::as_str) {
                Some("translation") => {
                    Self::append_line(
                        &mut self.translation,
                        &mut self.translation_needs_replace,
                        text,
                    );
                    translation_updated = true;
                }
                _ => {
                    Self::append_line(&mut self.source, &mut self.source_needs_replace, text);
                    source_updated = true;
                }
            }
        }
        (source_updated, translation_updated)
    }

    fn update(&self, source_updated: bool, translation_updated: bool) -> CaptionUpdate {
        CaptionUpdate {
            self_text: self.source.clone(),
            peer: self.translation.clone(),
            source_updated,
            translation_updated,
            clear: false,
        }
    }
}

impl BridgeClient {
    /// Connect to the hub bridge, authenticate, and read the initial snapshot.
    ///
    /// Returns the connected client alongside the initial `OverlayPresentationSnapshot`
    /// (the first `snapshot` message the hub sends once auth is accepted).
    pub async fn connect(
        manifest: &OverlayManifest,
    ) -> Result<(Self, OverlayPresentationSnapshot), BridgeError> {
        // Existing launchers may still write `/vr_ws` into the manifest. Use
        // that value as a compatibility alias for the original desktop `/ws`
        // stream so the Rust layer can be upgraded without touching Python.
        let connect_url = manifest
            .bridge_url
            .strip_suffix("/vr_ws")
            .map(|base| format!("{base}/ws"))
            .unwrap_or_else(|| manifest.bridge_url.clone());
        let (mut stream, _response) = connect_async(&connect_url)
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        // The normal desktop server exposes every subtitle event on `/ws`;
        // using it directly keeps the PR confined to this Rust layer and
        // removes the Python mirror/translation patch. A manifest with no
        // `/ws` path remains supported for the old authenticated snapshot
        // protocol used by the standalone probe tests.
        let snapshot_mode = !connect_url.contains("/ws");

        if snapshot_mode {
            let auth = serde_json::json!({
                "type": "auth",
                "session_token": manifest.session_token
            });
            stream
                .send(Message::Text(auth.to_string().into()))
                .await
                .map_err(|error| BridgeError::Connect(error.to_string()))?;
        }

        let mut client = Self {
            stream,
            snapshot_mode,
            desktop_lines: DesktopCaptionLines::default(),
        };

        if !snapshot_mode {
            return Ok((client, OverlayPresentationSnapshot::default()));
        }

        // Consume the initial snapshot (tolerate heartbeats until the first
        // `snapshot` message arrives). Auth rejection or a missing initial
        // snapshot fails fast instead of hanging.
        loop {
            match client.next_message().await? {
                OverlayBridgeEvent::Snapshot(snapshot) => return Ok((client, snapshot)),
                OverlayBridgeEvent::AuthError(reason) => return Err(BridgeError::Auth(reason)),
                OverlayBridgeEvent::Heartbeat => continue,
                other => {
                    return Err(BridgeError::Connect(format!(
                        "expected initial snapshot, got unexpected event: {other:?}"
                    )))
                }
            }
        }
    }

    pub async fn send_json(&mut self, payload: Value) -> Result<(), BridgeError> {
        self.stream
            .send(Message::Text(payload.to_string().into()))
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))
    }

    pub async fn close(&mut self) -> Result<(), BridgeError> {
        match self.stream.close(None).await {
            Ok(()) => Ok(()),
            Err(tokio_tungstenite::tungstenite::Error::ConnectionClosed)
            | Err(tokio_tungstenite::tungstenite::Error::AlreadyClosed) => Ok(()),
            Err(error) => Err(BridgeError::Connect(error.to_string())),
        }
    }

    pub async fn next_message(&mut self) -> Result<OverlayBridgeEvent, BridgeError> {
        use tokio_tungstenite::tungstenite::Error as WsError;

        let message = loop {
            let next = self.stream.next().await.ok_or(BridgeError::Disconnected)?;
            let message = next.map_err(|e| match &e {
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
            })?;
            match message {
                Message::Text(_) | Message::Close(_) => break message,
                Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => continue,
                Message::Binary(_) => {
                    return Err(BridgeError::Protocol(
                        "binary messages are not supported".into(),
                    ))
                }
            }
        };

        match message {
            Message::Text(text) => {
                let payload: Value = serde_json::from_str(&text)
                    .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                let Value::Object(map) = payload else {
                    return Err(BridgeError::Protocol(
                        "payload must be a JSON object".into(),
                    ));
                };

                if !self.snapshot_mode {
                    return Ok(self.parse_desktop_message(&map));
                }

                match map.get("type").and_then(Value::as_str) {
                    Some("heartbeat") => Ok(OverlayBridgeEvent::Heartbeat),
                    Some("shutdown") => Ok(OverlayBridgeEvent::Shutdown),
                    Some("runtime_control") => {
                        let control_val = match map.get("payload") {
                            Some(val) => val.clone(),
                            None => Value::Object(map.clone()),
                        };
                        let control: BridgeControl = serde_json::from_value(control_val)
                            .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                        Ok(OverlayBridgeEvent::Control(control))
                    }
                    Some("auth_error") => Ok(OverlayBridgeEvent::AuthError(
                        map.get("reason")
                            .and_then(Value::as_str)
                            .unwrap_or("bad token")
                            .to_string(),
                    )),
                    Some("snapshot") => {
                        let snapshot_val = match map.get("payload") {
                            Some(val) => val.clone(),
                            None => Value::Object(map.clone()),
                        };
                        let snapshot: OverlayPresentationSnapshot =
                            serde_json::from_value(snapshot_val)
                                .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                        Ok(OverlayBridgeEvent::Snapshot(snapshot))
                    }
                    _ => Ok(OverlayBridgeEvent::Noop),
                }
            }
            Message::Close(_) => Err(BridgeError::Disconnected),
            _ => unreachable!(),
        }
    }

    fn parse_desktop_message(
        &mut self,
        map: &serde_json::Map<String, Value>,
    ) -> OverlayBridgeEvent {
        match map.get("type").and_then(Value::as_str) {
            Some("update") => {
                let (mut source_updated, mut translation_updated) =
                    self.desktop_lines.apply_tokens(map.get("final_tokens"));
                // Draft tokens are intentionally not appended: they are
                // replaced by the provider on the next update.  Final tokens
                // and refine_result are the authoritative rows we submit to
                // SteamVR.
                if !source_updated && !translation_updated {
                    (source_updated, translation_updated) =
                        self.desktop_lines.apply_tokens(map.get("tokens"));
                }
                if source_updated || translation_updated {
                    OverlayBridgeEvent::Captions(
                        self.desktop_lines
                            .update(source_updated, translation_updated),
                    )
                } else {
                    OverlayBridgeEvent::Noop
                }
            }
            Some("refine_result") => {
                let source = map
                    .get("source")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim();
                let original = map
                    .get("original_translation")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim();
                let refined = map
                    .get("refined_translation")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim();
                let source_accept = !source.is_empty()
                    && DesktopCaptionLines::belongs_to_line(
                        &self.desktop_lines.source,
                        self.desktop_lines.source_needs_replace,
                        source,
                    );
                if source_accept {
                    self.desktop_lines.source = source.to_string();
                    self.desktop_lines.source_needs_replace = false;
                }
                let translation = if !refined.is_empty() {
                    refined
                } else {
                    original
                };
                // A refine may legitimately change the draft translation
                // text, so source correlation authorizes that replacement.
                // If the source is stale, do not let its translation roll the
                // VR line back to an older sentence.
                let translation_accept = !translation.is_empty()
                    && if !source.is_empty() {
                        // A source-bearing refine is correlated by source. A
                        // pending translation slot alone is not permission
                        // for an older result to roll back the newer line.
                        source_accept
                    } else {
                        DesktopCaptionLines::belongs_to_line(
                            &self.desktop_lines.translation,
                            self.desktop_lines.translation_needs_replace,
                            translation,
                        )
                    };
                if translation_accept {
                    // This assignment is the critical path: submit the
                    // completed translation now, independent of the next
                    // source sentence.
                    self.desktop_lines.translation = translation.to_string();
                    self.desktop_lines.translation_needs_replace = false;
                }
                if !source_accept && !translation_accept {
                    OverlayBridgeEvent::Noop
                } else {
                    OverlayBridgeEvent::Captions(
                        self.desktop_lines.update(source_accept, translation_accept),
                    )
                }
            }
            Some("clear") => {
                self.desktop_lines.reset();
                OverlayBridgeEvent::Captions(CaptionUpdate {
                    clear: true,
                    ..CaptionUpdate::default()
                })
            }
            Some("heartbeat") => OverlayBridgeEvent::Heartbeat,
            Some("shutdown") => OverlayBridgeEvent::Shutdown,
            _ => OverlayBridgeEvent::Noop,
        }
    }
}

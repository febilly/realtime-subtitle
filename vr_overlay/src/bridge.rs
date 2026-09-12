use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::ErrorKind;
use thiserror::Error;
use tokio::net::TcpStream;
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};

use crate::desktop_caption::{DesktopCaptionOutcome, DesktopCaptionReducer};
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
    desktop_lines: DesktopCaptionReducer,
}

fn bridge_url_path_range(url: &str) -> Option<(usize, usize)> {
    let authority_start = url.find("://").map(|index| index + 3).unwrap_or(0);
    let path_start = authority_start + url.get(authority_start..)?.find('/')?;
    let path_and_suffix = &url[path_start..];
    let query = path_and_suffix.find('?');
    let fragment = path_and_suffix.find('#');
    let path_end = match (query, fragment) {
        (Some(query), Some(fragment)) => path_start + query.min(fragment),
        (Some(query), None) => path_start + query,
        (None, Some(fragment)) => path_start + fragment,
        (None, None) => url.len(),
    };
    Some((path_start, path_end))
}

fn is_desktop_bridge_url(url: &str) -> bool {
    bridge_url_path_range(url)
        .map(|(start, end)| &url[start..end] == "/ws")
        .unwrap_or(false)
}

fn normalize_desktop_bridge_url(url: &str) -> String {
    url.to_owned()
}

impl BridgeClient {
    /// Connect to the hub bridge, authenticate, and read the initial snapshot.
    ///
    /// Returns the connected client alongside the initial
    /// `OverlayPresentationSnapshot` (the first `snapshot` message the hub
    /// sends once auth is accepted). The raw `/ws` path remains available as a
    /// deliberately unauthenticated reducer/test protocol.
    pub async fn connect(
        manifest: &OverlayManifest,
    ) -> Result<(Self, OverlayPresentationSnapshot), BridgeError> {
        // `/ws` carries raw desktop events for reducer tests and explicit raw
        // integrations. `/vr_ws` is the packaged desktop's authenticated
        // snapshot channel; it supplies initial state/calibration and survives
        // recognition restarts, so it must remain a distinct protocol.
        let vr_ws_path = bridge_url_path_range(&manifest.bridge_url)
            .map(|(start, end)| &manifest.bridge_url[start..end] == "/vr_ws")
            .unwrap_or(false);
        let connect_url = normalize_desktop_bridge_url(&manifest.bridge_url);
        let (mut stream, _response) = connect_async(&connect_url)
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        // The normal packaged launcher uses `/vr_ws`; only an explicit `/ws`
        // manifest opts into the raw desktop event reducer. Every other path
        // uses the authenticated snapshot protocol.
        let snapshot_mode = !is_desktop_bridge_url(&connect_url);
        eprintln!(
            "[overlay][BRIDGE] protocol={} vr_ws_path={vr_ws_path}",
            if snapshot_mode {
                "snapshot"
            } else {
                "desktop_ws"
            }
        );

        if snapshot_mode {
            let auth = serde_json::json!({
                "type": "auth",
                "session_token": ""
            });
            stream
                .send(Message::Text(auth.to_string().into()))
                .await
                .map_err(|error| BridgeError::Connect(error.to_string()))?;
        }

        let mut client = Self {
            stream,
            snapshot_mode,
            desktop_lines: DesktopCaptionReducer::default(),
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
        match self.desktop_lines.apply_message(map) {
            DesktopCaptionOutcome::Change(change) => OverlayBridgeEvent::Captions(CaptionUpdate {
                source_updated: change.source.is_some(),
                translation_updated: change.translation.is_some(),
                self_text: change.source.unwrap_or_default(),
                peer: change.translation.unwrap_or_default(),
                clear: false,
            }),
            DesktopCaptionOutcome::Clear => OverlayBridgeEvent::Captions(CaptionUpdate {
                clear: true,
                ..CaptionUpdate::default()
            }),
            DesktopCaptionOutcome::Noop(_) => match map.get("type").and_then(Value::as_str) {
                Some("heartbeat") => OverlayBridgeEvent::Heartbeat,
                Some("shutdown") => OverlayBridgeEvent::Shutdown,
                _ => OverlayBridgeEvent::Noop,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{is_desktop_bridge_url, normalize_desktop_bridge_url};

    #[test]
    fn desktop_bridge_protocol_matches_only_exact_paths() {
        assert!(is_desktop_bridge_url("ws://127.0.0.1:1/ws"));
        assert!(!is_desktop_bridge_url("ws://127.0.0.1:1/vr_ws?token=x"));
        assert!(!is_desktop_bridge_url("ws://127.0.0.1:1/workspace"));
        assert!(!is_desktop_bridge_url("ws://127.0.0.1:1/snapshot?next=/ws"));
    }

    #[test]
    fn vr_ws_keeps_the_authenticated_snapshot_protocol() {
        assert_eq!(
            normalize_desktop_bridge_url("ws://localhost/vr_ws?mode=live#caption"),
            "ws://localhost/vr_ws?mode=live#caption"
        );
    }
}

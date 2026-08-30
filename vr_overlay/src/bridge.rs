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

/// A simple caption update from the Python hub.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct CaptionUpdate {
    #[serde(default)]
    pub self_text: String,
    #[serde(default)]
    pub peer: String,
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
}

impl BridgeClient {
    /// Connect to the hub bridge, authenticate, and read the initial snapshot.
    ///
    /// Returns the connected client alongside the initial `OverlayPresentationSnapshot`
    /// (the first `snapshot` message the hub sends once auth is accepted).
    pub async fn connect(
        manifest: &OverlayManifest,
    ) -> Result<(Self, OverlayPresentationSnapshot), BridgeError> {
        let (mut stream, _response) = connect_async(&manifest.bridge_url)
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        // 发送 auth 消息
        let auth = serde_json::json!({
            "type": "auth",
            "session_token": manifest.session_token
        });
        stream
            .send(Message::Text(auth.to_string().into()))
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        let mut client = Self { stream };

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
                    _ => {
                        // Treat as a caption update
                        let update: CaptionUpdate = serde_json::from_value(Value::Object(map))
                            .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                        Ok(OverlayBridgeEvent::Captions(update))
                    }
                }
            }
            Message::Close(_) => Err(BridgeError::Disconnected),
            _ => unreachable!(),
        }
    }
}

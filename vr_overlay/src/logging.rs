use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::RwLock;
use tokio::io::{self, AsyncWrite, AsyncWriteExt};
use tokio::sync::Mutex;

type LogStream = std::pin::Pin<Box<dyn AsyncWrite + Send>>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OverlayLoggingMode {
    #[default]
    Basic,
    Detailed,
}

impl OverlayLoggingMode {
    fn allows_info(self) -> bool {
        matches!(self, Self::Detailed)
    }
}

pub struct OverlayLogger {
    stdout: Mutex<LogStream>,
    stderr: Mutex<LogStream>,
    mode: RwLock<OverlayLoggingMode>,
}

impl OverlayLogger {
    pub async fn open(
        _log_dir: impl AsRef<std::path::Path>,
        mode: OverlayLoggingMode,
    ) -> io::Result<Self> {
        Ok(Self::from_streams(
            Box::pin(tokio::io::stdout()),
            Box::pin(tokio::io::stderr()),
            mode,
        ))
    }

    pub async fn info(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("INFO", message.as_ref()).await
    }

    pub async fn warn(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("WARN", message.as_ref()).await
    }

    pub async fn error(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("ERROR", message.as_ref()).await
    }

    pub async fn emit_stdout_event(&self, payload: &Value) -> io::Result<()> {
        self.write_stream_line(true, &format!("EVENT {}", payload))
            .await
    }

    pub async fn emit_stderr_event(&self, payload: &Value) -> io::Result<()> {
        self.write_stream_line(false, &format!("EVENT {}", payload))
            .await
    }

    pub fn set_mode(&self, mode: OverlayLoggingMode) {
        if let Ok(mut current) = self.mode.write() {
            *current = mode;
        }
    }

    pub fn is_detailed(&self) -> bool {
        self.mode
            .read()
            .map(|guard| matches!(*guard, OverlayLoggingMode::Detailed))
            .unwrap_or(false)
    }

    fn from_streams(stdout: LogStream, stderr: LogStream, mode: OverlayLoggingMode) -> Self {
        Self {
            stdout: Mutex::new(stdout),
            stderr: Mutex::new(stderr),
            mode: RwLock::new(mode),
        }
    }

    async fn log_line(&self, level: &str, message: &str) -> io::Result<()> {
        if level == "INFO" {
            let mode = self.mode.read().map(|guard| *guard).unwrap_or_default();
            if !mode.allows_info() {
                return Ok(());
            }
        }
        self.write_stream_line(level != "ERROR", &format!("[overlay][{level}] {message}"))
            .await
    }

    async fn write_stream_line(&self, stdout: bool, line: &str) -> io::Result<()> {
        if stdout {
            let mut stream = self.stdout.lock().await;
            stream.write_all(line.as_bytes()).await?;
            stream.write_all(b"\n").await?;
            stream.flush().await
        } else {
            let mut stream = self.stderr.lock().await;
            stream.write_all(line.as_bytes()).await?;
            stream.write_all(b"\n").await?;
            stream.flush().await
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::pin::Pin;
    use std::sync::{Arc, Mutex as StdMutex};
    use std::task::{Context, Poll};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use tokio::io::AsyncWrite;

    #[derive(Clone, Default)]
    struct RecordingSink {
        buffer: Arc<StdMutex<Vec<u8>>>,
    }

    impl RecordingSink {
        fn new() -> Self {
            Self::default()
        }

        fn bytes(&self) -> Vec<u8> {
            self.buffer.lock().unwrap().clone()
        }
    }

    impl AsyncWrite for RecordingSink {
        fn poll_write(
            self: Pin<&mut Self>,
            _cx: &mut Context<'_>,
            buf: &[u8],
        ) -> Poll<Result<usize, io::Error>> {
            self.buffer.lock().unwrap().extend_from_slice(buf);
            Poll::Ready(Ok(buf.len()))
        }

        fn poll_flush(self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<Result<(), io::Error>> {
            Poll::Ready(Ok(()))
        }

        fn poll_shutdown(
            self: Pin<&mut Self>,
            _cx: &mut Context<'_>,
        ) -> Poll<Result<(), io::Error>> {
            Poll::Ready(Ok(()))
        }
    }

    fn unique_log_dir(name: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or(Duration::ZERO)
            .as_nanos();
        std::env::temp_dir().join(format!("rinbridge-overlay-logger-{name}-{nonce}"))
    }

    #[tokio::test]
    async fn overlay_logger_does_not_create_dedicated_log_file() {
        let log_dir = unique_log_dir("no-file");
        let log_path = log_dir.join("rinbridge_overlay.log");

        let logger = OverlayLogger::open(&log_dir, OverlayLoggingMode::Detailed)
            .await
            .unwrap();
        logger.info("hello").await.unwrap();

        assert!(!log_path.exists());
    }

    #[tokio::test]
    async fn overlay_logger_routes_info_and_error_lines_to_streams_only() {
        let stdout = RecordingSink::new();
        let stderr = RecordingSink::new();
        let logger = OverlayLogger::from_streams(
            Box::pin(stdout.clone()),
            Box::pin(stderr.clone()),
            OverlayLoggingMode::Detailed,
        );

        logger.info("child line").await.unwrap();
        logger.error("bad line").await.unwrap();

        assert_eq!(
            String::from_utf8(stdout.bytes()).unwrap(),
            "[overlay][INFO] child line\n"
        );
        assert_eq!(
            String::from_utf8(stderr.bytes()).unwrap(),
            "[overlay][ERROR] bad line\n"
        );
    }

    #[tokio::test]
    async fn overlay_logger_suppresses_info_lines_in_basic_mode() {
        let stdout = RecordingSink::new();
        let stderr = RecordingSink::new();
        let logger = OverlayLogger::from_streams(
            Box::pin(stdout.clone()),
            Box::pin(stderr.clone()),
            OverlayLoggingMode::Basic,
        );

        logger.info("hidden").await.unwrap();
        logger.warn("visible").await.unwrap();

        assert_eq!(
            String::from_utf8(stdout.bytes()).unwrap(),
            "[overlay][WARN] visible\n"
        );
        assert_eq!(String::from_utf8(stderr.bytes()).unwrap(), "");
    }

    #[tokio::test]
    async fn overlay_logger_applies_runtime_mode_updates() {
        let stdout = RecordingSink::new();
        let stderr = RecordingSink::new();
        let logger = OverlayLogger::from_streams(
            Box::pin(stdout.clone()),
            Box::pin(stderr.clone()),
            OverlayLoggingMode::Basic,
        );

        logger.info("hidden").await.unwrap();
        logger.set_mode(OverlayLoggingMode::Detailed);
        logger.info("visible").await.unwrap();

        assert_eq!(
            String::from_utf8(stdout.bytes()).unwrap(),
            "[overlay][INFO] visible\n"
        );
        assert_eq!(String::from_utf8(stderr.bytes()).unwrap(), "");
    }
}

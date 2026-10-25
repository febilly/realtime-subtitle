# Soniox API references

This project talks to the Soniox real-time Speech-to-Text WebSocket API. The
upstream documentation it relies on lives on soniox.com:

- [Real-time transcription](https://soniox.com/docs/stt/rt/real-time-transcription) — WebSocket session, config fields, audio formats, token stream
- [Real-time translation](https://soniox.com/docs/stt/rt/real-time-translation) — one-way / two-way translation and `translation_status`
- [Endpoint detection](https://soniox.com/docs/stt/rt/endpoint-detection) — `<end>` tokens and when tokens finalize
- [Speaker diarization](https://soniox.com/docs/stt/concepts/speaker-diarization) — `speaker` labels on tokens
- [Supported languages](https://soniox.com/docs/stt/concepts/supported-languages) — source of `SONIOX_SUPPORTED_LANGUAGE_CODES` in `config.py`
- [WebSocket API reference](https://soniox.com/docs/stt/api-reference/websocket-api) — error responses and message shapes

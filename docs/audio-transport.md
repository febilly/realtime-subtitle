# 托管 ASR 音频传输

| 项目 | Soniox | Gemini |
|---|---|---|
| 采集 | 16 kHz、单声道 PCM16；40 ms / 640 sample | 16 kHz、单声道 PCM16；100 ms / 1600 sample |
| 编码 | 32 kbps CBR Opus，40 ms / 160 字节每包 | 32 kbps CBR Opus，20 ms / 80 字节每包 |
| 常规发送 | 每条二进制消息一个 Ogg page，40 ms | 每条二进制消息一个 OPB1 批次，100 ms |
| 常规消息体 | 188 字节，首条另含 91 字节 Ogg headers | 414 字节：400 字节音频 + 14 字节批次头 |
| 消息体流量 | 37.6 kbps | 33.12 kbps |

消息体流量按连续音频计算，不含 WebSocket、TLS、网络协议开销和传输压缩。
两条路径都用随包 libopus 编码，VAD 与音频路由仍处理原始 PCM。
Gemini 的 100 ms 采集和上游 PCM 消息时长依据
[Live Translation 官方说明](https://ai.google.dev/gemini-api/docs/live-api/live-translate#sending-audio)。

Soniox 每个 Opus 包立即写入单独 Ogg page。路由恢复或静音补帧一次提供
多包 PCM 时，发送层也逐包发消息，不额外等待 40 ms；消息的音频时长和
消息之间的墙钟间隔是两个概念。结束时追加有效 EOS 页，保留 codec
lookahead 和真实输入时长，末条结束页可含补零音频。

## Gemini OPB1

每条二进制消息为 ASCII `OPB1`，后接五组：

```text
uint16 big-endian packet length + one complete mono 20 ms Opus packet
```

解码 Worker 按顺序解码五包，拼成一条 3200 字节、100 ms PCM16 消息发给
Google。尾部不足 100 ms 时补零，然后发送原有 JSON `audioStreamEnd`。
setup、LLM 控制和其他 JSON 消息保持原协议。

Worker 同时兼容旧 Opus JSON/Base64、旧单包二进制和 PCM JSON；
未声明 `audio_codec` 的旧 PCM 路由保持不变。
新 Worker 在 `setupComplete` 响应旁添加
`relayAudioFormats: ["opus-batch-v1"]`。客户端收到该标记才启用 OPB1；
连接旧 Worker 时沿用旧 JSON/Base64 Opus 发送方式，采集仍为 100 ms。
自带 Gemini Key 模式继续直连 Google，发送 PCM JSON。

先升级 prod/staging 解码 Worker，再发布客户端。DO、VPS 已有二进制透传，
本次补充了混合格式和换 Key 后的转发测试。本地代码与离线模拟验证不表示
生产已部署，也不代表真实模型识别延迟的测量。

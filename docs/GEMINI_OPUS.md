# Gemini 托管音频传输

Gemini 托管模式（RELAY_MODE=true）现在发送 **32 kbps CBR、单声道、40ms/packet 的原始 Opus**。采集采样率仍为 16kHz，32k 是码率而非采样率。用户自带 Gemini Key 的直连模式仍发送原来的 PCM；直连临时 Key 模式也保持 PCM。

托管连接由 subtitle-server 的 connect API 返回 URL/headers/票据，客户端仍连接该 URL。服务端随后经过独立 Gemini PCM/Opus Worker，把 Opus 解码为官方要求的 PCM；客户端不用获得上游 Key、连接 Worker 或新增参数。所有返回音频在托管链路被丢弃，转写与控制消息保留。

音频路由和 VAD 仍传入 mono PCM16 little-endian。gemini_client.GeminiLiveStream 在每条托管连接内创建 opus_audio.RawOpusEncoder，send(bytes) 按序发送 realtimeInput.audio JSON，MIME 为 audio/opus;rate=16000;channels=1，data 为单个完整 Opus packet 的标准 Base64（40ms CBR 包为160字节）。send(str) 保留 JSON 控制消息，包括托管 LLM。

finalize() 先排空编码器缓存和 lookahead，再发送 audioStreamEnd；停止、静音休眠和轮换旧流使用同一路径。close() 释放编码器；每次重连新建编码状态。Google 官方接口不接受 Opus，不能把托管路径的音频直接交给官方。

Soniox 保持现有 soniox_audio.py 的 32 kbps Ogg Opus。Gemini raw Opus 与 Soniox Ogg 不能交换：Gemini 不接收 OggS/OpusHead，Soniox 配置 audio_format=ogg，需要完整的 Ogg 流头、顺序页和 EOS。

Opus 编码不再依赖 PyAV/FFmpeg：opus_native.py 通过 ctypes 直接调用仓库内 libopus/win64/opus.dll（约 500 KB，随 exe 分发），Ogg mux 由 soniox_audio.py 手写实现（RFC 7845）。av==16.1.0 仅在本地跑测试时充当 Opus 解码器，不再进入 requirements 与打包 spec。

验证（项目现有虚拟环境；依赖同步仍使用 uv）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gemini_client_setup.py tests/test_soniox_audio.py tests/test_audio_router.py tests/test_gemini_session_response.py tests/test_hosted_llm.py -q
```

后台发布应先上线兼容 PCM 的 Worker，再上线 server DO/VPS/gateway，最后发布网页和 Python 客户端。旧 PCM 客户端继续可用。本次客户端为源码修改，尚未打包新的 exe。

完整服务器对接文档位于配套 subtitle-server 项目 docs/opus-audio-relay.md；独立 Worker 协议与部署记录位于 gemini-opus-worker/README.md、DEPLOYMENT.md。

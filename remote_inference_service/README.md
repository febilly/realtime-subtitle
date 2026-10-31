# Yakutan / Real-Time Subtitle 共用远程推理

## 现在怎么用

1. 双击本目录的 `tunnel_to_gpu4038.bat`。它使用已有的 SSH 配置别名 `gpu4038`，检查远端服务，必要时启动原有部署，再建立隧道；保持隧道窗口运行。已有 Yakutan 隧道时可以直接复用，不用再开一条。
2. 打开 Real-Time Subtitle 设置，选择“本地”模型提供方，再把“运行位置”选为“远程推理服务器”。填写 `ws://127.0.0.1:18775`，点“测试连接”，就绪后保存。设置会在下次启动恢复。
3. Yakutan 填同一个地址。两边分别维护识别与翻译状态，服务共用模型；只有模型推理在远端，音频采集、轻量 CPU 停顿检测和字幕分句仍在客户端。客户端无需下载 Qwen / Hy-MT 权重；缺少 Silero 小模型时首次启动自动下载。
4. 关闭隧道窗口只断开这条本地连接。`stop_gpu4038.bat` 会关闭远端共享服务，因此会同时影响正在使用它的两个软件。

这次已直接连接现有 gpu4038 服务验证，无须升级服务器就能使用。复制的 `server.py` 对新字幕的后文参考做了兼容扩展；现有旧服务会忽略这个可选字段，仍能正常识别和翻译。以后更新服务时可使用这个版本，Yakutan 仍可连接。

这些启停脚本保留你现有部署的 SSH 别名和目录。换另一台远程机器时，需要修改脚本里的主机、目录及主机校验，并提供下面说明的完整 Yakutan 推理运行时；这个文件夹不是一个独立的模型安装包。

## Shared deployment details

This directory carries the WebSocket service protocol and operational entry
points used by Yakutan and Realtime Subtitle.  Both desktop apps should tunnel
to the same loopback endpoint, `ws://127.0.0.1:18775`; do not start a second
copy simply because the client app changed.

The deployed service remains at
`/share/home/tjfbb/data/yakutan_remote_inference` by default because its
runtime snapshot supplies `asr_sensevoice.py` and `qwen_microbatch.py`, which
are not present in this checkout.  `server.py` is a protocol-compatible copy
for a future self-contained deployment.  To deploy it elsewhere, provide a
compatible runtime root and models root explicitly; copying only this folder
is insufficient.

## Protocol version 1

- Health: connect by WebSocket and send `{"type":"health"}`.  A healthy
  reply is `health_ok` with `ready: true`.  Port 18775 is WebSocket-only, so
  an HTTP request returning 426 is expected and is not a failed health check.
- ASR: send `init` with `service: "asr"` and `engine` (`sensevoice` or
  `qwen3-asr`), then `transcribe` frames containing base64 little-endian
  float32 16 kHz mono audio.  Replies are `recognition`.
- Hy-MT2: send `init` with `service: "hymt2"`, source and target languages,
  then `update` frames.  The default service is `hymt2` to remain compatible
  with the existing Realtime Subtitle Hy-MT2 client, which does not send the
  `service` field.  Replies retain `translation`, `committed_text`,
  `hypothesis_mode: sentence_revision`, and `source_token_join_mode: verbatim`.
  Updated deployments also accept optional `following_source` as reference-only
  context, plus history entries in either legacy source-only form or
  `[source, translation]` pairs.  Older deployments simply ignore the optional
  following-source field, so the desktop client remains compatible.

## Deployment entry points

`run_gpu4038.sh`, `tunnel_to_gpu4038.bat`, and the stop scripts are deliberately
checked in so both desktop projects use the same lifecycle rules.  They only
listen on remote loopback and require SSH forwarding.  The launcher and remote
stopper refuse any host other than `gpu4038`; the stopper resolves the exact
listeners on ports 18775 and 18776 instead of killing broad process patterns.

Do not run these scripts from an automated test.  `tunnel_to_gpu4038.bat`
checks both listeners, starts the existing canonical deployment only when
needed, polls up to 60 times, then opens the local port-forward. SSH connection
time is additional. The
Windows tunnel process owns the forward until it is closed.

The Linux dependencies are in `requirements-linux.txt`.  They target the
remote CUDA 12 environment; they are not desktop app dependencies.

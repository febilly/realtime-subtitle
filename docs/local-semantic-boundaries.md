# 本地 ASR 连续语音语义断句：调研、实现与实验记录

## 结论

当前可运行的 opt-in 实现是：

1. Qwen3-ASR 每 1.0 秒做一次仅供边界判断的 partial；它不会按这个频率触发翻译。
2. 一旦出现“内部句末标点 + 至少 2 个右侧有效字符”，0.35 秒后快速复核。
3. 同一个安全边界连续两次保持稳定（LocalAgreement-2）后，才允许提交。
4. 先用独立 Qwen 探针验证 0.5 秒前缀不命中、完整窗口确实命中，再做最多 4 轮二分探测，定位已确认文本首次完整出现的音频位置；两端不构成有效 bracket 就拒绝裁剪。
5. 从切点前 0.8 秒保守重放，按已确认文本的 suffix 与新识别 prefix 去重。
6. 依次发出 `final(prefix)` 和 `non-final(suffix)`；Hy-MT2 因此把 suffix 当作新 utterance。
7. 任何定位失败、队列丢包、状态过期或模型异常都不裁音频，继续走原有 VAD 静音、4 秒 partial 和最终 endpoint。

物理 cut 还要求 prefix 具备可保守去重的证据：至少 2 个拉丁词，或至少 4 个中/日/韩文字。`Hello.`、`大家好。` 这类短句不会冒险裁剪，仍交给 VAD/延时；这是为了避免 0.8 秒 replay 把短句再次显示和翻译。

Nemotron timestamp sidecar 保留为可选 A/B 项，默认关闭。中文实测中它虽然 timestamp 精细，但其文字与 Qwen 有漏字/改写，严格对齐无法稳定授权裁音频；Qwen 前缀二分直接验证主 ASR 的目标 prefix，也不需要常驻第二个 600M 模型。当前证据只足以选择它作为工程默认项，不代表已经证明多语言 production 切点精度。

## 为什么不能“看到句号就切”

Qwen 会给每一个暂时的输入尾部补完整标点。对 4.759 秒中文 fixture 的真实模型探测：

| 输入前缀 | Qwen 输出 |
|---:|---|
| 2.5s | `不要问你的国家能为你做什么。` |
| 3.5s | `不要问你的国家能为你做什么，而要问你能为你。` |
| 4.759s | `不要问你的国家能为你做什么，而要问你能为你的国家做什么。` |

前两个句号随后都会被改写。因此实现从不接受 hypothesis 的最后一个句末符；句末符必须已经变成内部标点、具有右上下文，并跨两次 hypothesis 稳定。这与 [Whisper-Streaming 的 LocalAgreement](https://aclanthology.org/2023.ijcnlp-demo.3.pdf) 及 [FunASR streaming punctuation](https://github.com/modelscope/FunASR/blob/main/funasr/models/ct_transformer_streaming/model.py) 对不稳定尾部的处理方向一致。

Qwen 官方 streaming ASR 不直接返回 timestamp；官方的时间定位方案是另一个 0.6B forced aligner。[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)，[forced aligner](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_forced_aligner.py)

## 已比较的候选

| 方案 | 优点 | 实测/限制 | 选择 |
|---|---|---|---|
| Qwen 内部标点 + LocalAgreement-2 + Qwen 前缀二分 | 无新增模型；文字来自实际主 ASR；可直接验证目标 prefix | 当前中文样本每个边界约 0.6s 定位计算；仍需自然语料验收 | **opt-in 首选** |
| Nemotron 3.5 streaming timestamp sidecar | token/timestamp 1:1；2 CPU threads 实测 RTF 约 0.161 | 600M；中文 stress 中漏首字且改写句尾，严格对齐失败 | 可选 A/B |
| sherpa 中英 CT-Transformer punctuation INT8 | 约 72MB；官方短文本 CPU 示例为毫秒级 | 仅中英；仍不能把标点映射回音频；末尾也会强补标点 | 未集成 |
| SaT / wtpsplit | 85 种语言，适合无标点 ASR 文本 | 依赖 future context；不能提供音频切点 | 后续文本候选器 |
| Smart Turn v3 | 很小，多语言 | 官方工作流是在 VAD 已检测到静音后判断 turn end，不解决无停顿内部句界 | 排除 |
| VAP / 韵律 turn-taking | 直接看声学与轮次 | 目标是 turn shift，不是同一说话人的句内语义边界 | 排除 |
| Qwen forced aligner / WhisperX | 可以做已知文本对齐 | 新增大模型/显存；对齐不能证明句号本身正确 | 暂不采用 |
| Google E2E Segmenter 类 RNNT EOS head | 研究结果最贴近最终方向 | 没有可直接接入当前中日英多语路径的公开 checkpoint | 长期方向 |

参考资料：[Nemotron 模型卡](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)、[sherpa result timestamp API](https://k2-fsa.github.io/sherpa/onnx/c-api/html/structSherpaOnnxOnlineRecognizerResult.html)、[sherpa punctuation models](https://k2-fsa.github.io/sherpa/onnx/punctuation/pretrained_models.html)、[SaT](https://github.com/segment-any-text/wtpsplit)、[Smart Turn](https://github.com/pipecat-ai/smart-turn)、[Google E2E Segmenter](https://arxiv.org/abs/2204.10749)。

## 运行时结构

```text
16k PCM -> Silero VAD / live PCM buffer
                    |
                    +-> normal partial cadence -> existing subtitle/translation path
                    |
                    +-> 1.0s boundary-only Qwen scan
                          -> safe internal punctuation
                          -> 0.35s fast confirmation
                          -> Qwen prefix binary locator
                          -> keep 0.8s pre-roll
                          -> trim confirmed PCM prefix
                          -> final(prefix)
                          -> replay/deduplicate suffix
                          -> non-final(suffix)
```

实现同时修了会破坏物理裁剪正确性的旧问题：

- `_waiting_final` 从单槽改为 FIFO，不再覆盖连续 final。
- 每个 transcription request 自带 `stream_id` 和 `voiced_seq`，不再从可变全局字段读取错误版本。
- 30 秒上限不再把真实 chunk 替换为零等待伪静音；现在先封存旧窗，再处理当前真实 chunk。
- worker 只在处理锁内取队列数据，stop/pause 不会把后到 chunk 排到已取出 chunk 前面。
- stop 会等串行 partial/final 链真正排空后再关闭 executor，尾部 final 不会丢失。
- overlap 去重在英文中同时验证左右完整词边界，而且绝不删除整个 hypothesis。

音频队列一旦溢出，会给当前 VAD 段标记 discontinuity，并禁用语义物理裁剪直到下一段，避免在有缺口的时间轴上做危险切点。

## 本机实验

### 中文无停顿双句

将 `zh.wav` 去首尾静音后复制两遍，以 20ms crossfade 无缝拼接：

- 总长：9.070s
- 已知 seam：4.525s
- 正常扫描：1.0s；候选快速复核：0.35s
- 稳定边界被确认时的音频窗口：约 5.54s
- 文本边界相对 seam 的音频检测延迟：约 1.02s
- 加入两端独立 guard probe 后，两次当前代码复跑的 Qwen 定位为 4.277--4.540s，相对 seam 为 `-248ms` 到 `+15ms`
- 定位额外 wall time：约 0.6s；解码有采样，单次数字会漂移
- 保留 0.8s overlap 后，实际裁掉：3.477--3.740s
- 最终第二句窗口：5.33--5.61s，而不裁剪时会是 9.07s；历史窗口缩短约 38.3%--41.2%
- 输出顺序：第一句 final -> 第二句 partial -> 第二句 final
- 重复/漏字：0
- recognizer error：0

早期 0.5s 固定扫描对照会更快获得候选，但扫描次数明显更多。因此默认采用“1.0s 常规 + 0.35s 候选复核”；切点数字以带完整窗口 guard probe 的上述当前代码复跑为准。

### Nemotron sidecar 对照

同一压力样本：

- 2 threads CPU RTF 约 0.161；4 threads 约 0.138。
- token 与 timestamp 1:1，seam 附近 timestamp 可到约 `+60ms`。
- 但 streaming 文本出现漏首字、句尾漏词/改写，无法通过保守的主 ASR prefix 精确对齐。
- 因此它没有成为默认依赖。启用 sidecar 后若精确对齐失败，仍自动回到 Qwen 前缀二分。

### 多语言压力探测

对德语、西语、法语、日语 fixture 做相同的无缝双句前缀探测。德/西/法的 Qwen 内部标点能在右上下文到来后出现；日语 fixture 的文本质量本身较差、revision 较大，而且样本自身可能包含更早的内部句界。它们只证明候选器能工作，不能证明多语言物理切点安全或 production precision。

西语真实 recognizer 整链还覆盖了“相邻 utterance 文本完全重复”的反例：早期 overlap 去重会误删整条合法重复句；现在若匹配占满整个 hypothesis，会保留它并等待更多右上下文。

### 真实 Hy-MT2 状态链

把本次中文真机 ASR 实际产生的 `prefix final -> suffix partial -> suffix final` 三个事件按原顺序送入本地 Hy-MT2，而不是 mock：

- prefix final：156ms，`Don’t ask what your country can do for you, but ask what you can do for your country.`
- suffix partial：63ms，`Don't ask your country what it can do for you.`
- suffix final：93ms，`Don't ask your country what it can do for you, but ask what you can do for your country.`

这验证了 semantic final 会结束旧 utterance，随后 partial 会开启新的 revision chain，最终结果能正常收口。它仍不是长时间同时运行 ASR+MT 的资源/质量 A/B。

### 自动测试

```powershell
python -m pytest tests -q -p no:cacheprovider --basetemp scratch\.pytest-all-semantic
# 549 passed

npm test
# 80 files / 666 passed

.\.venv\Scripts\python.exe tools\evaluate_local_semantic_boundary.py `
  models\sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11\test_wavs\zh.wav `
  --language zh
```

评估脚本使用“已知为单句”的 WAV 构造无停顿重复句，并按 realtime pace 送入。它自动检查：恰好一次语义 commit、恰好两个 final、两者之间出现 suffix partial、两次 final 的归一化文本一致、recognizer/locator 无错误、切点距已知 seam 不超过 350ms、物理 trim 不越过 seam 150ms、至少裁掉 30% 历史音频。任一条件不满足会返回非零退出码；多句 WAV 不适合这个 seam gate，`fed_audio_seconds` 只是生产者进度而非严格 decision latency。

## 配置与回滚

production 默认关闭。要运行本次已验证的实验路径，在 `.env` 主动开启：

```dotenv
LOCAL_SEMANTIC_BOUNDARY_ENABLED=True
LOCAL_SEMANTIC_BOUNDARY_SCAN_INTERVAL=1.0
LOCAL_SEMANTIC_BOUNDARY_CONFIRM_INTERVAL=0.35
LOCAL_SEMANTIC_BOUNDARY_REPLAY_MS=800
```

关闭并回到旧行为：

```dotenv
LOCAL_SEMANTIC_BOUNDARY_ENABLED=False
```

可选 sidecar（默认关闭）：

```powershell
uv pip install -r requirements-semantic-boundary-scout.txt
```

```dotenv
LOCAL_SEMANTIC_BOUNDARY_SCOUT_ENABLED=True
LOCAL_SEMANTIC_BOUNDARY_MODEL_DIR=models/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11
LOCAL_SEMANTIC_BOUNDARY_SCOUT_THREADS=2
```

## 尚未证明的部分

合成 fixture 只证明工程链路能跑、切点可定位、重放不丢字；它不能证明真实自然连续讲话上的 boundary precision/recall，因此本功能保持默认关闭。上线判定仍应使用 5--10 分钟中英日/混说自然语料人工标注，并至少记录：

- boundary precision、recall、F0.5；误切优先级高于漏切；
- 检测延迟 p50/p90；
- 切点误差与任何超过 150ms 的语音缺失；
- reset 前后 CER/WER；
- 翻译重复、漏词、revision；
- `onnx_encode`、`prefill_positions`、`llm_generate`、wall compute/min；
- CPU、RSS、GPU 与队列积压。

Qwen DirectML encoder 当前固定 pad 到 30 秒，因此缩短输入会明显降低音频窗口和 LLM prefill，但 ONNX encoder 时间不一定同比下降。本次没有把“裁掉 38%--41% 历史音频”误报成“总算力下降同样比例”；真实长讲话的 wall compute/min 仍需单独验收。
另外，开启后会把边界扫描从原有约 4 秒 cadence 提高到 1 秒，且每个已确认边界还有两端 guard 与最多四次二分探针；在完成 semantic-off baseline 前，不能断言总 compute 已下降。

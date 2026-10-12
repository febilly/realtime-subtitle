# 断句（字幕换行）机制

字幕的“一行”= 一个句子。换行由后端在 final token 流里插入 **separator** 决定，
前端只对尚未 final 的尾巴做**预测性**换行。本文说明规则在哪、为什么这样、怎么验证。

## 代码地图

| 位置 | 职责 |
|---|---|
| `sentence_segmentation.py` / `static/js/segmentation.js` | 纯规则：什么算句末（句号、问号、省略号不算、缩写/小数/引号例外）。两边规则共用 `tests/fixtures/segmentation-cases.json`，必须同步修改 |
| `realtime_session_base.py` | 两个 provider 共用的会话代码；`_expects_translation_stream()` 是“会不会有翻译流”的唯一判断 |
| `soniox_session.py` | Soniox 断句：`_process_interleaved_punctuation_tokens`（标点模式）与 `_process_non_interleaved_tokens`（翻译/停顿模式） |
| `gemini_session.py` | Gemini 断句（输出侧标点 + 源侧追上后再换行） |
| `sentence_pairing.py` | 语义配对：哪段翻译属于哪个源句（给 LLM 改进用），与显示换行**独立** |
| `interrupt_repair.py` | 短打断修复：A 说一半被 B 的“嗯/うん”打断后，把 A 的两段拼回一句 |
| `static/js/render-model.js` | 前端：对 non-final 尾巴做预测性换行（`separator_type: "speculative"`） |

## 两条独立的线

每个 final token 同时进入两个累加器：

1. **显示缓冲**（`_sentence_buffers`）：只决定换行落在输出流的哪个位置。
2. **语义配对**（`SentencePairer`）：决定 (源句, 译文) 配对与 `llm_sentence_id`。

它们故意分开：翻译常常比源文晚到，显示换行要等译文开始出现，但语义配对不能因此串位。

## 什么时候换行（标点模式，Soniox）

先问：这句话之后**会不会有翻译流**（`_expects_translation_stream`）。

| 情况 | 会有翻译流？ | 源文句号处 |
|---|---|---|
| 翻译关闭（`translation = "none"`） | 否 | 立即换行 |
| 准确模式（Soniox 翻译被抑制，LLM 翻译） | 否 | 立即换行 |
| 说的就是目标语言（单向） | 否 | 立即换行 |
| 跨语言，单向 / 双向翻译 | 是 | 推迟：等这句译文开始出现；最迟在下一句源文开始前换行 |

其他换行来源：

| separator_type | 触发 |
|---|---|
| `punctuation` | 句末标点（源文或译文） |
| `endpoint` | Soniox `<end>` / `endpoint_detected`，或推迟的换行在下一句源文前落地 |
| `speaker_change` | 换了说话人，关闭其他人未结束的句子 |
| `interrupt_repair` | 短打断修复：撤回 A1（`subtitle_retract` 帧），重发 A1 让 A2 接上 |
| `translation` | 翻译模式（非标点模式）：本批出现译文后整批末尾换一次 |
| `speculative` | 仅前端：non-final 尾巴的预测换行 |

需要多看一个 token 才能确定的边界（右引号、`a.` + `m.`、`5.` + `3%`）由
`PendingBoundaryState` 暂存，下一个 token 到达时再决定。

**预测与 final 必须一致**：前端预测在某处换行，而后端 final 后不换，用户就会看到“断开又拼回去”。
`tests/frontend/segmentation-replay.test.js` 对此做硬性检查。

## 已知问题

- 翻译模式下，短句的译文晚于下一句源文到达时，两句会留在同一行
  （“確かに。”+ 下一句）。最近 45 段录制中约 18 处。
- 翻译关闭/准确模式下，句号所在 token 位于批末、右引号在下一批才到时，引号会被挤到下一行。

## 回放验证

回放把真实录制（`SONIOX_RESPONSE_LOG=1` 记录到 `logs/soniox-responses/`）按记录时钟喂给当前后端。
**原始录制含真实语音转写，永远不要提交**；入库的 fixture 由 `make-fixture` 匿名化
（字母/数字逐次随机替换，保留标点、空白、token 边界、时间）。

```bash
# 体检：重放录制、检查后端不变量，并输出帧供前端检查
python tools/segmentation_replay.py audit logs/soniox-responses/<文件> --frames-dir scratch/frames
node scripts/check-segmentation-frames.js scratch/frames/*.jsonl --verbose

# 同一批录制强制走翻译 / 停顿模式
python tools/segmentation_replay.py audit <文件...> --segment-mode translation

# 从录制截取匿名 fixture（tests/fixtures/segmentation-replay/）
python tools/segmentation_replay.py make-fixture <录制> --run 0 --start 0 --duration 90 --name <名字>

# 行为**有意**改变后重新生成期望输出，并审阅 diff
python tools/segmentation_replay.py update-golden
```

检查的不变量：

- 每个 final 字符恰好送达前端一次（打断修复的重发除外）；
- 已完结（全 final、已闭合）的行之后不再变化；
- 预测换行处，final 后也必须换行。

纯重构时，可以把重构前后的 `audit --frames-dir` 输出逐字节比较，作为行为未变的证据。

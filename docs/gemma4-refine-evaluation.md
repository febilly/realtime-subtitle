# OpenRouter Gemma 4 31B 字幕修正评测

2026-10-01 实测 `google/gemma-4-31b-it`。结论：**Gemma 的保守配方接近当前 DeepSeek，
比调优千问更克制，值得保存；但尚未在纠错和实时延迟上整体胜出，继续保留 DeepSeek 默认。**

沿用固定历史数据英译中、日译中各 400 条，202 条整会话开发集选择提示词，
其余 598 条在候选冻结后才生成 Gemma 输出。原数据与早期其他模型评分已经看过，
不能称完全未见过的测试集。未把历史 benchmark 原句放进提示词示例。
所有 4000 组输出及 800 条同轮匿名评分完整，无遗漏分母。

## 质量对照

Luna 为独立自动裁判，原始草稿不是标准答案。本轮 97 条草稿语义 ≤2 分，
最终 ≥3 分算修好实质错误。不同轮次会有评分波动，只在本表内部比较。

|方案|语义 0–4|修好实质错误|新增实质错误|纯风格改写|修改句数|
|---|---:|---:|---:|---:|---:|
|最初 DeepSeek + 原提示词|3.748|57/97|9|152|267/800|
|当前 DeepSeek + 调优提示词|3.788|62/97|6|78|189/800|
|调优千问 + 短中文提示词|3.703|52/97|22|218|370/800|
|Gemma + 短中文提示词|3.774|65/97|16|62|183/800|
|Gemma + 中文歧义保护（保存配方）|3.774|56/97|11|36|136/800|

Gemma 两套配方的均分相同：短版纠错更积极，也更易改坏；保守版减少改动，适合字幕稳定性目标。
选择保守配方的决定先于剩余 598 条候选生成。留出会话中，保守 Gemma 修好 38 个实质错误、
新增 8 个；当前 DeepSeek 修好 44 个、新增 4 个。

保守 Gemma 减当前 DeepSeek 的全量配对语义差 -0.01375，会话 bootstrap 95% 区间
[-0.04545, +0.01910]，包含零；剔除不确定与评分/文字不一致后，648 条差 -0.01080。
语义均分接近，不能宣称统计显著优劣。它相对最初 DeepSeek 的差 +0.02625，区间也包含零。

英译中：当前 DeepSeek 修好 28/45、新增 1；保守 Gemma 修好 25/45、新增 6。
日译中：当前 DeepSeek 修好 34/52、新增 5；保守 Gemma 修好 31/52、新增 5。
Gemma 两个方向都较少纯风格改写，但仍会错误消解含混词义、角色、ASR 片段。

## 速度和供应商

使用 `.env` 原有 Gemma 配置，非思考 `reasoning.effort: none`，temperature 0.2、
max_tokens 1024、三句原文上下文。实际模型 ID 和供应商均保存到每条记录。
全量保守配方 794 条走 CoreWeave、6 条走 DeepInfra；短版分别 797/3。
全量限制 `provider.only` 为这两家，保持 CoreWeave 优先，避免路由到其他供应商。

最初全量批次有 57 个暂时连接失败，降低并发后补齐；质量分母未减少。
首次速度批次有 19 个连接失败，其补跑汇总不作为主要速度证据。之后单独重跑同一批
60 条样本、并发 4，300 个新请求全部成功：

|方案|p50 毫秒|p95 毫秒|
|---|---:|---:|
|最初 DeepSeek|502|753|
|当前 DeepSeek|476|709|
|调优千问|359|562|
|保守 Gemma，CoreWeave|503|2593|
|同提示词 Gemma，固定 DeepInfra|525|1707|

Gemma 中位速度已接近 DeepSeek，但长尾明显更高。这里是成功 HTTP 请求耗时，
不含 ASR、字幕排队和 UI；不是线上端到端保证。
DeepInfra 只做了这批 60 条比较，不能把 CoreWeave 为主的完整质量结果直接当成
DeepInfra 的全量质量证明。两家同提示词有 3 条输出分歧，实际供应商需要记录。
模型与供应商信息依据 [OpenRouter Gemma 页面](https://openrouter.ai/google/gemma-4-31b-it) 和
[endpoint 元数据](https://openrouter.ai/api/v1/models/google/gemma-4-31b-it/endpoints)，快照保存在私人实验目录。

## 费用

完整 800 条成功响应的 OpenRouter `usage.cost` 合计：

- 保守 Gemma：US$0.0229042，折合 **US$0.02863/千句**。
- 短版 Gemma：US$0.01715949，折合 US$0.02145/千句。

这是 API 返回的成功调用费用，未保存 usage 的失败/重试费用、充值手续费和裁判费用不在其中，
不是账单总支出证明。未使用页面最低标价冒充实际路由费用。
按每美元 7 元作为比较假设，保守 Gemma 约 ¥0.20/千句，短版约 ¥0.15/千句。
此前同样文本的当前 DeepSeek 按官方非高峰/高峰单价及缓存 token 估算约
US$0.0345–0.0689/千句；千问约 ¥0.0428/千句。7 为换算假设，不是实时汇率。
费用仅覆盖修正模型，不含语音识别或初次翻译。

## 提示词选择与局限

两轮开发共测试六套 Gemma 提示词：旧版、DeepSeek 示例、短中文、英文歧义保护、
中文歧义保护、压缩英文。压缩英文虽然自然开发集均分不错，却在合成题中错误修复
主客体和必要条件，被淘汰。中文歧义保护 40 条已知合成开发题全部保持/修好，
其中正确草稿 20 条原样保留，错误草稿 20 条按意思阅读核对，不要求逐字匹配参考译文。
这些题已反复使用，不能称新的独立留出题，也不能取代真实字幕结果。

个案核对发现，Gemma 有正确修复而 DeepSeek 漏修的句子，也仍会擅自解释含混词义。
此外有一个含混片段返回了推测说明和译文，当前普通文本解析器未拦住这种格式。
保存为实验配方不代表保证每次返回都可直接上屏。裁判也有不确定片段过度自信、
未修改输出被标 style_only 等分类问题，抽查记录单独保存，不改写原自动分数。

## 产物与复跑

保存的 [提示词](../tools/refine_benchmark_prompts.gemma.json) 和
[对照配置](../tools/refine_benchmark_models.gemma.json) 不含密钥。
生产 `.env` 和生产提示词未修改。提示词不会因单独切换模型自动进入产品。

固定私人数据和完整原始响应均在 Git 忽略的 `scratch/gemma4-tuning-20261001/`：

- `final800/report.md`、`summary.json`、`review.jsonl`：全部方案同轮对照。
- `final800/gemma-reported-cost.json`：成功调用费用和实际供应商。
- `final800/judge-spot-check.md`：逐条核对及裁判局限。
- `compare-current800/report.md`：当前 DeepSeek 与保守 Gemma 的配对区间，同轮记录派生，无新增 API 调用。
- `heldout598/report.md`：留出会话子集，同轮记录派生，无新增 API 调用。
- `latency60-clean/summary.json`：单独重测、全成功的速度批次。
- `decision-before800.json`、`final-decision.json`：冻结候选及最终决定。
- `endpoint-snapshot.json`、`dev-round1`、`dev-round2`、`semantic*`：元数据及全部开发试验。

运行方式见 [refine-benchmark.md](refine-benchmark.md)。新建实验时可加
`--models-config tools/refine_benchmark_models.gemma.json --prompts-config tools/refine_benchmark_prompts.gemma.json`。
复跑本次结论应保留冻结的 800 条及提示词快照，不重新抽样。
104 项相关本地回归测试通过，未提交、推送、部署或删除 stash。

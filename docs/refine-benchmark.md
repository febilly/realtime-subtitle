# 可复用的字幕修正评测

本评测最初比较 **旧提示词 + DeepSeek** 和 **新提示词 + Qwen** 的完整方案，也支持
在 `models.json` 中增加任意模型与提示词组合，复用生产
`llm_refine.parse_refine_response`，以实际最终字幕衡量效果。直接翻译不是本次评测范围。

默认 800 条（英译中、日译中各 400 条），来自 `logs/llm_*.jsonl` 的原文与原始草稿。
按方向去重原文、按句长比例分层，轮流从不同日志会话抽样；固定随机种子。
**不会用过去的修正结果、模型是否修改、过去判断的错误类型来选样或充当正确答案。**
保留自然口语和不完整句，不合成错误。方向根据假名占比及本地 `langid` 保守推断：
拉丁文本需通过不限制候选语言的检测才能归入英语，避免西班牙语等混入。
英语引用日语词语的句子按主体语言处理；全汉字或其他语言排除。短句识别仍可能有误，
并非经过人工核实的语言标签。

源文质量、翻译错位等原始日志问题不能完全自动排除。历史日志没有完整请求上下文，
所以只取同文件里最近已完成调用的原文，最多三句，并在文件、语言切换或 120 秒空档处
重置。它是统一的模拟上下文，不是原请求逐字重放。两组使用完全相同的用户消息。

## 建集与复跑

在仓库根目录运行，先用 uv 安装可选的本地语言检测依赖：

```powershell
uv --cache-dir scratch\uv-cache pip install --python .venv\Scripts\python.exe -r tools\refine_benchmark_requirements.txt
.venv\Scripts\python.exe tools\refine_benchmark.py build
.venv\Scripts\python.exe tools\refine_benchmark.py run --limit 8 --concurrency 2
.venv\Scripts\python.exe tools\refine_benchmark.py run --concurrency 6
.venv\Scripts\python.exe tools\refine_benchmark.py judge --concurrency 4
.venv\Scripts\python.exe tools\refine_benchmark.py report
```

`run` 和 `judge` 会发送日志文本到外部 API 并产生费用。独立裁判会额外收到草稿和两组
输出，必须确保这类外发在当前操作中已获授权；不能仅凭本文档推定授权。

默认目录 `scratch/refine-benchmark-v2/` 已被 Git 忽略。固定数据集、结果和复核明细
包含私人历史文本，不应提交。可用全局参数选择新目录：

```powershell
.venv\Scripts\python.exe tools\refine_benchmark.py --output scratch\refine-benchmark-v3 build --size 1000
```

建集从 HEAD 提取旧 system prompt、从工作区提取新 prompt 并冻结到 `prompts.json`。
用户消息通过生产构造器生成，固定三句上下文与空 suffix。
数据和提示词 SHA256 保存在 `manifest.json`；已有数据集不会覆盖。
`models.json` 仅保存模型名及配置，密钥运行时从 `.env` 指定模型的配置块读取，支持
注释掉的配置块。Qwen `enable_thinking` 字符串转为 API 所需的布尔值。
不修改 `.env` 或应用代码。

默认配置保留最初的 DeepSeek/Qwen 对照。要使用本次推荐的 DeepSeek 提示词对照，建集时加
`--models-config tools\refine_benchmark_models.recommended.json`；也可提供自己的配置文件。
模型配置通过 `env_model` 找密钥，禁止在 JSON 中写明文 `api_key`。
今后公平比较应沿用本次冻结的 800 条数据；`build` 只用于确实需要新数据集的情况。

每个模型条目可覆盖全局 `temperature`、`max_tokens`，例如同一模型分别用 0、0.1、
0.2 测试。参数进入运行指纹及每条结果，配置变动需另建实验目录。
`extra_json` 可覆盖该模型 `.env` 块里的请求参数，用于比较思考开关等；覆盖也是指纹
的一部分，实验不会直接改写 `.env`。
多方案报告默认比较前两组；可用顶层 `comparison_models` 指定要比较的两组 `name`。
所有方案仍逐一汇总，不会只保留获胜方案。

千问短中文提示词已单独保存到 `tools/refine_benchmark_prompts.qwen.json`，可用
`build --models-config tools/refine_benchmark_models.qwen.json --prompts-config tools/refine_benchmark_prompts.qwen.json`
建立新的对照实验。这个命令会重新采样；复跑本次结论应使用冻结的
`scratch/qwen-tuning-20260930/final800/`，不要覆盖其数据和提示词。
配置引用不存在的提示词会在落盘样本前报错；该 profile 不修改应用默认模型。

千问调优实验还支持每个条目的 `context_count: 0`（去掉历史原文），以及
`verifier_prompt`（仅在提出修改时用同一模型复核）。复核以 temperature 0 调用，
`verifier_max_tokens` 默认 128；只接受严格的 `{"accept":true,"reason":"文字理由"}`，
否决或非法返回均保留草稿。`verifier_response_format: null` 可关闭复核的 API JSON 格式参数。
原始候选、复核结果分别保存，token 和延迟计入两次调用。这些是离线实验选项，
不会自动给产品增加第二次请求。

`tools/refine_response_protocols.py` 仅供 benchmark 测试 JSON 局部补丁、错误证据及
先比较再判断等协议；会拒绝无效锚点、重叠补丁和不完整 JSON。生产仍采用普通文本协议。
实验解析器源码也进入运行指纹。结构验证并不能证明模型给出的修正符合原意。

`run`/`judge` 逐条落盘，成功记录在下次运行时跳过，失败可重试。配置或提示词改变
会拒绝把不同实验混进同一目录；生产模块源码哈希也进入指纹，解析行为变了需重新处理
所有方案的原始响应，避免把不同版本上屏结果混在一起。改变模型时复制固定 dataset、prompts、manifest 到
新目录，再编辑新目录的 models.json；不要复制旧 run_manifest/results/judgements。
保留 `baseline` 等内部方案名，模型变体用独特 `name`。
模型别名可能随服务升级变化，记录 `response_model`，但不能保证服务永久版本固定。

## 评测口径

- **修改量**：修改率、去空白后的 Levenshtein 字符距离比例、修改句子的平均改动比例。
  空白变化单独标记，不把“少改”误当成“正确”。
- **语义与通顺度**：独立裁判 0–4 分，分别比较草稿与最终译文；统计修好与改坏。
  分数升降与裁判文字判断分开统计；若文字说“没变”但分数变化，则标记为内部不一致。
- **漏修**：草稿有实质错误（语义 ≤2 分）时，是否修到 ≥3 分；以及遗留错误总数。
- **无必要修改**：纯风格改写、修错同时润色、必要修正、损害译文、判断不确定分开记录。
- **配对比较**：同条样本两方案语义分差，按日志会话进行 2000 次 bootstrap，给出 95% 区间。
  会话相关性得以保留；分数区间仍无法涵盖自动裁判的系统偏差。
- **运行指标**：成功/失败/未完成数、p50/p95、token usage。不猜测未核实的 token 单价。
  单次调用延迟是评测并发下最终成功 HTTP 请求耗时，未计入重试等待；
  可选复核方案额外计入复核阶段的耗时和重试等待。均不含字幕排队与 ASR 时间。

裁判只收到随机顺序的匿名译文、原文、上下文和草稿，相同译文合并后共用评分；
模型名、提示词、供应商、时延和过去模型输出不送给裁判。草稿不是标准答案。
裁判要求容忍片段、不补全、不因同义替换加分。输出字段、评分范围和候选覆盖必须
通过验证，失败不能悄悄充当成功或减少分母。

质量分数来自单个独立 LLM，而非人工金标准。建议先人工复核不确定、改坏和方案分歧
样本，将人工标签另存后再作为长期验收依据。自动分数适合筛选与回归，不能单独证明
小分差就代表真实质量差异。当前两组同时换模型与提示词，不能把差异单独归因于其中
一个因素；归因实验可在同一数据上增加另外两种模型/提示词组合。

`report.md` 为中文报告；`summary.json`、`summary.csv` 为质量结果，
`mechanical.csv` 为运行结果。`review.jsonl` 保存源文、草稿、输出、裁判理由供人工复核。
报告同时给出剔除裁判“不确定”和内部不一致样本后的配对结果，用来检查结论是否稳健；
原始结果仍保留，不自动改写裁判评分。裁判费用使用 OpenRouter 响应的 `usage.cost`，
只合计已保存的成功响应，可能遗漏失败或评分格式重试的费用。
原始响应在 `results.jsonl`/`judgements.jsonl`，模型/API 请求参数在两个运行 manifest。
HTTP 错误仅存错误码，避免代理回显密钥。
推理结果还保存上游返回的 `response_provider` 和 `finish_reason`（未提供时为空），
以区分 OpenRouter 实际路由与配置中的优先顺序；`usage.cost` 原样保留。
供应商的延迟、定价和量化方式可能不同，不能用页面最低报价替代实际路由费用。

## 不使用第三方自动裁判

导出匿名任务和同一评分细则，在本地人工打分后验证导入：

```powershell
.venv\Scripts\python.exe tools\refine_benchmark.py export-review
.venv\Scripts\python.exe tools\refine_benchmark.py import-review --input scratch\my-manual-ratings.json
.venv\Scripts\python.exe tools\refine_benchmark.py report --judgements manual_judgements.jsonl
```

导出不会发送网络请求。每条人工结果需保留 `sample_id` 和 `input_hash`，并提供
评分细则要求的 ratings/best_ids/uncertainty 字段。导入会验证候选完整性、字段范围及
输入哈希，防止拿不同版本的译文分数混入当前实验；允许分批导入。
人工标签与自动裁判结果分开存储，报告只能明确选择一种来源，避免不知情混用。

## 调优与验收

先把固定数据按整个日志会话划分开发集和留出集。只在开发集选择模型、提示词和参数，
记录冻结决定之后才生成留出集候选结果。相同句子的变体必须放在同一轮匿名评分，
不要直接比较不同轮次的均分；自动裁判本身存在评分波动。
旧结果可复用，但必须确认输入、参数、提示词快照一致，并记录来源。

本次开发集 202 条（英 100、日 102），留出集 598 条（英 300、日 298），会话不交叉。
之前初始对照的全量分数已看过，所以留出集是候选输出未参与调优，并非完全从未见过的
数据。调优脚本、分组 ID、冻结决定和各轮结果在 `scratch/refine-tuning/`。
最后用同一轮裁判比较旧 DeepSeek、原 Qwen、新选定 DeepSeek，完整 800 条和留出集
单独出报告。结论与当前推荐配置见 [refine-model-selection.md](refine-model-selection.md)。

`tests/fixtures/refine-semantic-acceptance.jsonl` 是另外 20 条合成验收题：10 条正确草稿
必须保留，10 条明确的否定、数量、人物关系、实体错误必须修正。`reference` 只用于
检查意思，不要求错误句修正版逐字匹配。它们不混入历史 benchmark 分母，也不能作为
真实口语质量的替代指标。
另有 `refine-semantic-holdout.jsonl` 的 20 条新题，冻结方案后才测试，不作为提示词示例。

当前解析器还会拦截明确的说明格式：单独成行的 NO_CHANGE 标记、输入中没有的
Source/Draft/Issues/Corrected 等字段、中文“应改为：”、原句加箭头的对照。
这种响应直接保留草稿，不重试、不把解释上屏。输入本来就含字段名或箭头时保留其
字面内容。它不是通用的语义校验，也不能保证拦截所有任意形态的解释。

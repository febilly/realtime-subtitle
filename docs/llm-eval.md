# LLM Translation Evaluation Workflow

这套工具用于比较不同 LLM 接入本程序后的翻译效果，覆盖：

- 时延：每次请求的 latency、平均值、p50、p95。
- 稳定性：成功率、错误数、重复请求表现。
- 费用：按模型配置中的 token 单价估算。
- 准确性：用裁判 LLM 自动评判，或导出提示词和数据给网页版 AI 手动评判。

工具会复用 `llm_refine.py` 里生产路径的真实 prompt 模板。

## 1. 准备模型配置

复制示例：

```powershell
New-Item -ItemType Directory -Force scratch\llm_eval | Out-Null
Copy-Item tools\llm_eval_models.example.json scratch\llm_eval\models.json
```

编辑 `scratch\llm_eval\models.json`：

```json
{
  "models": [
    {
      "name": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-chat",
      "temperature": 0.2,
      "max_tokens": 1024,
      "timeout_seconds": 60,
      "currency": "CNY",
      "extra_headers": {},
      "extra_json": {},
      "pricing": {
        "input_per_1m": 0.27,
        "cached_input_per_1m": 0.07,
        "output_per_1m": 1.1
      }
    }
  ]
}
```

`base_url` 是 OpenAI-compatible API 根路径；工具会自动补 `/chat/completions`。
`pricing` 的单位按 `currency` 解释，默认是 `CNY`，字段含义是每 100 万
token 的价格。

## 2. 从本地日志构建数据集

默认读取 `logs/transcript_*.txt`，输出到 `scratch/llm_eval/dataset.jsonl`：

```powershell
python tools\llm_eval.py build-dataset --target-lang zh --source-lang ja --limit 200 --sample random
```

每条样本包含：

- `source`：日志里的原文。
- `draft_translation`：日志里的实时翻译，作为 baseline。
- `context`：前几条 source/translation，用于模拟真实上下文 prompt。
- `source_lang` / `target_lang` / `log_path` / `log_line`：溯源信息。

## 3. 跑候选 LLM

直接翻译模式，对比“LLM 直接翻译”：

```powershell
python tools\llm_eval.py run --dataset scratch\llm_eval\dataset.jsonl --models scratch\llm_eval\models.json --mode translate --repeat 3 --concurrency 2
```

润色模式，对比“LLM refine 现有翻译”：

```powershell
python tools\llm_eval.py run --dataset scratch\llm_eval\dataset.jsonl --models scratch\llm_eval\models.json --mode refine --repeat 3 --concurrency 2
```

运行时会显示进度条；如果需要静默输出，可以加 `--no-progress`。

输出目录默认是 `scratch/llm_eval/run-YYYYMMDD_HHMMSS/`，包含：

- `results.jsonl`：每次请求的原始输出、解析后翻译、usage、latency、错误。
- `summary.md` / `summary.csv` / `summary.json`：模型级摘要。

## 4A. 用裁判 LLM 自动评判

裁判配置也使用同样的模型配置格式，可以是单模型文件，也可以是 `models` 数组，工具会取第一个：

```powershell
python tools\llm_eval.py judge `
  --dataset scratch\llm_eval\dataset.jsonl `
  --results scratch\llm_eval\run-YYYYMMDD_HHMMSS\results.jsonl `
  --judge-model scratch\llm_eval\judge_model.json
```

输出：

- `judge_results.jsonl`：每条样本的裁判原始 JSON。
- `judge_summary.csv` / `judge_summary.json`：候选项平均准确性、流畅度、完整性、overall、best 次数。

默认会把日志里的 `draft_translation` 作为 `baseline_draft` 一起送评。

## 4B. 导出给网页版 AI 手动评判

```powershell
python tools\llm_eval.py export-judge `
  --dataset scratch\llm_eval\dataset.jsonl `
  --results scratch\llm_eval\run-YYYYMMDD_HHMMSS\results.jsonl
```

输出在 `manual_judge/`：

- `judge_prompt.md`：直接复制到网页版 AI 的总说明。
- `judge_manual_data.json`：上传给网页版 AI 的格式化数据文件。
- `judge_manual_tasks.jsonl`：同样任务的 JSONL 版本，适合工具处理。

推荐做法：把 `judge_prompt.md` 的内容粘贴到网页版 AI，然后上传
`judge_manual_data.json`。如果网页 AI 一次吃不下全部任务，用 `--limit`
先导出较小批次，或让它按 `task_id` 顺序分批继续。提示词会要求网页
AI 把最终 JSON 以 `judge_results.json` 文件形式提供下载。

拿到网页版 AI 返回的 JSON 后，导入并汇总：

```powershell
python tools\llm_eval.py import-judge `
  --input D:\Downloads\judge_results.json `
  --results scratch\llm_eval\run-YYYYMMDD_HHMMSS\results.jsonl
```

默认输出到该 run 目录的 `manual_judge_import/`，包含
`judge_results.jsonl`、`judge_summary.md`、`judge_summary.csv` 和
`judge_summary.json`。

## 建议实验口径

- 先用 `--limit 30 --repeat 1` 小跑，确认接口、价格和输出解析没问题。
- 正式比较时用 `--repeat 3` 或更高，稳定性和 p95 才有意义。
- 如果要看真实成本，把各模型的 `pricing` 改成实际供应商价格；没有 usage 的供应商只能统计时延和质量，费用会是 0 或空。
- `translate` 模式适合比较“准确模式/纯 LLM 翻译”；`refine` 模式适合比较“快速翻译 + LLM 修正”。

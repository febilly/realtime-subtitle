import json
from types import SimpleNamespace

from tools import llm_eval


def test_parse_transcript_file_pairs_source_with_translation(tmp_path):
    log_path = tmp_path / "transcript_20260708_010203.txt"
    log_path.write_text(
        "\n".join(
            [
                "=== Real-time Subtitle Log ===",
                "[18:30:58] [SPEAKER 1][JA] 怖い。",
                "[18:30:58] [SPEAKER 1][ZH][TRANS] 好可怕。",
                "[18:31:04] [SPEAKER 1][JA] しかも最近のパソコンって",
                "[18:31:06] [SPEAKER 1][JA] 、そういうの多いイメージあるから",
                "[18:31:06] [SPEAKER 1][ZH][TRANS] 而且最近的电脑，我总觉得那种东西很多。",
            ]
        ),
        encoding="utf-8",
    )

    rows = llm_eval.parse_transcript_file(log_path)

    assert len(rows) == 2
    assert rows[0]["source"] == "怖い。"
    assert rows[0]["draft_translation"] == "好可怕。"
    assert rows[0]["source_lang"] == "ja"
    assert rows[0]["target_lang"] == "zh"
    assert rows[1]["source"] == "しかも最近のパソコンって、そういうの多いイメージあるから"


def test_build_dataset_adds_previous_context(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "transcript_20260708_010203.txt").write_text(
        "\n".join(
            [
                "[18:30:58] [SPEAKER 1][JA] 怖い。",
                "[18:30:58] [SPEAKER 1][ZH][TRANS] 好可怕。",
                "[18:31:04] [SPEAKER 1][JA] 頼むで。",
                "[18:31:04] [SPEAKER 1][ZH][TRANS] 拜托了。",
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "dataset.jsonl"

    llm_eval.build_dataset(
        SimpleNamespace(
            logs_dir=str(logs_dir),
            pattern="transcript_*.txt",
            output=str(output),
            context=1,
            limit=0,
            sample="first",
            seed=42,
            source_lang="ja",
            target_lang="zh",
            min_source_chars=1,
            min_translation_chars=1,
            dedupe=True,
        )
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["context"] == []
    assert rows[1]["context"] == [{"source": "怖い。", "translation": "好可怕。"}]


def test_summarize_run_computes_latency_and_cost():
    summary = llm_eval.summarize_run(
        [
            {
                "model_name": "m",
                "status": "ok",
                "latency_ms": 100,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "estimated_cost": 0.001,
                "cost_currency": "CNY",
            },
            {
                "model_name": "m",
                "status": "error",
                "latency_ms": None,
                "usage": {},
                "estimated_cost": None,
                "cost_currency": "CNY",
            },
        ]
    )

    assert summary[0]["requests"] == 2
    assert summary[0]["ok"] == 1
    assert summary[0]["errors"] == 1
    assert summary[0]["latency_p50_ms"] == 100
    assert summary[0]["estimated_cost"] == 0.001
    assert summary[0]["cost_currency"] == "CNY"


def test_load_manual_judge_results_accepts_web_ai_result_object(tmp_path):
    path = tmp_path / "judge_results.json"
    path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "task_id": "sample_1",
                        "sample_id": "sample_1",
                        "ratings": [
                            {
                                "candidate_id": "model_a__r1",
                                "accuracy": 5,
                                "fluency": 4,
                                "completeness": 5,
                                "overall": 90,
                                "major_errors": [],
                                "comment": "good",
                            }
                        ],
                        "best_candidate_id": "model_a__r1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = llm_eval._load_manual_judge_results(path)
    summary = llm_eval.summarize_judgements(rows)

    assert rows[0]["sample_id"] == "sample_1"
    assert rows[0]["judge"]["best_candidate_id"] == "model_a__r1"
    assert summary[0]["candidate_id"] == "model_a__r1"
    assert summary[0]["overall_avg"] == 90

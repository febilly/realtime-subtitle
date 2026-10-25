import json
from collections import Counter

import pytest

from tools import refine_benchmark as bench


def test_env_blocks_use_matching_commented_credentials_and_boolean(tmp_path):
    env = tmp_path / '.env'
    env.write_text('# LLM_BASE_URL="https://old.invalid"\n# LLM_API_KEY="old-secret"\n'
                   '# LLM_MODEL="old"\nLLM_BASE_URL="https://new.invalid"\n'
                   'LLM_API_KEY="new-secret"\nLLM_MODEL="new"\n'
                   'LLM_REQUEST_JSON=\'{"enable_thinking":"false"}\'\n', encoding='utf-8')
    old = bench.resolve_model({'env_model': 'old'}, env)
    new = bench.resolve_model({'env_model': 'new'}, env)
    assert old['api_key'] == 'old-secret'
    assert new['api_key'] == 'new-secret'
    assert new['extra_json']['enable_thinking'] is False
    thinking = bench.resolve_model({'env_model': 'new', 'extra_json': {'enable_thinking': True}}, env)
    assert thinking['extra_json']['enable_thinking'] is True


def test_language_filter_excludes_spanish_and_handles_english_japanese_quotes():
    assert bench.source_language('Me dolió un poco porque no podía quedarme.') is None
    assert bench.source_language('You can add "です" to make it slightly more polite.') == 'en'
    assert bench.source_language('みたいな。') == 'ja'


def test_collect_never_uses_old_output_and_resets_file_and_gap(tmp_path):
    def row(source, timestamp):
        return {'event': 'refine_result', 'source': source, 'draft': '原始草稿',
                'target_lang': 'zh', 'ts': timestamp, 'refined': '不能进入输入的旧修正结果'}
    records = [row('One.', '2026-09-01T12:00:00'), row('Two.', '2026-09-01T12:00:05'),
               row('Three.', '2026-09-01T12:05:00')]
    (tmp_path / 'llm_1.jsonl').write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')
    (tmp_path / 'llm_2.jsonl').write_text(json.dumps(row('Four.', '2026-09-01T12:05:01')), encoding='utf-8')
    rows, _ = bench.collect(tmp_path)
    assert rows[1]['context'] == [{'source': 'One.'}]
    assert rows[2]['context'] == []
    assert rows[3]['context'] == []
    assert '不能进入输入的旧修正结果' not in json.dumps(rows, ensure_ascii=False)


def test_sampling_is_deterministic_balanced_and_source_preserving():
    pool = [{'id': str(i), 'source_lang': lang, 'length_bin': 'short', 'log_path': str(i % 3)}
            for lang in ('en', 'ja') for i in range(20)]
    first = bench.select_samples(pool, 20, 42)
    assert first == bench.select_samples(pool, 20, 42)
    assert Counter(r['source_lang'] for r in first) == {'en': 10, 'ja': 10}
    assert len(set((r['source_lang'], r['id']) for r in first)) == 20


def test_anonymous_judge_collapses_identical_outputs():
    sample = {'id': 'fixed', 'source_lang': 'en', 'source': 'Hello', 'context': [], 'draft_translation': '你好'}
    payload, mapping = bench.judge_task(sample, {'old': {'output_translation': '你好'}, 'new': {'output_translation': '您好'}})
    assert len(payload['translations']) == 2
    assert sorted(n for names in mapping.values() for n in names) == ['baseline', 'new', 'old']
    assert 'old' not in json.dumps(payload) and 'new' not in json.dumps(payload)


def test_invalid_judgement_cannot_silently_reduce_denominator():
    value = {'ratings': [], 'best_ids': ['T0'], 'uncertainty': False}
    with pytest.raises(ValueError):
        bench.validate_judge(value, {'T0': ['baseline']})


def test_edit_ratio_distinguishes_whitespace_and_content():
    assert bench.edit_metrics('你 好', '你好')['edit_ratio'] == 0
    assert bench.edit_metrics('你好', '您好')['edit_chars'] == 1
    assert bench.edit_metrics('你好', '您好')['edit_ratio'] == .5


def test_resume_latest_success_preserves_success_after_failed_retry():
    rows = [{'sample_id': 'a', 'model_name': 'm', 'status': 'ok', 'value': 1},
            {'sample_id': 'a', 'model_name': 'm', 'status': 'error', 'value': 2}]
    assert bench.latest_success(rows, ['sample_id', 'model_name'])[('a', 'm')]['value'] == 1


def test_manual_import_rejects_changed_input_without_writing_partial_scores(tmp_path, monkeypatch):
    sample = {'id': 'a', 'source': 'Hello.', 'source_lang': 'en', 'draft_translation': '你好', 'context': []}
    results = [{'sample_id': 'a', 'model_name': n, 'status': 'ok', 'output_translation': '你好'} for n in ('old', 'new')]
    bench.ev._write_jsonl(tmp_path / 'results.jsonl', results)
    incoming = tmp_path / 'incoming.json'
    incoming.write_text(json.dumps([{'sample_id': 'a', 'input_hash': 'wrong'}]), encoding='utf-8')
    monkeypatch.setattr(bench, 'load_experiment', lambda args: (tmp_path, [sample], {}, {}, [{'name': 'old'}, {'name': 'new'}], {}, ''))
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='different translations'):
        bench.import_review(SimpleNamespace(input=str(incoming)))
    assert not (tmp_path / 'manual_judgements.jsonl').exists()


def test_judge_inconsistent_score_and_verdict_are_flagged_not_rewritten():
    value = {'ratings': [
        {'id': 'T0', 'meaning': 2, 'fluency': 3, 'meaning_change': 'same', 'fluency_change': 'same'},
        {'id': 'T1', 'meaning': 4, 'fluency': 4, 'meaning_change': 'same', 'fluency_change': 'improved'}]}
    issues = bench.judgement_consistency(value, {'T0': ['baseline'], 'T1': ['old']})
    assert issues == [{'candidate': 'T1', 'dimension': 'meaning', 'score_delta': 2, 'declared_change': 'same'}]
    assert value['ratings'][1]['meaning_change'] == 'same'


def test_run_preserves_per_variant_sampling_parameters_and_resumes(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    sample = {'id': 'a', 'source_lang': 'en', 'source': 'Hello.',
              'draft_translation': '你好。', 'user_prompt': 'fixture'}
    models = [{'name': 'default', 'prompt': 'p'},
              {'name': 'deterministic', 'prompt': 'p', 'temperature': 0, 'max_tokens': 64}]
    config = {'temperature': .2, 'max_tokens': 1024}
    monkeypatch.setattr(bench, 'load_experiment', lambda args:
                        (tmp_path, [sample], config, {'p': 'prompt'}, models, models, 'frozen'))
    calls = []

    async def request(session, model, messages, temperature, max_tokens):
        calls.append((model['name'], temperature, max_tokens))
        return {'content': '__NO_CHANGE__', 'latency_ms': 1, 'usage': {}, 'response_model': 'fixture'}, 1

    monkeypatch.setattr(bench, 'request', request)
    args = SimpleNamespace(limit=0, concurrency=2)
    asyncio.run(bench.run(args))
    assert sorted(calls) == [('default', .2, 1024), ('deterministic', 0, 64)]
    results = {r['model_name']: r for r in bench.read_rows(tmp_path / 'results.jsonl')}
    assert results['deterministic']['temperature'] == 0
    assert results['deterministic']['max_tokens'] == 64
    assert all(r['status'] == 'ok' and r['output_translation'] == '你好。' for r in results.values())
    asyncio.run(bench.run(args))
    assert len(calls) == 2


@pytest.mark.parametrize('verdict,accepted', [
    ({'accept': True, 'reason': '修复否定'}, True),
    ({'accept': False, 'reason': '无明确错误'}, False),
    ({'accept': 'true', 'reason': '错误类型'}, False),
    ({'accept': True}, False),
])
def test_optional_verifier_only_checks_changed_candidates(tmp_path, monkeypatch, verdict, accepted):
    import asyncio
    from types import SimpleNamespace

    samples = [{'id': n, 'source_lang': 'en', 'source': 'I am not ready.',
                'draft_translation': '我准备好了。', 'user_prompt': n} for n in ('keep', 'fix')]
    models = [{'name': 'checked', 'prompt': 'p', 'verifier_prompt': 'v',
               'verifier_response_format': None, 'verifier_max_tokens': 256}]
    monkeypatch.setattr(bench, 'load_experiment', lambda args:
                        (tmp_path, samples, {'temperature': .2, 'max_tokens': 1024},
                         {'p': 'primary', 'v': 'verifier'}, models, models, 'frozen'))
    calls = []

    async def request(session, model, messages, temperature, max_tokens):
        calls.append(messages)
        if messages[0]['content'] == 'verifier':
            assert temperature == 0 and max_tokens == 256
            assert 'response_format' not in model['extra_json']
            content = json.dumps(verdict)
        else:
            content = '__NO_CHANGE__' if messages[1]['content'] == 'keep' else '我没准备好。'
        return {'content': content, 'latency_ms': 1, 'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
                'response_model': 'fixture'}, 1

    monkeypatch.setattr(bench, 'request', request)
    asyncio.run(bench.run(SimpleNamespace(limit=0, concurrency=1)))
    rows = {r['sample_id']: r for r in bench.read_rows(tmp_path/'results.jsonl')}
    assert len(calls) == 3
    assert rows['keep']['verification'] is None
    assert rows['fix']['verification']['accepted'] is accepted
    assert rows['fix']['output_translation'] == ('我没准备好。' if accepted else '我准备好了。')
    assert rows['fix']['usage']['prompt_tokens'] == 20


def test_build_freezes_named_prompts_and_rejects_missing_references(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import llm_refine

    sample = {'id': 'a', 'source_lang': 'en', 'source': 'Hello.', 'draft_translation': '你好。',
              'context': [], 'log_path': 'fixture', 'length_bin': 'short'}
    monkeypatch.setattr(bench, 'collect', lambda *args: ([sample], {}))
    monkeypatch.setattr(bench, 'select_samples', lambda *args: [sample])
    monkeypatch.setattr(bench.subprocess, 'check_output', lambda cmd, **kw:
                        b"_REFINE_SYSTEM_PROMPT = 'old'" if cmd[1] == 'show' else b'fixture-head')
    monkeypatch.setattr(llm_refine.config, 'llm_context_bounds', llm_refine.config.llm_context_bounds)
    monkeypatch.setattr(llm_refine.config, 'LLM_PROMPT_SUFFIX', llm_refine.config.LLM_PROMPT_SUFFIX)
    prompt_path, model_path = tmp_path/'prompts.json', tmp_path/'models.json'
    bench.ev._write_json(prompt_path, {'candidate': 'frozen Qwen prompt'})
    bench.ev._write_json(model_path, {'models': [{'name': 'qwen', 'env_model': 'qwen3.7-flash', 'prompt': 'candidate'}]})
    args = SimpleNamespace(output=str(tmp_path/'ok'), logs='', context=3, size=1, seed=42,
                           models_config=str(model_path), prompts_config=str(prompt_path))
    bench.build(args)
    prompts = bench.ev._read_json(tmp_path/'ok/prompts.json')
    assert prompts['candidate'] == 'frozen Qwen prompt'
    assert bench.ev._read_json(tmp_path/'ok/manifest.json')['prompts_sha256'] == bench.digest(prompts)
    args.output = str(tmp_path/'invalid')
    args.prompts_config = None
    with pytest.raises(ValueError, match='unknown system prompt'):
        bench.build(args)
    assert not (tmp_path/'invalid/dataset.jsonl').exists()

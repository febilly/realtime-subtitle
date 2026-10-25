import json

import pytest

from tools.refine_response_protocols import parse_refine_response


def test_edits_use_original_offsets_and_preserve_unaffected_words():
    value = {'edits': [
        {'source_span': 'eight', 'old': '三条', 'new': '八条', 'kind': 'number'},
        {'source_span': 'She sent him', 'old': '他给她', 'new': '她给他', 'kind': 'role'},
    ]}
    result = parse_refine_response(json.dumps(value), '哎呀，他给她发了三条消息呢。', 'She sent him eight messages.')
    assert result['refined'] == '哎呀，她给他发了八条消息呢。'


@pytest.mark.parametrize('edits', [
    [{'source_span': 'dog', 'old': '猫', 'new': '狗', 'kind': 'entity'}],
    [{'source_span': 'animal', 'old': '猫', 'new': '动物', 'kind': 'style'}],
    [{'source_span': 'animal', 'old': '不存在', 'new': '动物', 'kind': 'entity'}],
    [{'source_span': 'animal', 'old': '它是猫', 'new': '动物', 'kind': 'entity'},
     {'source_span': 'animal', 'old': '猫', 'new': '动物', 'kind': 'entity'}],
    [{'source_span': 'animal', 'old': '它是猫', 'new': '', 'kind': 'addition'}],
    [{'source_span': 'animal', 'old': '猫', 'new': 1, 'kind': 'entity'}],
])
def test_invalid_patch_is_atomic_and_retains_draft(edits):
    result = parse_refine_response(json.dumps({'edits': edits}), '它是猫', 'It is an animal.')
    assert result == {'has_answer': True, 'no_change': True, 'refined': '', 'category': 'invalid_edits'}


def test_repeated_old_span_requires_a_unique_surrounding_span():
    value = {'edits': [{'source_span': 'she', 'old': '他', 'new': '她', 'kind': 'role'}]}
    assert parse_refine_response(json.dumps(value), '他对他笑', 'He smiled at her; she smiled.')['category'] == 'invalid_edits'


@pytest.mark.parametrize('value', [
    {'edits': []},
    {'issue': {'kind': 'none', 'source_span': '', 'draft_span': ''}, 'translation': ''},
])
def test_structured_keep_matches_existing_plain_text_protocol(value):
    assert parse_refine_response(json.dumps(value), '你好', 'Hello') == {
        'has_answer': True, 'no_change': True, 'refined': '', 'category': ''}


def test_full_translation_requires_semantic_issue_anchored_to_both_inputs():
    value = {'issue': {'kind': 'negation', 'source_span': 'not', 'draft_span': '是医生'}, 'translation': '我不是医生。'}
    assert parse_refine_response(json.dumps(value), '我是医生。', 'I am not a doctor.')['refined'] == '我不是医生。'
    value['issue']['source_span'] = 'never'
    assert parse_refine_response(json.dumps(value), '我是医生。', 'I am not a doctor.')['category'] == 'invalid_edits'


def test_broken_json_cannot_leak_into_subtitles():
    result = parse_refine_response('{"edits": [', '你好', 'Hello')
    assert result['no_change'] and result['category'] == 'invalid_edits'


@pytest.mark.parametrize('value', [
    {'comparison': '意思相同', 'decision': 'keep', 'translation': ''},
    {'comparison': '词义不确定', 'decision': 'uncertain', 'translation': ''},
])
def test_compare_protocol_keeps_draft(value):
    assert parse_refine_response(json.dumps(value), '你好', 'Hello')['no_change']


@pytest.mark.parametrize('value', [
    {'comparison': '', 'decision': 'fix', 'translation': '她来了'},
    {'comparison': '理由', 'decision': 'keep', 'translation': '她来了'},
    {'comparison': '理由', 'decision': 'fix', 'translation': 3},
    {'comparison': '理由', 'decision': 'unknown', 'translation': ''},
])
def test_compare_protocol_invalid_decisions_are_rejected(value):
    assert parse_refine_response(json.dumps(value), '你好', 'Hello')['category'] == 'invalid_edits'

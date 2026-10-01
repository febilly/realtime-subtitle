"""Experimental benchmark response formats; production subtitles use plain text."""
import json
import re

_SEMANTIC_EDIT_KINDS = {"role", "negation", "condition", "number", "entity", "action", "omission", "addition"}


def _parse_semantic_json(value: dict, draft: str, source: str) -> str:
    """Validate source-grounded edits atomically; never apply ambiguous/overlapping spans."""
    if set(value) == {'comparison', 'decision', 'translation'}:
        if not all(isinstance(v, str) for v in value.values()) or not value['comparison'] or len(value['comparison']) > 200:
            raise ValueError('invalid_comparison')
        if value['decision'] in {'keep', 'uncertain'} and not value['translation']:
            return draft
        if value['decision'] == 'fix' and value['translation'].strip():
            return value['translation']
        raise ValueError('invalid_decision')
    if set(value) == {"edits"}:
        edits = value['edits']
        if not isinstance(edits, list) or len(edits) > 24:
            raise ValueError('invalid_edits')
        spans = []
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {'source_span', 'old', 'new', 'kind'}:
                raise ValueError('invalid_edit_fields')
            if not all(isinstance(edit[k], str) for k in edit):
                raise ValueError('invalid_edit_type')
            old, new, evidence = edit['old'], edit['new'], edit['source_span']
            if (edit['kind'] not in _SEMANTIC_EDIT_KINDS or not evidence or evidence not in source
                    or not old or draft.count(old) != 1):
                raise ValueError('unanchored_edit')
            start = draft.index(old)
            spans.append((start, start + len(old), new))
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError('overlapping_edits')
        output = draft
        for start, end, new in reversed(spans):
            output = output[:start] + new + output[end:]
        if not output.strip():
            raise ValueError('empty_translation')
        return output
    if set(value) == {'issue', 'translation'}:
        issue, output = value['issue'], value['translation']
        if (not isinstance(issue, dict) or set(issue) != {'kind', 'source_span', 'draft_span'}
                or not all(isinstance(v, str) for v in issue.values()) or not isinstance(output, str)):
            raise ValueError('invalid_issue')
        if issue['kind'] == 'none' and not issue['source_span'] and not issue['draft_span'] and not output:
            return draft
        if (issue['kind'] not in _SEMANTIC_EDIT_KINDS or not issue['source_span']
                or issue['source_span'] not in source or not issue['draft_span']
                or issue['draft_span'] not in draft or not output.strip()):
            raise ValueError('unanchored_translation')
        return output
    raise ValueError('unknown_schema')


def parse_refine_response(raw_content: str, draft: str, source: str = "") -> dict:
    import llm_refine

    answer = llm_refine._strip_code_fence(raw_content)
    if answer.startswith('{'):
        try:
            value = json.loads(answer)
        except ValueError:
            value = None
        if isinstance(value, dict) and ('edits' in value or 'issue' in value or 'comparison' in value):
            try:
                answer = _parse_semantic_json(value, draft or '', source or '')
            except ValueError:
                return {"has_answer": True, "no_change": True, "refined": "", "category": "invalid_edits"}
        elif value is None and re.search(r'"(?:edits|issue|comparison)"\s*:', answer):
            return {"has_answer": True, "no_change": True, "refined": "", "category": "invalid_edits"}
    return llm_refine.parse_refine_response(answer, draft, source)

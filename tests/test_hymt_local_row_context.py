from streaming_translation.api.hymt2 import HyMT2API


class _FakeLocalEngine:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, draft=None):
        self.prompts.append((prompt, draft))
        return f"translated-{len(self.prompts)}", [len(self.prompts)]


def _local_api():
    # Avoid loading a model: the API instance still uses the same local prompt
    # and revision state path while this fake captures its generate call.
    api = HyMT2API(backend="api")
    api.backend = "local"
    api._local_engine = _FakeLocalEngine()
    return api


def test_local_row_prompt_matches_training_template_and_ignores_following_source():
    api = _local_api()

    result = api.translate(
        "Current source",
        source_language="en",
        target_language="zh",
        context_pairs=[
            {"source": "Untranslated", "target": ""},
            *({"source": f"S{i}", "target": f"T{i}"} for i in range(7)),
        ],
        following_source="Following source",
        is_partial=True,
    )

    prompt, _draft = api._local_engine.prompts[0]
    # Same text the LoRA was trained on (hy-mt-2-lora v4/pipeline/prompt_v4.py):
    # at most five translated history pairs, no following-source section.
    assert prompt == (
        "<｜hy_User｜>[Background Information]\n"
        "Recent utterances and their translations:\n"
        "S2 → T2\nS3 → T3\nS4 → T4\nS5 → T5\nS6 → T6\n\n"
        "When the source meaning has not changed, preserve the still-correct prefix "
        "of the previous translation whenever possible. When content is added or "
        "corrected, accuracy and completeness take priority.\n\n"
        "Please translate the following text from English into Chinese, taking the provided "
        "background information into consideration.\n\n[Source Text]\n"
        "Current source<｜hy_Assistant｜>"
    )
    assert result == "translated-1"


def test_local_revision_prompt_orders_previous_version_after_history():
    api = _local_api()
    context = [{"source": "Prior", "target": "前文"}]

    api.translate("Draft", source_language="en", target_language="zh", context_pairs=context, is_partial=True)
    api.translate("Draft more", source_language="en", target_language="zh", context_pairs=context, is_partial=True)

    prompt, draft = api._local_engine.prompts[1]
    assert (
        "Recent utterances and their translations:\nPrior → 前文\n\n"
        "Previous version of the current source:\nDraft\n\n"
        "Previous translation of the current source:\ntranslated-1\n\n"
        "When the source meaning"
    ) in prompt
    assert draft == [1]


def test_local_no_context_prompt_stays_on_the_existing_simple_path():
    api = _local_api()

    api.translate("Hello", source_language="en", target_language="zh", is_partial=True)

    prompt, _draft = api._local_engine.prompts[0]
    assert prompt == (
        "<｜hy_User｜>Translate the following text from English into Chinese. Note that you should "
        "only output the translated result without any additional explanation:\n\n"
        "Hello<｜hy_Assistant｜>"
    )


def test_local_final_reuse_requires_matching_row_context():
    api = _local_api()
    first_context = [{"source": "Prior A", "target": "前文 A"}]
    changed_context = [{"source": "Prior B", "target": "前文 B"}]

    partial = api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        is_partial=True,
    )
    assert api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        is_partial=False,
    ) == partial
    assert len(api._local_engine.prompts) == 1

    api.reset_session()
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        is_partial=True,
    )
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=changed_context,
        is_partial=False,
    )
    assert len(api._local_engine.prompts) == 3

    api.reset_session()
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        following_source="Next A",
        is_partial=True,
    )
    # Following rows are not part of the prompt, so they must not block reuse.
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        following_source="Next B",
        is_partial=False,
    )
    assert len(api._local_engine.prompts) == 4

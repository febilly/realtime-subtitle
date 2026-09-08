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


def test_local_row_prompt_keeps_following_source_as_background_only():
    api = _local_api()

    result = api.translate(
        "Current source",
        source_language="en",
        target_language="zh",
        context_pairs=[{"source": "Previous source", "target": "之前译文"}],
        following_source="Following source",
        is_partial=True,
    )

    prompt, _draft = api._local_engine.prompts[0]
    assert "Previous source/translation context:" in prompt
    assert "Source: Previous source" in prompt
    assert "Translation: 之前译文" in prompt
    assert "Following source context (reference only; do not translate or output):\nFollowing source" in prompt
    assert "Translate only the [Source Text] below." in prompt
    assert prompt.endswith("[Source Text]\nCurrent source<｜hy_Assistant｜>")
    assert result == "translated-1"
    assert "Following source" not in result


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
        following_source="Next A",
        is_partial=True,
    )
    assert api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        following_source="Next A",
        is_partial=False,
    ) == partial
    assert len(api._local_engine.prompts) == 1

    api.reset_session()
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        following_source="Next A",
        is_partial=True,
    )
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=changed_context,
        following_source="Next A",
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
    api.translate(
        "Same source",
        source_language="en",
        target_language="zh",
        context_pairs=first_context,
        following_source="Next B",
        is_partial=False,
    )
    assert len(api._local_engine.prompts) == 5

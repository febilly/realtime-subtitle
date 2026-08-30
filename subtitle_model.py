"""字幕数据模型: token 流 → 说话人块/句子 (桌面浮窗与 VR 浮层共用)。

从 overlay_window.py 提取, 保持与网页版 renderSubtitles 相同的归组语义。
浮窗与 VR「显示内容一致」依赖于此: 两边跑的是同一份分句代码。
"""

SENTENCE_PUNCT = "。．.!！?？…"


def _ensure_speaker(spk):
    return "undefined" if spk is None else spk


class SubtitleModel:
    """维护 final / non-final token，并产出可渲染的「说话人块」结构。"""

    def __init__(self, sid_boundaries: bool = False):
        self.final_tokens = []
        self.non_final_tokens = []
        # VR 镜像专用 (默认关, 桌面行为逐字节不变): 按后端 SentencePairer
        # 盖章的 llm_sentence_id 分行 —— 新句直接换行、draft 不得冲入定稿句、
        # 迟到译文按 sid 归位。
        self.sid_boundaries = sid_boundaries

    def clear(self, preserve_existing=False):
        if preserve_existing:
            # 把进行中的 token 落定，避免重启时闪烁
            for tk in self.non_final_tokens:
                tk = dict(tk)
                tk["is_final"] = True
                self.final_tokens.append(tk)
        else:
            self.final_tokens = []
        self.non_final_tokens = []

    def apply_update(self, data: dict):
        for tk in (data.get("final_tokens") or []):
            if tk.get("text") == "<end>":
                continue
            self.final_tokens.append(tk)
        self.non_final_tokens = [
            tk for tk in (data.get("non_final_tokens") or [])
            if tk.get("text") != "<end>"
        ]

    # --- 构建渲染 token（final + non-final，必要时补 speculative 分隔） ---
    def _build_render_tokens(self):
        non_final = self.non_final_tokens or []
        has_nf_translation = any(
            (tk.get("translation_status") or "original") == "translation"
            for tk in non_final
        )
        if has_nf_translation:
            return [*self.final_tokens, *non_final]

        tokens = list(self.final_tokens)
        n = len(non_final)
        for i, tk in enumerate(non_final):
            tokens.append(tk)
            is_last = i == n - 1
            text = (tk.get("text") or "").rstrip()
            if (not is_last and not tk.get("is_separator")
                    and text and text[-1] in SENTENCE_PUNCT):
                tokens.append({"is_separator": True, "is_final": False})
        return tokens

    # --- 分句（移植 renderSubtitles 的归组算法，去掉 furigana/LLM 等） ---
    def build_blocks(self):
        tokens = self._build_render_tokens()
        sentences = []
        current = None

        def start_sentence(
            speaker,
            requires=None,
            translation_only=False,
            sid=None,
        ):
            nonlocal current
            s = {
                "speaker": _ensure_speaker(speaker),
                "original": [],
                "translation": [],
                "original_lang": None,
                "translation_lang": None,
                "requires_translation": requires,
                "translation_only": translation_only,
                "fake_translation": False,
                # 后端 SentencePairer 盖章的权威句身份 (soniox 全部 final token
                # 必带; 无 sid 的后端回退旧启发式, 行为不变)。
                "sid": sid,
                # 该句已有带 sid 的 final 译文 = 语义上已封口。
                "has_final_translation": False,
            }
            sentences.append(s)
            if not translation_only:
                current = s
            return s

        def find_last(speaker, predicate):
            spk = _ensure_speaker(speaker)
            for s in reversed(sentences):
                if s["speaker"] == spk and predicate(s):
                    return s
            return None

        for token in tokens:
            if token.get("is_separator"):
                if (current and current["requires_translation"] is not False
                        and not current["translation"]):
                    current["fake_translation"] = True
                current = None
                continue

            speaker = _ensure_speaker(token.get("speaker"))
            status = token.get("translation_status") or "original"
            # 开关关闭时 sid 永远为 None → 所有 sid 规则失效, 回到旧启发式。
            raw_sid = token.get("llm_sentence_id") if self.sid_boundaries else None
            sid = str(raw_sid) if raw_sid not in (None, "") else None

            if status == "translation":
                target = None
                if sid is not None:
                    # 迟到的译文按 sid 归位到它自己的句子, 不得挂到更新的句子上。
                    target = find_last(
                        speaker,
                        lambda s, _sid=sid: (not s["translation_only"])
                        and s["sid"] == _sid,
                    )
                if target is None:
                    target = find_last(speaker, lambda s: not s["translation_only"])
                if target is None:
                    target = start_sentence(speaker, translation_only=True, sid=sid)
                if sid is not None:
                    if target["sid"] is None:
                        target["sid"] = sid
                    target["has_final_translation"] = True
                if target["translation_lang"] is None and token.get("language"):
                    target["translation_lang"] = token.get("language")
                if not target["original_lang"] and token.get("source_language"):
                    target["original_lang"] = token.get("source_language")
                target["translation"].append(token)
            else:
                requires = status != "none"
                start_new = False
                if not current:
                    start_new = True
                elif current["speaker"] != speaker:
                    start_new = True
                elif current["translation_only"]:
                    start_new = True
                elif (
                    sid is not None
                    and current["sid"] is not None
                    and sid != current["sid"]
                ):
                    # 后端 pairer 已判定这是新的一句 → 直接换行。
                    start_new = True
                elif sid is None and current["sid"] is not None:
                    # 无 sid 的 live draft 不得冲入已定稿/已译出的句子:
                    # 它就是下一句的开头 (本次修复的核心泄漏路径)。
                    start_new = True
                elif (
                    sid is not None
                    and current["sid"] is None
                    and current["has_final_translation"]
                ):
                    start_new = True
                elif (current["requires_translation"] is not None
                      and current["requires_translation"] != requires):
                    start_new = True
                if start_new:
                    current = start_sentence(speaker, requires=requires, sid=sid)
                elif sid is not None and current["sid"] is None:
                    current["sid"] = sid
                if current["requires_translation"] is None:
                    current["requires_translation"] = requires
                lang = token.get("language")
                if current["original_lang"] is None and lang:
                    current["original_lang"] = lang
                elif current["original_lang"] and lang and current["original_lang"] != lang:
                    current = start_sentence(speaker, requires=requires, sid=sid)
                    current["original_lang"] = lang
                    if sid is not None:
                        current["sid"] = sid
                current["original"].append(token)

        # 归并为说话人块
        blocks = []
        block = None
        for s in sentences:
            if not s["original"] and not s["translation"]:
                continue
            if not block or block["speaker"] != s["speaker"]:
                if block:
                    blocks.append(block)
                block = {"speaker": s["speaker"], "sentences": []}
            block["sentences"].append(s)
        if block:
            blocks.append(block)
        return blocks

    def trim_final_tokens_to_recent_sentences(self, max_sentences: int) -> None:
        """Trim history on the same sentence boundaries used for display."""
        if max_sentences <= 0 or not self.final_tokens:
            return

        saved_non_final = self.non_final_tokens
        self.non_final_tokens = []
        try:
            blocks = self.build_blocks()
        finally:
            self.non_final_tokens = saved_non_final

        sentences = [
            sentence
            for block in blocks
            for sentence in block["sentences"]
            if sentence["original"] or sentence["translation"]
        ]
        if len(sentences) <= max_sentences:
            return

        retained = sentences[-max_sentences:]
        retained_token_ids = {
            id(token)
            for sentence in retained
            for token in (sentence["original"] + sentence["translation"])
        }
        if not retained_token_ids:
            return

        first_index = next(
            (
                index
                for index, token in enumerate(self.final_tokens)
                if id(token) in retained_token_ids
            ),
            None,
        )
        if first_index is not None and first_index > 0:
            self.final_tokens = self.final_tokens[first_index:]


def sentence_sid(sentence: dict):
    """取句子级 LLM sentence_id (优先 build_blocks 记录的权威 sid)。"""
    sid = sentence.get("sid")
    if sid:
        return str(sid)
    for key in ("translation", "original"):
        for tk in sentence.get(key) or []:
            sid = tk.get("llm_sentence_id")
            if sid:
                return str(sid)
    return None

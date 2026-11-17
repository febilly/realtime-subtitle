"""
Hy-MT2 (HunYuan-MT 1.8B) real-time streaming translation via WebSocket.

Implements the stateless context-revision protocol documented in
`Hy-MT2-1.8B 实时文本流翻译服务接入文档` (INTEGRATION.md):

    Client -> Server : init / update (UTF-8 JSON text frames, one per frame)
    Server -> Client : init_ok / translation / error

Per the protocol the server keeps **no session state**: every ``update``
message is fully self-contained and carries the client-held revision chain
(``previous_source`` / ``previous_translation`` / ``history``).  This class
owns that chain for the sentence currently being translated, exactly like the
protocol prescribes.

The WebSocket address is intentionally **not** hard-coded: it is injected by
the caller (Web UI setting / ``HYMT2_WEBSOCKET_URL`` env var) and must be
filled in by the user.
"""
from __future__ import annotations

import gc
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Dict, List, Optional
from realtime_subtitle.shared.hymt_languages import HYMT2_LANGUAGE_NAMES, normalize_hymt_language

from realtime_subtitle.local_inference.repetition_guard import (
    RETRY_REPEAT_PENALTY,
    is_runaway,
    max_translation_tokens,
    source_is_repetitive,
)

from .base import BaseTranslationAPI

try:
    from websockets.sync.client import connect as _sync_ws_connect
except ImportError:  # pragma: no cover
    raise ImportError(
        "websockets not installed. Run: pip install websockets>=12"
    )

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
GGML_TYPE_Q8_0 = 8
MAX_HISTORY = 8        # 服务端接受 <=10 条，取 8 与本应用上下文窗口一致
HISTORY_KEEP = 20      # 本地保留的完成句对上限（截断后再取最新一条发送）
TRAINED_HISTORY_PAIRS = 5  # 微调数据（v4 build_traj.HISTORY_WINDOW）里历史最多 5 对
# 压低 EOS 后，续写里又出现上一版译文开头这么多字，就认定是在重写整段
REPEAT_PROBE_CHARS = 10
# 去掉上一版重翻的结果，实际内容至少多这么多倍才采用
FRESH_MIN_GROWTH = 1.05
MIN_REPEAT_PROBE_CHARS = 4

LANGUAGE_NAMES: Dict[str, str] = HYMT2_LANGUAGE_NAMES


# ── 临时实验：LOCAL_MT_MODEL=index 时本地翻译换成 Index-Translate-2B（未微调） ──
# 模型：IndexTeam/Index-Translate-2B-GGUF（Qwen3.5 混合线性注意力）。递归状态不能截断，
# 所以这个模式下关掉 prompt 前缀复用和草稿投机解码，每次整段重算；提示词用官方模板，
# 不带上一版原文/译文和历史（模型没学过这套流式修订格式）。
INDEX_TRANSLATE_LANG_NAMES = {
    "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语",
    "es": "西班牙语", "ru": "俄语", "pt": "葡萄牙语", "it": "意大利语",
}


def index_translate_enabled() -> bool:
    import os

    return os.environ.get("LOCAL_MT_MODEL", "").strip().lower() in ("index", "index-translate")


def index_translate_model_path() -> Optional[str]:
    import os

    explicit = os.environ.get("INDEX_TRANSLATE_GGUF", "").strip()
    if explicit:
        return explicit
    from realtime_subtitle.local_inference.model_manager import MODELS_DIR

    files = sorted((MODELS_DIR.resolve() / "index-translate").glob("*.gguf"))
    files = [f for f in files if "mmproj" not in f.name]
    return str(files[0]) if files else None


def index_prompt_context_enabled() -> bool:
    import os

    return os.environ.get("INDEX_PROMPT_CONTEXT", "1").strip().lower() not in ("0", "false", "off", "no")


_INDEX_TRAILING_ELLIPSIS = re.compile(r"(\.\.\.|…+|⋯+)\s*$")


def index_clean_output(text: str) -> str:
    """模型偶尔在译文后空一行再追加"（注：……）"之类的说明；字幕只要第一段。"""
    return (text or "").split("\n\n")[0].strip()


def index_final_fresh() -> bool:
    import os

    return os.environ.get("INDEX_FINAL_FRESH", "").strip().lower() in ("1", "true", "on", "yes")


def _index_lang_name(code: str) -> str:
    return INDEX_TRANSLATE_LANG_NAMES.get(code) or LANGUAGE_NAMES.get(code, code)


def render_index_prompt(
    target_lang: str,
    text: str,
    *,
    source_lang: str = "",
    history: Optional[List[List[str]]] = None,
    previous_source: str = "",
    previous_translation: str = "",
) -> str:
    import os

    target = _index_lang_name(target_lang)
    if not index_prompt_context_enabled() or not (history or previous_source or previous_translation):
        # docs/prompts.md 默认翻译模板
        content = f"请将以下文本翻译为{target}，直接输出翻译结果，不要进行任何解释：\n\n{text}"
    elif os.environ.get("INDEX_PROMPT_STYLE", "bg").strip().lower() == "inst":
        # docs/prompts.md instTrans 中文标准模板：【源文】+【约束要求】（流式信息都写成【注意】软约束）
        source = _index_lang_name(source_lang) if source_lang else ""
        rules = [
            "【注意】这是实时语音字幕，源文可能是一句还没说完的话，只翻译源文已有的内容，不要补全或猜测后文。"
        ]
        if history:
            rules.append(
                "【注意】跨句一致：前面几句及其译文如下，用词和称呼与之保持一致——"
                + "；".join(f"{src} → {tgt}" for src, tgt in history)
            )
        if previous_source or previous_translation:
            rules.append(
                f"【注意】这句话的上一版原文是「{previous_source}」，上一版译文是「{previous_translation}」。"
                "源文意思没有变的部分，尽量沿用上一版译文中仍然正确的开头，不要无故改写；"
                "源文有新增或更正时，以准确完整为先。"
            )
        numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))
        content = (
            f"请将以下{source}口语字幕翻译成{target}，并且严格遵循所有约束要求。\n\n"
            f"【源文】\n{text}\n\n【约束要求】\n{numbered}\n\n只输出译文，不要有任何额外说明。"
        )
    elif os.environ.get("INDEX_PROMPT_STYLE", "bg").strip().lower() == "bg":
        # 2026-11-17 提示词 A/B 胜出版（v2）：上一版标成"已过时/只供参考"、去掉上一版末尾省略号、
        # 指出源文新增的部分、要求完整覆盖。漏译明显少于 bg0，代价是中间版擦写略多。
        background = [
            "这是实时语音字幕的流式翻译。【源文】是正在说的一句话，可能还没说完，之后会继续变长或被更正。"
        ]
        if history:
            background.append(
                "前面几句及其译文：\n" + "\n".join(f"{src} → {tgt}" for src, tgt in history)
            )
        prev_t = _INDEX_TRAILING_ELLIPSIS.sub("", previous_translation or "").strip()
        if previous_source:
            background.append("这句话的上一版原文（更早、更短，已过时）：\n" + previous_source)
        if prev_t:
            background.append("这句话的上一版译文（只供参考）：\n" + prev_t)
        if previous_source and text.startswith(previous_source) and len(text) > len(previous_source):
            background.append(
                f"相比上一版，【源文】新增了：「{text[len(previous_source):].strip()}」。译文必须包含这部分的翻译。"
            )
        background.append(
            "要求：译文必须完整翻译【源文】的全部内容，不能停在上一版译文的结尾，也不要照抄上一版末尾的省略号。"
            "在完整的前提下，原文意思没变的部分尽量沿用上一版译文中仍然正确的说法，不要无故改写。"
            "背景信息只供参考，只翻译【源文】。"
        )
        content = (
            f"请将以下文本翻译为{target}，直接输出翻译结果，不要进行任何解释。\n\n【背景信息】\n"
            + "\n\n".join(background) + f"\n\n【源文】\n{text}"
        )
    else:  # bg0：第一版自写格式
        background = [
            "这是实时语音字幕的流式翻译。【源文】是正在说的一句话，可能还没说完，之后会继续变长或被更正。"
        ]
        if history:
            background.append(
                "前面几句及其译文：\n" + "\n".join(f"{src} → {tgt}" for src, tgt in history)
            )
        if previous_source:
            background.append("这句话的上一版原文：\n" + previous_source)
        if previous_translation:
            background.append("这句话的上一版译文：\n" + previous_translation)
        background.append(
            "要求：原文意思没有变的部分，尽量保留上一版译文中仍然正确的开头，不要无故改写；"
            "原文有新增或更正时，以准确完整为先。背景信息只供参考，只翻译【源文】。"
        )
        content = (
            f"请将以下文本翻译为{target}，直接输出翻译结果，不要进行任何解释。\n\n【背景信息】\n"
            + "\n\n".join(background) + f"\n\n【源文】\n{text}"
        )
    # Qwen3.5 聊天模板，enable_thinking=false
    return (
        f"<|im_start|>user\n{content}<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def _local_backend_available() -> bool:
    """本地推理后端是否可用（仅本地推理版打包 / 源码模式带 local_inference 运行时）。"""
    try:
        from realtime_subtitle.local_inference import is_local_inference_ui_enabled
    except Exception:
        return False
    try:
        return bool(is_local_inference_ui_enabled())
    except Exception:
        return False


def _sanitize_local_device(value) -> str:
    """把运行位置收敛成 'auto' / 'cpu' / 'vulkan:N'（不触碰 GPU 运行时）。"""
    normalized = str(value or "").strip().lower()
    if normalized == "cpu":
        return "cpu"
    if normalized.startswith("vulkan:") and normalized[len("vulkan:"):].isdigit():
        return normalized
    return "auto"



# 本地模型的提示词必须逐字对齐 LoRA 微调时的模板
# （D:/hy-mt-2-lora/v4/pipeline/prompt_v4.py:render_v4，explicit_source_language=True,
# include_stability_rule=True）。模型没见过的段落（例如后文原文）不要加。
STABILITY_RULE = (
    "When the source meaning has not changed, preserve the still-correct prefix "
    "of the previous translation whenever possible. When content is added or "
    "corrected, accuracy and completeness take priority."
)


def source_was_cut(previous_source: Optional[str], source: str) -> bool:
    """本地分行把一行切短（后半句分给了下一行）时，旧版本里装着下一行的内容。

    训练数据里的"上一版原文"都是同一句更早、更短的中间结果；拿更长的旧版本当参考，
    模型会按稳定性规则把下一行的译文留在这一行里。短到旧版本的 80% 以下就当新句子翻。
    """
    previous = (previous_source or "").strip()
    return bool(previous) and len(source.strip()) < 0.8 * len(previous)


def trained_history(pairs) -> List[List[str]]:
    """训练数据只收有译文的历史句，最多 TRAINED_HISTORY_PAIRS 对。"""
    kept = [
        [str(source).strip(), str(target).strip()]
        for source, target in pairs
        if str(source or "").strip() and str(target or "").strip()
    ]
    return kept[-TRAINED_HISTORY_PAIRS:]


def render_trained_prompt(
    *,
    source_name: str,
    target_name: str,
    history: List[List[str]],
    previous_source: str,
    previous_translation: str,
    current_source: str,
) -> str:
    if not history and not previous_source and not previous_translation:
        return (
            f"Translate the following text from {source_name} into {target_name}. Note that you "
            "should only output the translated result without any additional "
            f"explanation:\n\n{current_source}"
        )
    background: List[str] = []
    if history:
        background.append(
            "Recent utterances and their translations:\n"
            + "\n".join(f"{source} → {target}" for source, target in history)
        )
    if previous_source:
        background.append("Previous version of the current source:\n" + previous_source)
    if previous_translation:
        background.append("Previous translation of the current source:\n" + previous_translation)
    background.append(STABILITY_RULE)
    return (
        "[Background Information]\n" + "\n\n".join(background)
        + f"\n\nPlease translate the following text from {source_name} into {target_name}, "
        "taking the provided background information into consideration.\n\n[Source Text]\n"
        + current_source
    )


def clean_output(raw: str) -> str:
    # 只认全角竖线：这个词表没有 <|...|> 这种特殊 token，译文里的它是原文带来的普通文本。
    clean = re.split(r"<｜.*?｜>", raw)[0]
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.S)
    clean = re.split(r"<\|im_end\|>|<\|endoftext\|>", clean)[0].strip()
    for prefix in ("Translation:", "翻译：", "译文："):
        if clean.startswith(prefix):
            clean = clean[len(prefix) :].strip()
    return clean


def source_grew(previous_source: Optional[str], source: str) -> bool:
    previous = (previous_source or "").strip()
    return bool(previous) and len(source.strip()) > len(previous)


_NON_CONTENT = set("，。、！？；：,.!?;:…・「」『』（）()[]\"'“”‘’ \t\n-—~～")
# 按字计数的语言（没有空格分词）；其他语言按词计数
_CHAR_UNIT_LANGS = {"zh", "ja", "ko", "yue", "th", "lo", "my", "km", "bo"}


def text_content(text: Optional[str]) -> str:
    """去掉标点和空白，只留实际内容，用来比较两版译文是否"只差标点"。"""
    return "".join(ch for ch in (text or "") if ch not in _NON_CONTENT)


def added_content_units(previous_source: Optional[str], source: str, source_lang: str) -> int:
    """新增原文里有多少实际内容：中日韩等按字，其他语言按词，标点不算。"""
    previous = (previous_source or "").strip()
    current = source.strip()
    added = current[len(previous):] if current.startswith(previous) else current
    base = (source_lang or "").replace("_", "-").split("-")[0].lower()
    if base in _CHAR_UNIT_LANGS:
        return len(text_content(added))
    return sum(1 for word in added.split() if text_content(word))


class HyMT2LocalEngine:
    """本地 Hy-MT2 GGUF 推理引擎（基于 llama.cpp / LlamaModel）。"""

    def __init__(self, model_path: Optional[str] = None, device: str = "auto"):
        from realtime_subtitle.local_inference.gpu_devices import describe_device, resolve_device
        from realtime_subtitle.local_inference.model_manager import (
            get_hymt2_model_path,
            prepare_qwen_llama_runtime_env,
        )

        prepare_qwen_llama_runtime_env()
        self.is_index = index_translate_enabled() and not model_path
        path = model_path or (index_translate_model_path() if self.is_index else get_hymt2_model_path())
        if not path or not Path(path).is_file():
            raise FileNotFoundError(f"Hy-MT2 本地模型文件未找到: {path}")

        from realtime_subtitle.local_inference.vendor.qwen_asr_gguf.inference import llama as llama_mod
        from realtime_subtitle.local_inference.vendor.qwen_asr_gguf.inference.llama import (
            LlamaBatch,
            LlamaContext,
            LlamaModel,
            LlamaSampler,
        )

        self._llama_mod = llama_mod
        self._LlamaBatch = LlamaBatch

        # 解析运行位置：cpu -> 不卸载任何层；auto/vulkan:N -> 只用一张 GPU
        # （load_model 已固定 split_mode = NONE，不会把层切到核显上）
        n_gpu_layers, main_gpu, self.device = resolve_device(device)
        logger.info(
            "Hy-MT2 本地引擎运行位置: %s -> %s",
            self.device,
            describe_device(self.device),
        )

        self._LlamaSampler = LlamaSampler
        self.model = LlamaModel(str(path), n_gpu_layers=n_gpu_layers, main_gpu=main_gpu)
        self._n_ctx = 2048
        import realtime_subtitle.core.config as _config
        from realtime_subtitle.local_inference.spec_decode import MAX_DRAFT_CHUNK

        self.ctx = LlamaContext(
            self.model,
            n_ctx=self._n_ctx,
            type_kv=GGML_TYPE_Q8_0 if getattr(_config, "LOCAL_MT_KV_Q8", False) else None,
            # 只有草稿验证需要逐 token 的 logits，上限就是一段草稿的长度
            n_outputs_max=MAX_DRAFT_CHUNK + 1,
        )
        self.eos_token = self.model.token_eos()
        self.stop_ids = set()
        for s in (
            self.model.token_to_piece(self.eos_token),
            "<｜hy_place▁holder▁no▁2｜>",
            "<｜hy_User｜>",
            "<｜hy_Assistant｜>",
            "<|im_end|>",
            "<|endoftext|>",
            "</s>",
        ):
            tid = self._special_token_id(s)
            if tid >= 0:
                self.stop_ids.add(tid)
        self._engine_lock = threading.Lock()

        from realtime_subtitle.local_inference.spec_decode import SpeculativeDecoder

        self._spec = SpeculativeDecoder(llama_mod, self.ctx, self.model)
        # KV 里当前装着哪串 token（prompt + 已生成），供下一次比公共前缀
        self._cached_tokens: List[int] = []

    def _special_token_id(self, text: str) -> int:
        """词表里真正的特殊 token 才能当停止符，普通文本返回 -1。

        token_to_id 对普通文本会给出第一个片段（"</s>" -> "</"）；这个 GGUF 的 eos_token_id
        还指向普通 token "$"。它们一旦进停止符表，译文写到这里就被截断了。
        """
        ids = self.model.tokenize(text, add_special=False, parse_special=True)
        if len(ids) != 1 or ids == self.model.tokenize(text, add_special=False, parse_special=False):
            return -1
        return ids[0]

    def _prefill(self, tokens: List[int], cached: List[int]) -> int:
        """提交 prompt，返回本次真正送进模型的 token 数。

        逐 token 调用 llama_decode 时每个 token 都要走一遍完整的 kernel 提交/
        同步往返，实测 100+ token 的 prompt 比批量提交慢 30-40 倍；
        批量语义与逐个等价（位置编码相同，只对末位 token 取 logits）。

        流式修订里相邻两次 prompt 的开头（背景/历史那段）是一样的，KV 里已经算过；
        只重算从第一处不同开始的尾巴，把前面留着。
        """
        n = len(tokens)
        if n <= 0:
            return 0
        if n > self._n_ctx:
            raise ValueError(f"prompt 超过上下文长度: {n} > {self._n_ctx}")

        from realtime_subtitle.local_inference.spec_decode import common_prefix_len

        reuse = 0
        if self._prompt_cache_enabled() and cached:
            reuse = common_prefix_len(cached, tokens)
            # 末位必须重算：要拿它的 logits 起头
            reuse = min(reuse, n - 1)

        if getattr(self, "is_index", False):
            reuse = 0  # 递归状态不能从中间截断
        if reuse > 0:
            self._spec.seq_rm(reuse, -1)
        else:
            self.ctx.clear_kv_cache()

        todo = tokens[reuse:]
        self._spec.decode_tokens(todo, reuse, logits="last")
        return len(todo)

    @staticmethod
    def _prompt_cache_enabled() -> bool:
        import realtime_subtitle.core.config as _config

        return bool(getattr(_config, "LOCAL_MT_PROMPT_CACHE", True))

    def generate(
        self,
        prompt: str,
        max_tokens: int = 128,
        draft: Optional[List[int]] = None,
        repeat_penalty: float = 1.0,
        stop_on_runaway: bool = False,
        eos_penalty: float = 0.0,
        hold_text: Optional[str] = None,
        hold_tokens: Optional[List[int]] = None,
    ) -> tuple[str, List[int]]:
        """生成译文；返回 (清洗后的文本, 原始 token)。

        ``draft`` 是上一次中间结果的 token：流式修订里新旧译文大段重合，一次前向就能
        验证掉模型认同的部分。验证是精确比对，输出与逐 token 解码一致。

        ``stop_on_runaway``：末尾复读到 RUNAWAY_REPEATS 次就停（先按 token 粗筛，命中再按文本确认）。

        ``eos_penalty`` + ``hold_text``/``hold_tokens``：原文变长了，模型却刚好把上一版译文原样
        写完就要结束——多半是按稳定性规则照抄、漏了新增内容。这一步把停止符的 logit 压低
        ``eos_penalty``，让它接着写；模型确实更想停（差距超过惩罚）时照样停。
        """
        import realtime_subtitle.core.config as _config
        from realtime_subtitle.local_inference.repetition_guard import RUNAWAY_REPEATS, tail_repeats, text_units

        def runaway(toks: List[int]) -> bool:
            if not tail_repeats(toks, RUNAWAY_REPEATS):
                return False
            return bool(tail_repeats(text_units(self.model.detokenize(toks)), RUNAWAY_REPEATS))

        hold_ids = list(hold_tokens or [])
        hold_clean = (hold_text or "").strip()

        def stops_on_previous(toks: List[int]) -> bool:
            if hold_ids and toks == hold_ids:
                return True
            return bool(hold_clean) and clean_output(self.model.detokenize(toks)) == hold_clean

        use_eos_guard = eos_penalty > 0 and bool(hold_ids or hold_clean)
        # 被压住 EOS 的模型有时没东西可写，就把整段上一版译文再写一遍；这时退回上一版。
        probe = hold_clean[:REPEAT_PROBE_CHARS]
        held = {"pushed": False, "repeated": False}

        def guarded_stop(toks: List[int]) -> bool:
            hit = stops_on_previous(toks)
            if hit:
                held["pushed"] = True
            return hit

        def restarts_previous(toks: List[int]) -> bool:
            if not held["pushed"] or len(probe) < MIN_REPEAT_PROBE_CHARS:
                return False
            text = clean_output(self.model.detokenize(toks))
            if text.startswith(hold_clean) and probe in text[len(hold_clean):]:
                held["repeated"] = True
                return True
            return False

        def on_token(toks: List[int]) -> bool:
            if use_eos_guard and restarts_previous(toks):
                return True
            return bool(stop_on_runaway and runaway(toks))

        with self._engine_lock:
            tokens = self.model.tokenize(prompt, add_special=False, parse_special=True)
            # 中途出错时 KV 与 _cached_tokens 会对不上，先作废；成功后再写回真实内容
            cached, self._cached_tokens = self._cached_tokens, []
            n_new = self._prefill(tokens, cached)

            sampler = self._LlamaSampler(
                temperature=0.0, repeat_penalty=repeat_penalty, n_vocab=self.model.n_vocab
            )
            try:
                use_draft = bool(getattr(_config, "LOCAL_SPECULATIVE_DECODE", True)) and not getattr(self, "is_index", False)
                gen = self._spec.generate(
                    sampler,
                    len(tokens),
                    stop_ids=self.stop_ids,
                    # 上下文写满后 llama_decode 会失败，译文同样被截断；留在上下文之内。
                    max_tokens=min(max_tokens, self._n_ctx - len(tokens)),
                    draft=draft if use_draft else None,
                    on_token=on_token if (stop_on_runaway or use_eos_guard) else None,
                    stop_penalty=eos_penalty if use_eos_guard else 0.0,
                    avoid_stop=guarded_stop if use_eos_guard else None,
                )
            finally:
                del sampler

            # KV 现在正好是 prompt + 生成的 token，下一次可以拿它比前缀
            self._cached_tokens = list(tokens) + list(gen.tokens)

            logger.debug(
                "Hy-MT2 本地生成: %s token / %s 次前向 (草稿 %s/%s), prompt 复用 %s/%s",
                len(gen.tokens), gen.n_passes, gen.n_accepted, gen.n_drafted,
                len(tokens) - n_new, len(tokens),
            )
            if held["repeated"]:
                print("[Hy-MT2 本地] 压低 EOS 后开始重写上一版译文，退回上一版")
                kept = hold_ids if gen.tokens[: len(hold_ids)] == hold_ids else list(gen.tokens)
                return hold_clean, list(kept)
            if getattr(gen, "n_stop_vetoed", 0):
                print("[Hy-MT2 本地] 原文变长但译文停在上一版，压低 EOS 继续生成")

            return clean_output(self.model.detokenize(gen.tokens)), list(gen.tokens)


# ── 进程内共享本地引擎注册表（引用计数） ─────────────────────────────────
# 主/次翻译器等同一进程内的多个翻译器共享同一本地模型实例，避免重复加载
# 同一份 GGUF 占用数倍内存；引用计数归零时才真正释放模型与显存/内存。
# 加载与释放均线程安全（推理跑在 executor 线程，配置热重载/停止跑在事件循环线程）。

_local_engine_registry: Dict[tuple, Dict] = {}
_local_engine_registry_lock = threading.Lock()


def _local_engine_key(
    model_path: Optional[str], device: str
) -> tuple:
    """按 (模型文件, 运行位置) 归并共享实例：同一路径同一设备只加载一次。"""
    path = model_path
    if not path:
        try:
            from realtime_subtitle.local_inference.model_manager import get_hymt2_model_path

            path = index_translate_model_path() if index_translate_enabled() else get_hymt2_model_path()
        except Exception:  # pragma: no cover - 非本地构建
            path = None
    return (str(path) if path else "", _sanitize_local_device(device))


def _new_engine_entry() -> Dict:
    return {
        "engine": None,
        "refcount": 0,
        "loading": False,
        "load_error": None,
        "load_event": threading.Event(),
    }


def acquire_local_engine(
    model_path: Optional[str] = None, device: str = "auto"
) -> HyMT2LocalEngine:
    """获取一个本地引擎引用；尚无实例时负责加载（同参数并发调用只会加载一次）。

    调用成功即持有 1 个引用，必须配对的 :func:`release_local_engine`。
    等待中的调用方会按 key 重新检查最新状态（等待期间实例可能被释放后重建）。
    """
    key = _local_engine_key(model_path, device)
    while True:
        with _local_engine_registry_lock:
            entry = _local_engine_registry.get(key)
            if entry is None:
                entry = _new_engine_entry()
                _local_engine_registry[key] = entry
            if entry["engine"] is not None:
                entry["refcount"] += 1
                return entry["engine"]
            if entry["loading"]:
                # 有加载在进行：等它结束后回到循环顶部重新按 key 检查
                # （加载完成前实例可能被全部引用释放，需继续等新实例）
                event = entry["load_event"]
                wait_inflight = True
            else:
                event = None
                wait_inflight = False
                # 首个请求（或上次失败后的重试）：由本调用方负责加载
                entry["loading"] = True
                entry["load_error"] = None
                entry["load_event"].clear()
                entry["refcount"] += 1

        if wait_inflight:
            event.wait()
            continue

        try:
            engine = HyMT2LocalEngine(model_path, device=device)
        except Exception as exc:
            with _local_engine_registry_lock:
                entry["engine"] = None
                entry["load_error"] = exc
                entry["loading"] = False
                entry["refcount"] -= 1
                entry["load_event"].set()
            raise
        with _local_engine_registry_lock:
            entry["engine"] = engine
            entry["load_event"].set()
            entry["loading"] = False
            refcount = entry["refcount"]
        logger.info(
            "Hy-MT2 本地引擎已加载（进程内共享实例，引用计数 %d）: %s",
            refcount,
            key,
        )
        return engine


def release_local_engine(engine: Optional[HyMT2LocalEngine]) -> bool:
    """释放一个本地引擎引用；引用计数归零时真正卸载模型（释放内存/显存）。"""
    if engine is None:
        return False
    with _local_engine_registry_lock:
        disposed = False
        for key, entry in _local_engine_registry.items():
            if entry["engine"] is engine:
                entry["refcount"] -= 1
                if entry["refcount"] <= 0:
                    entry["refcount"] = 0
                    # 先从注册表摘除并置空，避免等待中的 acquire 拿到已卸载实例
                    entry["engine"] = None
                    del _local_engine_registry[key]
                    disposed = True
                break
        if not disposed:
            return False
    _dispose_local_engine(engine)
    logger.info("Hy-MT2 本地引擎已卸载（引用计数归零，模型内存已释放）")
    return True


def _dispose_local_engine(engine: HyMT2LocalEngine) -> None:
    """释放 llama.cpp 模型/上下文内存（通过包装对象的 __del__）。

    先等推理锁：若此刻有请求正在推理则等它结束，避免释放正在使用的模型。
    """
    engine_lock = getattr(engine, "_engine_lock", None)
    if engine_lock is not None:
        engine_lock.acquire()
    try:
        # 释放靠的是引用计数归零后包装对象的 __del__，所以每一个持有 ctx/model 的东西
        # 都得放手——推测解码器也持有它们，漏掉它显存就要留到进程退出才还。
        engine._spec = None
        engine._cached_tokens = []
        # 先释放上下文（llama_free），再释放模型（llama_model_free）
        engine.ctx = None
        engine.model = None
    finally:
        if engine_lock is not None:
            engine_lock.release()
    gc.collect()


def get_local_engine_runtime_status() -> Dict:
    """进程内本地引擎运行时状态快照（供 Web 前端展示加载/已加载）。"""
    with _local_engine_registry_lock:
        engines = [
            {
                "model_path": model_path or None,
                "device": device,
                "loaded": entry["engine"] is not None,
                "loading": bool(entry["loading"] and entry["engine"] is None),
                "refcount": int(entry["refcount"]),
            }
            for (model_path, device), entry in _local_engine_registry.items()
        ]
    return {
        "loaded": any(e["loaded"] for e in engines),
        "loading": any(e["loading"] for e in engines),
        "engines": engines,
    }


class HyMT2API(BaseTranslationAPI):
    """Hy-MT2 实时流式修订翻译（支持 WebSocket 外部服务与本地 GGUF 推理）。"""

    SUPPORTS_CONTEXT = True

    DEFAULT_SOURCE_LANG = "en"
    DEFAULT_TARGET_LANG = "zh"

    def __init__(
        self,
        websocket_url: str = "",
        timeout: float = 30.0,
        max_retries: int = 3,
        proxy_url: Optional[str] = None,
        backend: str = "api",
        model_path: Optional[str] = None,
        local_device: str = "auto",
    ):
        self.backend = (backend or "api").lower().strip()
        if self.backend == "local" and not _local_backend_available():
            logger.warning(
                "Hy-MT2 本地推理运行时不可用（标准版不含本地模型支持），已回退到 API 后端"
            )
            self.backend = "api"
        self.websocket_url = (websocket_url or "").strip()
        self.timeout = float(timeout or 30.0)
        self.max_retries = int(max_retries if max_retries is not None else 3)
        self._proxy_url = proxy_url
        self._model_path = model_path
        self.local_device = _sanitize_local_device(local_device)
        self._local_engine: Optional[HyMT2LocalEngine] = None

        self._lock = threading.Lock()
        self._ws = None
        self._boot_ms = time.time() * 1000
        self.reset_session()
        self._ws = None
        self._boot_ms = time.time() * 1000
        self.reset_session()

    # ── Session / chain state ─────────────────────────────────────────

    def reset_session(self) -> None:
        """重置进行中的句子修订链（识别会话开始 / 应用重配置时调用）。

        已完成句子的 ``history`` 保留（仍可作为上下文），仅清空当前句。
        """
        with self._lock:
            self._seq = 0
            self._utterance_id = -1  # 首次调用自增后从 0 开始
            self._prev_source: Optional[str] = None
            self._prev_translation: Optional[str] = None
            # 上一次中间译文的 token，作为下一次修订的推测解码草稿（仅 local 后端用）
            self._prev_tokens: List[int] = []
            # The local final-reuse shortcut is safe only when all prompt
            # background that can affect this row's translation is unchanged.
            self._prev_local_context_signature = None
            self._last_final = True
            self._sent_any = False
            self._last_source_lang: Optional[str] = None
            self._last_target_lang: Optional[str] = None
            self._utt_start_ms: Optional[float] = None
            self._history: List[List[str]] = []

    # ── Language helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_lang(code: Optional[str]) -> str:
        """Normalize aliases and regions without losing zh-Hant."""
        return normalize_hymt_language(code)

    def _resolve_source_lang(self, source_language: str, detected: Optional[str]) -> str:
        code = self._normalize_lang(detected) or self._normalize_lang(source_language)
        if not code:
            # 兜底：沿用本连接最近一次已知的具体语言码
            code = self._last_source_lang or self.DEFAULT_SOURCE_LANG
        return code

    # ── Local engine lifecycle ─────────────────────────────────────────

    def load_local_engine(self) -> bool:
        """立即加载（或复用）本地模型；非 local 后端或已加载时直接返回。

        服务启动 / 切换为本地后端时由宿主调用，避免首次请求才加载。
        """
        if self.backend != "local" or self._local_engine is not None:
            return self._local_engine is not None
        self._local_engine = acquire_local_engine(
            self._model_path, device=self.local_device,
        )
        return True

    def unload_local_engine(self) -> bool:
        """放弃本实例对本地模型的引用；最后一个引用释放时真正卸载模型。

        返回是否成功释放了本实例持有的引用（是否真正卸载取决于引用计数）。
        """
        engine, self._local_engine = self._local_engine, None
        if engine is None:
            return False
        try:
            release_local_engine(engine)
            return True
        except Exception as e:
            logger.warning("Hy-MT2 本地引擎释放失败: %s", e)
            return False

    def close(self) -> None:
        """释放本实例持有的全部资源（本地模型引用 + WebSocket 连接）。"""
        self.unload_local_engine()
        self._close_ws()

    # ── Connection management ──────────────────────────────────────────

    def _close_ws(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _recv_json(self, ws) -> Dict:
        message = ws.recv(timeout=self.timeout)
        if isinstance(message, (bytes, bytearray)):
            message = bytes(message).decode("utf-8")
        data = json.loads(message)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected non-object message: {data!r}")
        return data

    def _open_connection(self, source_lang: str, target_lang: str) -> None:
        """Open a WebSocket connection and complete the ``init`` handshake."""
        last_error: Optional[Exception] = None
        connect_kwargs: Dict = {"open_timeout": self.timeout}
        if self._proxy_url:
            connect_kwargs["proxy"] = self._proxy_url

        for attempt in range(self.max_retries + 1):
            ws = None
            try:
                ws = _sync_ws_connect(self.websocket_url, **connect_kwargs)
                init_payload = {
                    "type": "init",
                    "protocol_version": PROTOCOL_VERSION,
                    "source_lang": source_lang or self.DEFAULT_SOURCE_LANG,
                    "target_lang": target_lang or self.DEFAULT_TARGET_LANG,
                    "preset": "standard",
                    "hypothesis_mode": "sentence_revision",
                    "source_token_join_mode": "verbatim",
                }
                ws.send(json.dumps(init_payload, ensure_ascii=False))
                reply = self._recv_json(ws)
                if reply.get("type") != "init_ok":
                    raise RuntimeError(f"init failed: {reply}")
                if reply.get("hypothesis_mode") != "sentence_revision" or (
                    reply.get("source_token_join_mode") != "verbatim"
                ):
                    raise RuntimeError(
                        f"incompatible Hy-MT2 server (protocol check failed): {reply}"
                    )
                self._ws = ws
                if attempt:
                    logger.info(
                        "Hy-MT2 connection established after %d retries", attempt
                    )
                return
            except Exception as exc:  # noqa: BLE001 - surface as [ERROR]
                last_error = exc
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 8.0))

        raise RuntimeError(
            f"cannot connect to Hy-MT2 service at {self.websocket_url!r}: "
            f"{last_error}"
        )

    def _request(self, payload: Dict) -> Dict:
        with self._lock:
            ws = self._ws
            if ws is None:
                self._open_connection(
                    payload.get("source_lang") or "",
                    payload.get("target_lang") or "",
                )
                ws = self._ws
            try:
                ws.send(json.dumps(payload, ensure_ascii=False))
                reply = self._recv_json(ws)
            except Exception:
                # 传输层失败：断开，让下一次请求重连（无状态，重连不丢链）
                self._close_ws()
                raise
            if reply.get("type") == "error":
                raise RuntimeError(f"Hy-MT2 server error: {reply}")
            if reply.get("type") != "translation":
                raise RuntimeError(f"unexpected Hy-MT2 message: {reply}")
            return reply

    # ── History helpers ────────────────────────────────────────────────

    @staticmethod
    def _strip_speaker_prefix(source: str) -> str:
        for prefix in ("[Me] ", "[Others] "):
            if source.startswith(prefix):
                return source[len(prefix):]
        return source

    @classmethod
    def _history_from_context_pairs(
        cls,
        context_pairs: Optional[List[Dict[str, str]]],
    ) -> List[List[str]]:
        """把 ContextAwareTranslator 的完成句对转成协议 history 格式。"""
        if not context_pairs:
            return []
        pairs: List[List[str]] = []
        for pair in context_pairs:
            source = cls._strip_speaker_prefix(str(pair.get("source") or "")).strip()
            target = str(pair.get("target") or "").strip()
            if source:
                pairs.append([source, target])
        return pairs[-MAX_HISTORY:]

    # ── Translation entry point ────────────────────────────────────────

    @staticmethod
    def _local_context_signature(
        history: List[List[str]],
    ) -> tuple[tuple[str, str], ...]:
        """Return the local prompt inputs that must match for final reuse."""
        return tuple((str(source), str(target)) for source, target in history)

    def _try_reuse_final(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        local_context_signature=None,
    ) -> Optional[str]:
        """终句原文与上一次中间结果完全一致（且语言方向一致）时，跳过这次
        重复请求/推理，直接复用上一次的译文，并按终句方式收尾修订链
        （记入历史、清空当前句 prev、标记终译），等价于正常终句响应的效果。"""
        with self._lock:
            if self._last_final or self._prev_source is None or self._prev_source != text:
                return None
            if self._last_source_lang != source_lang or self._last_target_lang != target_lang:
                return None
            if (
                local_context_signature is not None
                and self._prev_local_context_signature != local_context_signature
            ):
                return None
            display = (self._prev_translation or "").strip()
            if not display or display.startswith("[ERROR]"):
                return None
            print("[Hy-MT2] 终句原文与上次中间结果一致，复用上次译文（省一次调用）")
            if text.strip():
                self._history.append([text, display])
                self._history = self._history[-HISTORY_KEEP:]
            self._prev_source = None
            self._prev_translation = None
            self._prev_tokens = []
            self._prev_local_context_signature = None
            self._last_final = True
            return display

    def translate(
        self,
        text: str,
        source_language: str = "auto",
        target_language: str = "zh-CN",
        context: Optional[str] = None,
        context_pairs: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> str:
        if not text or not text.strip():
            return ""

        is_partial = bool(kwargs.get("is_partial", False))
        detected_source = kwargs.get("detected_source_language")
        source_lang = self._resolve_source_lang(source_language, detected_source)
        target_lang = self._normalize_lang(target_language) or (
            self._last_target_lang or self.DEFAULT_TARGET_LANG
        )
        history = self._history_from_context_pairs(context_pairs)
        local_context_signature = None
        if self.backend in {"local", "remote"}:
            local_context_signature = self._local_context_signature(history)

        if not is_partial:
            reused = self._try_reuse_final(
                text,
                source_lang,
                target_lang,
                local_context_signature=local_context_signature,
            )
            if reused is not None:
                return reused

        if self.backend == "local":
            try:
                if self._local_engine is None:
                    # 兜底：宿主未预载时首次请求仍可用（走共享注册表）
                    self._local_engine = acquire_local_engine(
                        self._model_path, device=self.local_device,
                    )
                return self._translate_local(
                    text=text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    is_partial=is_partial,
                    history=history,
                    context_signature=local_context_signature,
                )
            except Exception as e:
                logger.warning("Hy-MT2 local translate failed: %s", e)
                return f"[ERROR] Hy-MT2 本地模型错误: {e}"

        if not self.websocket_url:
            return (
                "[ERROR] Hy-MT2: WebSocket 地址未配置，请在网页「翻译API设置」"
                "中填写 Hy-MT2 WebSocket 地址（或设置 HYMT2_WEBSOCKET_URL）。"
            )

        try:
            payload, previous_translation = self._build_update(
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                is_partial=is_partial,
                history=history,
            )
            reply = self._request(payload)

            committed = str(reply.get("committed_text") or "")
            buffer_text = str(reply.get("buffer_text") or "")
            display = (committed + buffer_text).strip()
            if not display:
                # stop_reason="wait"：无新内容，保持上一版显示（不返回空串）
                display = previous_translation or ""

            self._record_response(
                text=text,
                display=display,
                is_partial=is_partial,
                source_lang=source_lang,
                target_lang=target_lang,
            )
            if self.backend == "remote":
                self._prev_local_context_signature = local_context_signature
            return display

        except Exception as e:
            logger.warning("Hy-MT2 translate failed: %s", e)
            return f"[ERROR] Hy-MT2: {e}"

    def _translate_local(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        is_partial: bool,
        history: List[List[str]],
        context_signature,
    ) -> str:
        with self._lock:
            direction_changed = (
                self._last_source_lang is not None and self._last_source_lang != source_lang
            ) or (
                self._last_target_lang is not None and self._last_target_lang != target_lang
            )
            if direction_changed:
                self._history = []
            if self._last_final or direction_changed:
                self._prev_source = None
                self._prev_translation = None
                self._prev_tokens = []
                self._prev_local_context_signature = None

            if source_was_cut(self._prev_source, text):
                self._prev_source = None
                self._prev_translation = None
                self._prev_tokens = []
            prev_src = self._prev_source
            prev_trans = self._prev_translation
            recent_history = trained_history(self._history + history)

            source_name = LANGUAGE_NAMES.get(source_lang, source_lang)
            target_name = LANGUAGE_NAMES.get(target_lang, target_lang)

            content = render_trained_prompt(
                source_name=source_name,
                target_name=target_name,
                history=recent_history,
                previous_source=prev_src or "",
                previous_translation=prev_trans or "",
                current_source=text,
            )

            prompt = f"<｜hy_begin▁of▁sentence｜><｜hy_User｜>{content}<｜hy_Assistant｜>"
            is_index = bool(getattr(self._local_engine, "is_index", False))
            if is_index:
                # INDEX_FINAL_FRESH=1：终稿不给上一版，只带历史重翻（漏译最少，但终稿会整句改写）
                drop_prev = not is_partial and index_final_fresh()
                prompt = render_index_prompt(
                    target_lang, text, source_lang=source_lang, history=recent_history,
                    previous_source="" if drop_prev else (prev_src or ""),
                    previous_translation="" if drop_prev else (prev_trans or ""),
                )
            if self._local_engine is None:
                display = ""
            else:
                inference_started = time.monotonic()
                guard = not source_is_repetitive(text)
                max_tokens = max_translation_tokens(text)
                eos_kwargs = {} if is_index and not index_prompt_context_enabled() else self._eos_guard_kwargs(prev_src, prev_trans, text)
                display, out_tokens = self._local_engine.generate(
                    prompt,
                    max_tokens=max_tokens,
                    draft=self._prev_tokens or None,
                    stop_on_runaway=guard,
                    **eos_kwargs,
                )
                if guard and is_runaway(display):
                    # 复读跑飞：加一点重复惩罚重跑一次；还复读就照用，不再重试。
                    print("[Hy-MT2 本地] 译文末尾复读，加重复惩罚重跑一次")
                    display, out_tokens = self._local_engine.generate(
                        prompt,
                        max_tokens=max_tokens,
                        repeat_penalty=RETRY_REPEAT_PENALTY,
                        stop_on_runaway=True,
                        **eos_kwargs,
                    )
                if self._needs_fresh_retry(eos_kwargs, display, prev_src, prev_trans, text, source_lang):
                    # 压低 EOS 也没让它往下写：模型被上一版译文牵着走了。去掉上一版原文/译文
                    # （保留历史）重翻一次，确实写出更多内容才用。
                    fresh_prompt = (
                        "<｜hy_begin▁of▁sentence｜><｜hy_User｜>"
                        + render_trained_prompt(
                            source_name=source_name,
                            target_name=target_name,
                            history=recent_history,
                            previous_source="",
                            previous_translation="",
                            current_source=text,
                        )
                        + "<｜hy_Assistant｜>"
                    )
                    fresh, fresh_tokens = self._local_engine.generate(
                        fresh_prompt, max_tokens=max_tokens, stop_on_runaway=guard
                    )
                    if not (guard and is_runaway(fresh)) and len(text_content(fresh)) > len(
                        text_content(display)
                    ) * FRESH_MIN_GROWTH:
                        print("[Hy-MT2 本地] 原文变长但译文照抄上一版，去掉上一版重翻")
                        display, out_tokens = fresh, fresh_tokens
                # 同一句的下一次修订拿这次的结果当草稿；整句定稿后这一句就翻篇了。
                if is_index:
                    display = index_clean_output(display)
                self._prev_tokens = [] if not is_partial else out_tokens
                # 临时：本地模型每次推理后输出一行日志，记录本次耗时（毫秒）
                print(
                    f"[Hy-MT2 本地] 推理完成（{'最终' if not is_partial else '中间'}）: "
                    f"耗时 {(time.monotonic() - inference_started) * 1000.0:.0f}ms"
                )

            self._prev_source = text
            self._prev_translation = display
            self._prev_local_context_signature = context_signature
            self._last_final = not is_partial
            self._last_source_lang = source_lang
            self._last_target_lang = target_lang
            if not is_partial and text and display:
                self._history.append([text, display])
                self._history = self._history[-HISTORY_KEEP:]
            return display

    @staticmethod
    def _needs_fresh_retry(eos_kwargs: Dict, display: str, prev_src, prev_trans, text: str, source_lang: str) -> bool:
        import realtime_subtitle.core.config as _config

        if not eos_kwargs or not getattr(_config, "LOCAL_MT_FRESH_RETRY", False):
            return False
        if text_content(display) != text_content(prev_trans):
            return False
        min_add = max(1, int(getattr(_config, "LOCAL_MT_FRESH_MIN_ADD", 2)))
        return added_content_units(prev_src, text, source_lang) >= min_add

    def _eos_guard_kwargs(self, prev_src: Optional[str], prev_trans: Optional[str], text: str) -> Dict:
        """原文变长时，不许模型把上一版译文原样抄完就停。"""
        if not prev_trans or not source_grew(prev_src, text):
            return {}
        return {
            "eos_penalty": self._eos_penalty(),
            "hold_text": prev_trans,
            "hold_tokens": list(self._prev_tokens),
        }

    @staticmethod
    def _eos_penalty() -> float:
        import realtime_subtitle.core.config as _config

        return max(0.0, float(getattr(_config, "LOCAL_MT_EOS_PENALTY", 0.0)))

    def _build_update(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        is_partial: bool,
        history: List[List[str]],
    ) -> tuple[Dict, Optional[str]]:
        with self._lock:
            direction_changed = (
                (
                    self._last_source_lang is not None
                    and source_lang != self._last_source_lang
                )
                or (
                    self._last_target_lang is not None
                    and target_lang != self._last_target_lang
                )
            )
            new_utterance = (not self._sent_any) or self._last_final or direction_changed
            if direction_changed:
                self._history = []
            if not new_utterance and self.backend == "remote" and source_was_cut(self._prev_source, text):
                self._prev_source = None
                self._prev_translation = None
                self._prev_tokens = []
            if new_utterance:
                self._utterance_id += 1
                self._prev_source = None
                self._prev_translation = None
                self._prev_tokens = []
                self._utt_start_ms = time.time() * 1000

            self._seq += 1
            self._sent_any = True
            previous_source = self._prev_source
            previous_translation = self._prev_translation
            local_history = self._history[-MAX_HISTORY:]

            self._last_source_lang = source_lang
            self._last_target_lang = target_lang
            self._last_final = not is_partial

            payload: Dict = {
                "type": "update",
                "seq": self._seq,
                "utterance_id": self._utterance_id,
                "words": [[text, None, None]],
                "clock_ms": int(
                    time.time() * 1000 - (self._utt_start_ms or self._boot_ms)
                ),
                "is_final": not is_partial,
                "source": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
            if previous_source is not None:
                payload["previous_source"] = previous_source
            if previous_translation is not None:
                payload["previous_translation"] = previous_translation
            history_send = history or local_history
            if history_send:
                payload["history"] = history_send
        return payload, previous_translation

    def _record_response(
        self,
        *,
        text: str,
        display: str,
        is_partial: bool,
        source_lang: str,
        target_lang: str,
    ) -> None:
        with self._lock:
            if not is_partial:
                # 句子已完成：记入历史，清空修订链（下一条 update 自动开新句）
                if text.strip() and display.strip():
                    self._history.append([text, display])
                    if len(self._history) > HISTORY_KEEP:
                        self._history = self._history[-HISTORY_KEEP:]
                self._prev_source = None
                self._prev_translation = None
                self._prev_tokens = []
            else:
                self._prev_source = text
                self._prev_translation = display

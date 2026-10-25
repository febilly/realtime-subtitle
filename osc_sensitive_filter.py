"""HMAC-backed sensitive phrase filtering for outbound OSC text only."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Mapping


logger = logging.getLogger(__name__)

# Keep this value shared by the runtime and scripts/build_osc_sensitive_word_hashes.py.
# Raising it later only requires regenerating the hashed word-list file.
OSC_SENSITIVE_FILTER_MAX_WORDS = 4

# This key is intentionally stored in source control.  The HMAC representation keeps
# the original list out of the repository; it is not intended to make a public build's
# word list cryptographically undiscoverable.
OSC_SENSITIVE_FILTER_HMAC_KEY = bytes.fromhex(
    "ced825177b76a0eb872a4d31417341e90a26804b118c9ad7661439b372b764ef"
)

HASH_FILE_FORMAT = "osc-sensitive-phrases-v1"
HASH_FILE_RELATIVE_PATH = os.path.join("resources", "osc_sensitive_words.json")
_WORD_PATTERN = r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*"
_WORD_RE = re.compile(rf"(?<![A-Za-z0-9_]){_WORD_PATTERN}(?![A-Za-z0-9_])")
_PLAINTEXT_PHRASE_RE = re.compile(
    rf"{_WORD_PATTERN}(?:\s+{_WORD_PATTERN}){{0,{OSC_SENSITIVE_FILTER_MAX_WORDS - 1}}}"
)


def extract_normalized_words(text: str) -> list[str]:
    """Extract English words using the exact normalization used for HMAC input."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return [match.group(0).casefold() for match in _WORD_RE.finditer(normalized)]


def normalize_plaintext_phrase(text: str) -> list[str] | None:
    """Validate one private-list line and return its normalized 1..N words."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not normalized or _PLAINTEXT_PHRASE_RE.fullmatch(normalized) is None:
        return None
    return [match.group(0).casefold() for match in _WORD_RE.finditer(normalized)]


def phrase_digest(words: Iterable[str]) -> str:
    """Return the stable HMAC-SHA256 hex digest for a normalized word sequence."""
    normalized = [unicodedata.normalize("NFKC", str(word)).casefold() for word in words]
    payload = "\x1f".join(normalized).encode("utf-8")
    return hmac.new(OSC_SENSITIVE_FILTER_HMAC_KEY, payload, hashlib.sha256).hexdigest()


def default_hash_file_path() -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir / HASH_FILE_RELATIVE_PATH


def load_hashed_phrases(path: os.PathLike[str] | str | None = None) -> dict[int, frozenset[str]]:
    """Load and validate a generated digest file without ever needing plaintext terms."""
    hash_path = Path(path) if path is not None else default_hash_file_path()
    empty = {length: frozenset() for length in range(1, OSC_SENSITIVE_FILTER_MAX_WORDS + 1)}
    if not hash_path.is_file():
        logger.warning("[OSC Filter] Hashed phrase file not found: %s", hash_path)
        return empty

    try:
        payload = json.loads(hash_path.read_text(encoding="utf-8"))
        if payload.get("format") != HASH_FILE_FORMAT:
            raise ValueError("unsupported format")
        if payload.get("algorithm") != "HMAC-SHA256":
            raise ValueError("unsupported algorithm")
        if int(payload.get("max_words", 0)) != OSC_SENSITIVE_FILTER_MAX_WORDS:
            raise ValueError("max_words does not match runtime constant")
        raw_digests = payload.get("digests")
        if not isinstance(raw_digests, dict):
            raise ValueError("digests must be an object")

        loaded: dict[int, frozenset[str]] = {}
        for length in range(1, OSC_SENSITIVE_FILTER_MAX_WORDS + 1):
            values = raw_digests.get(str(length), [])
            if not isinstance(values, list):
                raise ValueError(f"digests[{length}] must be an array")
            normalized_values = set()
            for value in values:
                digest = str(value).strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError(f"invalid digest in digests[{length}]")
                normalized_values.add(digest)
            loaded[length] = frozenset(normalized_values)
        return loaded
    except Exception as error:
        logger.error("[OSC Filter] Failed to load hashed phrase file %s: %s", hash_path, error)
        return empty


class OscSensitiveWordFilter:
    """Filter 1..N adjacent English-word phrases while preserving separators."""

    def __init__(
        self,
        hashes_by_length: Mapping[int, Iterable[str]] | None = None,
        *,
        hash_file: os.PathLike[str] | str | None = None,
    ) -> None:
        source = hashes_by_length if hashes_by_length is not None else load_hashed_phrases(hash_file)
        self._hashes = {
            length: frozenset(str(value).lower() for value in source.get(length, ()))
            for length in range(1, OSC_SENSITIVE_FILTER_MAX_WORDS + 1)
        }

    @property
    def digest_count(self) -> int:
        return sum(len(values) for values in self._hashes.values())

    def filter_text(self, text: str) -> str:
        raw_text = str(text or "")
        matches = list(_WORD_RE.finditer(raw_text))
        if not raw_text or not matches or not self.digest_count:
            return raw_text

        masked_ranges: list[tuple[int, int]] = []
        index = 0
        while index < len(matches):
            matched_length = 0
            available = min(OSC_SENSITIVE_FILTER_MAX_WORDS, len(matches) - index)
            for length in range(available, 0, -1):
                candidates = self._hashes.get(length)
                if not candidates:
                    continue
                if any(
                    "\n" in raw_text[matches[pos - 1].end():matches[pos].start()]
                    or "\r" in raw_text[matches[pos - 1].end():matches[pos].start()]
                    for pos in range(index + 1, index + length)
                ):
                    continue
                words = [matches[pos].group(0).casefold() for pos in range(index, index + length)]
                if phrase_digest(words) in candidates:
                    matched_length = length
                    masked_ranges.extend(
                        (matches[pos].start(), matches[pos].end())
                        for pos in range(index, index + length)
                    )
                    break
            index += matched_length or 1

        if not masked_ranges:
            return raw_text

        chars = list(raw_text)
        for start, end in masked_ranges:
            for position in range(start, min(end, len(chars))):
                if chars[position].isascii() and chars[position].isalpha():
                    chars[position] = "*"
        return "".join(chars)

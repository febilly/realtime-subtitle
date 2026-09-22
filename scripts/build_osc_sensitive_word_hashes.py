#!/usr/bin/env python3
"""Generate the repository-safe HMAC digest file from a private plaintext list."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from osc_sensitive_filter import (  # noqa: E402
    HASH_FILE_FORMAT,
    HASH_FILE_RELATIVE_PATH,
    OSC_SENSITIVE_FILTER_MAX_WORDS,
    normalize_plaintext_phrase,
    phrase_digest,
)


def build_digest_payload(input_path: Path) -> tuple[dict, int, int]:
    digests = {length: set() for length in range(1, OSC_SENSITIVE_FILTER_MAX_WORDS + 1)}
    accepted = 0
    duplicates = 0

    with input_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            phrase = raw_line.strip()
            if not phrase or phrase.startswith("#"):
                continue
            words = normalize_plaintext_phrase(phrase)
            if words is None:
                raise ValueError(
                    f"Line {line_number} must contain 1-{OSC_SENSITIVE_FILTER_MAX_WORDS} English words"
                )
            digest = phrase_digest(words)
            bucket = digests[len(words)]
            if digest in bucket:
                duplicates += 1
                continue
            bucket.add(digest)
            accepted += 1

    payload = {
        "format": HASH_FILE_FORMAT,
        "algorithm": "HMAC-SHA256",
        "max_words": OSC_SENSITIVE_FILTER_MAX_WORDS,
        "digests": {
            str(length): sorted(digests[length])
            for length in range(1, OSC_SENSITIVE_FILTER_MAX_WORDS + 1)
        },
    }
    return payload, accepted, duplicates


def write_payload(payload: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, output_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            f"Convert a private UTF-8 text file (one 1-{OSC_SENSITIVE_FILTER_MAX_WORDS} "
            "word English phrase per line) "
            "into the HMAC-SHA256 digest file used by OSC filtering."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Path to the private plaintext word list (prompted when omitted)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / HASH_FILE_RELATIVE_PATH,
        help=f"Output JSON path (default: {HASH_FILE_RELATIVE_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.input is None:
        try:
            entered_path = input("Private plaintext word-list path: ").strip().strip('"')
        except EOFError:
            entered_path = ""
        if not entered_path:
            print("No input path provided.", file=sys.stderr)
            return 2
        args.input = Path(entered_path)
    if not args.input.is_file():
        print(f"Input file does not exist: {args.input}", file=sys.stderr)
        return 2
    try:
        payload, accepted, duplicates = build_digest_payload(args.input)
        write_payload(payload, args.output)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Failed to build hashed phrase file: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {accepted} unique digests to {args.output} ({duplicates} duplicates skipped).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

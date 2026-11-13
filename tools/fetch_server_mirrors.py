"""Print the current relay mirror list as a SUBTITLE_SERVER_MIRRORS .env line.

Used when packaging the hosted build: the bundled list is what a fresh install
tries on its very first start (before it has ever reached the server). After
that the client only uses the list the server sends.

    python tools/fetch_server_mirrors.py https://subtitle-server.tako01.top
    python tools/fetch_server_mirrors.py https://subtitle-server.tako01.top --env-file path/to/.env

With --env-file the SUBTITLE_SERVER_MIRRORS line in that file is replaced (or
appended). The official site is tried first, then each mirror it already knows
(from the existing line), so packaging also works when the official site is
unreachable from the build machine. Exits non-zero if no list could be fetched.
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from server_route import normalize_mirrors, parse_bundled_mirrors  # noqa: E402

KEY = "SUBTITLE_SERVER_MIRRORS"


def read_existing(env_file: str) -> str:
    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith(KEY + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def fetch(bases: list[str]) -> list[dict]:
    errors = []
    for base in bases:
        try:
            response = requests.get(base.rstrip("/") + "/client/ping", timeout=15)
            response.raise_for_status()
            data = response.json()
            mirrors = normalize_mirrors(data.get("mirrors") if isinstance(data, dict) else None)
            if mirrors is not None:
                return mirrors
            errors.append(f"{base}: no mirror list in response")
        except Exception as exc:  # report and try the next route
            errors.append(f"{base}: {exc}")
    raise SystemExit("Could not fetch the mirror list:\n  " + "\n  ".join(errors))


def format_line(mirrors: list[dict]) -> str:
    return KEY + "=" + ",".join(f"{m['url']}|{m['priority']}" for m in mirrors)


def write_env(env_file: str, line: str) -> None:
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    replaced = False
    for index, existing in enumerate(lines):
        if existing.strip().startswith(KEY + "="):
            lines[index] = line
            replaced = True
    if not replaced:
        lines.append(line)
    with open(env_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("official", help="Official subtitle-server URL")
    parser.add_argument("--env-file", help=".env file to update in place")
    args = parser.parse_args()
    known = parse_bundled_mirrors(read_existing(args.env_file)) if args.env_file else []
    mirrors = fetch([args.official] + [m["url"] for m in known])
    if not mirrors:
        raise SystemExit("The server returned an empty mirror list; refusing to bundle it")
    line = format_line(mirrors)
    if args.env_file:
        write_env(args.env_file, line)
    print(line)


if __name__ == "__main__":
    main()

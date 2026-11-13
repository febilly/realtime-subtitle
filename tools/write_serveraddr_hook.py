"""Write the PyInstaller runtime hook that bakes the hosted server address in.

    python tools/write_serveraddr_hook.py <hook.py> <server_url> [<mirrors_file>]

<mirrors_file> is the output of tools/fetch_server_mirrors.py (a single
``SUBTITLE_SERVER_MIRRORS=...`` line). When it is missing or empty the hook
still sets SUBTITLE_SERVER_MIRRORS to "" so an ambient value cannot leak into a
hosted build. Values are written with repr(), so URLs containing & or | are safe.
"""
from __future__ import annotations

import pathlib
import sys

KEY = "SUBTITLE_SERVER_MIRRORS"


def read_mirrors(path: str | None) -> str:
    if not path:
        return ""
    file = pathlib.Path(path)
    if not file.is_file():
        return ""
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(KEY + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def render(server_url: str, mirrors: str) -> str:
    return (
        "import os\n"
        f"os.environ['SUBTITLE_SERVER_URL'] = {server_url!r}\n"
        f"os.environ[{KEY!r}] = {mirrors!r}\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    hook_path, server_url = argv[1], argv[2]
    mirrors = read_mirrors(argv[3] if len(argv) > 3 else None)
    pathlib.Path(hook_path).write_text(render(server_url, mirrors), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

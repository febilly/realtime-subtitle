"""Filesystem anchors shared by modules that load bundled files.

Modules live in subpackages of ``realtime_subtitle``, so ``__file__`` no longer
points at the repository root. Resolve bundled assets (static/, resources/,
libopus/) through these helpers instead.
"""

import os
import sys
from pathlib import Path

# Repository root in a source checkout (the folder holding server.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Where bundled data files live: PyInstaller's _MEIPASS when frozen,
    otherwise the repository root."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else PROJECT_ROOT


def bundle_path(*parts: str) -> str:
    return os.path.join(str(bundle_root()), *parts)

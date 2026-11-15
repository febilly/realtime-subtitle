"""Helpers for swapping modules under ``realtime_subtitle`` in tests.

Code imports submodules as ``import realtime_subtitle.core.config as config``.
That statement reads the ``config`` attribute of the parent package before it
looks in ``sys.modules``, so replacing only ``sys.modules[...]`` no longer
reaches code imported afterwards. These helpers keep both in step.
"""

import contextlib
import importlib
import sys
from unittest.mock import patch

_MISSING = object()


def _parent(name):
    parent_name, _, child = name.rpartition(".")
    if not parent_name:
        return None, child
    parent = sys.modules.get(parent_name)
    if parent is None:
        try:
            parent = importlib.import_module(parent_name)
        except ImportError:
            return None, child
    return parent, child


def set_module(name, module):
    """Install ``module`` as ``name``; return a callable that undoes it."""
    previous = sys.modules.get(name, _MISSING)
    parent, child = _parent(name)
    previous_attr = getattr(parent, child, _MISSING) if parent is not None else _MISSING
    if module is None:
        sys.modules.pop(name, None)
        if parent is not None and previous_attr is not _MISSING:
            delattr(parent, child)
    else:
        sys.modules[name] = module
        if parent is not None:
            setattr(parent, child, module)

    def restore():
        if previous is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        if parent is not None:
            if previous_attr is _MISSING:
                if hasattr(parent, child):
                    delattr(parent, child)
            else:
                setattr(parent, child, previous_attr)

    return restore


def pop_module(name, default=None):
    """``sys.modules.pop(name, default)`` that also unbinds the parent attribute."""
    previous = sys.modules.get(name, default)
    set_module(name, None)
    return previous


def monkeypatch_module(monkeypatch, name, module):
    """``monkeypatch.setitem(sys.modules, ...)`` that also rebinds the parent.

    ``module=None`` removes the entry, like ``monkeypatch.delitem``.
    """
    parent, child = _parent(name)
    if module is None:
        monkeypatch.delitem(sys.modules, name, raising=False)
        if parent is not None:
            monkeypatch.delattr(parent, child, raising=False)
        return
    monkeypatch.setitem(sys.modules, name, module)
    if parent is not None:
        monkeypatch.setattr(parent, child, module, raising=False)


@contextlib.contextmanager
def patch_modules(mapping):
    """``patch.dict(sys.modules, mapping)`` that also rebinds parent packages."""
    with patch.dict(sys.modules, mapping):
        restores = [
            set_module(name, module)
            for name, module in mapping.items()
            if name.startswith("realtime_subtitle.")
        ]
        try:
            yield
        finally:
            for restore in reversed(restores):
                restore()

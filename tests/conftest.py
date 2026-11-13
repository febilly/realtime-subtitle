import sys

import pytest


@pytest.fixture(autouse=True)
def _no_server_route_race(monkeypatch):
    """Tests that set SUBTITLE_SERVER_URL must not probe real servers at startup."""
    config = sys.modules.get("config")
    if config is not None and hasattr(config, "server_router"):
        monkeypatch.setattr(config, "server_router", lambda: None)
    yield

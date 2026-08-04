"""The balance endpoint's view of subscription quota.

/billing/summary reports a subscription's pools for every model the plan funds.
The client only ever runs one model at a time, so quota attached to a different
model must not reach the UI: it would both display credits this session cannot
spend and count towards whether the session can afford to start.
"""

import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def web_server_runtime():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        config = importlib.import_module("config")
        web_server = importlib.import_module("web_server")
        yield config, web_server.WebServer
    finally:
        sys.modules.pop("web_server", None)
        sys.modules.pop("config", None)
        if previous_web_server is not None:
            sys.modules["web_server"] = previous_web_server
        if previous_config is not None:
            sys.modules["config"] = previous_config


SUMMARY = {
    "prepaid_balance": 40,
    "apis": [
        {
            "name": "soniox",
            "provider": "soniox",
            "prepaid_balance": 40,
            "subscriptions": [
                {
                    "subscription_id": 1,
                    "plan_name": "Monthly",
                    "model_name": "stt-rt-v5",
                    "period": "daily",
                    "quota_credits": 600,
                    "used_credits": 0,
                    "remaining_credits": 600,
                    "expires_at": "2026-11-02T00:00:00.000Z",
                },
                {
                    "subscription_id": 1,
                    "plan_name": "Monthly",
                    "model_name": "stt-rt-v9",
                    "period": "daily",
                    "quota_credits": 900,
                    "used_credits": 0,
                    "remaining_credits": 900,
                    "expires_at": "2026-11-02T00:00:00.000Z",
                },
            ],
            "models": [
                {
                    "model_name": "stt-rt-v5",
                    "price_per_second": 0.5,
                    "free": {"pools": [{"period": "daily", "remaining": 10}]},
                }
            ],
        }
    ],
}


async def _balance_payload(config, WebServer, monkeypatch, summary):
    monkeypatch.setattr(config, "RELAY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "soniox", raising=False)
    server = WebServer(MagicMock(), MagicMock())
    server.provider_manager = SimpleNamespace(relay_token="ss_test")
    server._server_request = AsyncMock(return_value=(200, summary))
    request = SimpleNamespace(query={})

    response = await server.account_balance_handler(request)

    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_only_pools_that_can_pay_for_the_active_model_are_returned(
    monkeypatch, web_server_runtime
):
    config, WebServer = web_server_runtime

    payload = await _balance_payload(config, WebServer, monkeypatch, SUMMARY)

    assert payload["model"] == "stt-rt-v5"
    assert [pool["model_name"] for pool in payload["subscriptions"]] == ["stt-rt-v5"]
    assert payload["subscriptions"][0]["remaining_credits"] == 600
    assert payload["prepaid_balance"] == 40
    assert payload["price_per_second"] == 0.5


@pytest.mark.asyncio
async def test_a_pool_without_a_model_is_kept(monkeypatch, web_server_runtime):
    config, WebServer = web_server_runtime
    summary = json.loads(json.dumps(SUMMARY))
    summary["apis"][0]["subscriptions"][1].pop("model_name")

    payload = await _balance_payload(config, WebServer, monkeypatch, summary)

    # An unscoped pool funds whatever the API offers, so it cannot be dropped.
    assert len(payload["subscriptions"]) == 2


@pytest.mark.asyncio
async def test_no_subscription_yields_an_empty_list(monkeypatch, web_server_runtime):
    config, WebServer = web_server_runtime
    summary = json.loads(json.dumps(SUMMARY))
    summary["apis"][0]["subscriptions"] = []

    payload = await _balance_payload(config, WebServer, monkeypatch, summary)

    assert payload["subscriptions"] == []

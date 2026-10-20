"""Tests use fake Telegram + Supabase: no network and no production credentials.

Run after installing requirements plus pytest: python -m pytest -q
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Importing SupabaseDB reads env at module import, but tests never call a real API.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "unit-test-not-real")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from telegram.error import Forbidden

import gateway


def make_client(monkeypatch, recipients=(1001, 1002)):
    monkeypatch.setenv("GATEWAY_API_KEYS", "fall:unit-secret;flood:other-secret")
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(gateway.supabase_db, "list_alert_subscribers", AsyncMock(return_value=list(recipients)))
    monkeypatch.setattr(gateway.supabase_db, "claim_gateway_event", AsyncMock(return_value=True))
    monkeypatch.setattr(gateway.supabase_db, "finish_gateway_event", AsyncMock())
    monkeypatch.setattr(gateway.supabase_db, "set_alert_subscription", AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock())
    app = FastAPI()
    app.state.telegram_app = SimpleNamespace(bot=bot)
    app.include_router(gateway.router)
    return TestClient(app), bot


def test_auth_required(monkeypatch):
    client, bot = make_client(monkeypatch)
    with client:
        assert client.post("/gateway/v1/notify", json={"message": "hi"}).status_code == 401
        assert client.post("/gateway/v1/notify", json={"message": "hi"},
                           headers={"X-Gateway-Key": "wrong"}).status_code == 401
    bot.send_message.assert_not_awaited()


def test_broadcast_to_all_and_no_chat_id(monkeypatch):
    client, bot = make_client(monkeypatch, (10, 20, 30))
    with client:
        response = client.post(
            "/gateway/v1/notify", headers={"X-Gateway-Key": "other-secret"},
            json={"title": "Flood", "level": "warning", "message": "Rain!"},
        )
        invalid = client.post(
            "/gateway/v1/notify", headers={"X-Gateway-Key": "other-secret"},
            json={"message": "hi", "chat_id": 99},
        )
    assert response.status_code == 200
    assert response.json()["sent"] == 3
    assert response.json()["source"] == "flood"
    assert invalid.status_code == 422
    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [10, 20, 30]
    assert "Flood" in bot.send_message.await_args_list[0].kwargs["text"]


def test_same_event_id_not_sent_twice(monkeypatch):
    client, bot = make_client(monkeypatch, (10,))
    gateway.supabase_db.claim_gateway_event.side_effect = [True, False]
    payload = {"message": "fall", "event_id": "fall-42"}
    with client:
        first = client.post("/gateway/v1/notify", json=payload, headers={"X-Gateway-Key": "unit-secret"})
        duplicate = client.post("/gateway/v1/notify", json=payload, headers={"X-Gateway-Key": "unit-secret"})
    assert first.json()["sent"] == 1
    assert duplicate.json()["duplicate"] is True
    assert bot.send_message.await_count == 1
    gateway.supabase_db.finish_gateway_event.assert_awaited_once()


def test_fall_endpoint_and_blocked_subscribers(monkeypatch):
    client, bot = make_client(monkeypatch, (10, 20))
    bot.send_message.side_effect = [Forbidden("blocked"), None]
    with client:
        response = client.post(
            "/gateway/v1/fall", headers={"X-Gateway-Key": "unit-secret"},
            json={"camera": "Camera kitchen", "event_time": "2026-10-20 20:30"},
        )
    assert response.status_code == 200
    assert response.json()["sent"] == 1
    assert response.json()["blocked"] == 1
    gateway.supabase_db.set_alert_subscription.assert_awaited_once_with(10, False)
    assert "Camera kitchen" in bot.send_message.await_args_list[1].kwargs["text"]


def test_fail_closed_when_no_keys(monkeypatch):
    client, bot = make_client(monkeypatch)
    monkeypatch.delenv("GATEWAY_API_KEYS", raising=False)
    with client:
        response = client.post("/gateway/v1/notify", json={"message": "hi"},
                               headers={"X-Gateway-Key": "unit-secret"})
    assert response.status_code == 503
    bot.send_message.assert_not_awaited()


def test_subscriber_database_pagination(monkeypatch):
    """Supabase pagination returns every chat even if API limits rows per request."""
    import asyncio
    import httpx

    requests = []

    async def fake_request(method, table, *, params, **kwargs):
        requests.append((method, table, params))
        offset = int(params["offset"])
        rows = [{"telegram_id": idx} for idx in range(offset, min(offset + 500, 502))]
        return httpx.Response(200, json=rows, request=httpx.Request("GET", "https://example.test"))

    monkeypatch.setattr(gateway.supabase_db, "_request", fake_request)
    result = asyncio.run(gateway.supabase_db.list_alert_subscribers())
    assert result == list(range(502))
    assert [x[2]["offset"] for x in requests] == ["0", "500"]
    assert all(x[1] == "bot_alert_subscribers" for x in requests)


def test_subscriber_upsert(monkeypatch):
    import asyncio
    import httpx

    calls = []

    async def fake_request(method, table, **kwargs):
        calls.append((method, table, kwargs))
        return httpx.Response(201, request=httpx.Request("POST", "https://example.test"))

    monkeypatch.setattr(gateway.supabase_db, "_request", fake_request)
    asyncio.run(gateway.supabase_db.set_alert_subscription(123456, False))
    assert calls[0][0:2] == ("POST", "bot_alert_subscribers")
    assert calls[0][2]["json_body"] == {"telegram_id": 123456, "enabled": False}


def test_max_recipients_fails_without_sending(monkeypatch):
    client, bot = make_client(monkeypatch, range(5))
    monkeypatch.setenv("GATEWAY_MAX_RECIPIENTS", "4")
    with client:
        response = client.post("/gateway/v1/notify", json={"message": "hi"},
                               headers={"X-Gateway-Key": "unit-secret"})
    assert response.status_code == 503
    bot.send_message.assert_not_awaited()

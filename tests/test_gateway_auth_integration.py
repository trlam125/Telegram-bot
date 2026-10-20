"""Integration tests without Telegram credentials or external API calls."""
import asyncio
import logging
import os
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AuthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # gateway imports telegram.error & Supabase module; replace only during
        # import so the tests don't need Telegram or Supabase configuration.
        fake_telegram = types.ModuleType("telegram")
        fake_error = types.ModuleType("telegram.error")
        for name in ("Forbidden", "RetryAfter", "TelegramError"):
            setattr(fake_error, name, type(name, (Exception,), {}))
        fake_telegram.error = fake_error
        fake_db = types.ModuleType("supabase_db")
        fake_db.supabase_db = object()
        with patch.dict(sys.modules, {
            "telegram": fake_telegram,
            "telegram.error": fake_error,
            "supabase_db": fake_db,
        }):
            import gateway
            cls.gateway = gateway
        cls.app = FastAPI()
        cls.app.include_router(cls.gateway.router)
        cls.client = TestClient(cls.app)

    def test_401_response_does_not_expose_secret_and_logs_diagnostics(self):
        a = "a" * 43
        b = "b" * 43
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "fall_detection:" + a,
                                      "GATEWAY_API_KEY": ""}):
            with self.assertLogs("telegram-notification-gateway.auth", level=logging.WARNING) as logs:
                response = self.client.post("/gateway/v1/notify", json={"message": "Ping"}, headers={"X-Gateway-Key": b})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid gateway key"})
        self.assertIn("configured_sources_and_lengths=fall_detection:43", logs.output[0])
        self.assertIn("request_key_length=43", logs.output[0])
        self.assertNotIn(a, str(response.json()) + str(logs.output))
        self.assertNotIn(b, str(response.json()) + str(logs.output))

    def test_valid_key_preserves_api_contract(self):
        a = "c" * 43
        async def fake_broadcast(request, payload, source):
            return {"ok": True, "source": source, "sent": 0, "failed": 0}
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "fall_detection:" + a, "GATEWAY_API_KEY": ""}), \
                patch.object(self.gateway, "_broadcast", new=fake_broadcast):
            response = self.client.post("/gateway/v1/notify", json={"message": "Ping"}, headers={"X-Gateway-Key": a})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fall_detection")

    def test_not_configured_is_503(self):
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "", "GATEWAY_API_KEY": ""}):
            with self.assertLogs("telegram-notification-gateway.auth", level=logging.WARNING):
                response = self.client.post("/gateway/v1/notify", json={"message": "Ping"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Gateway credentials not configured"})


if __name__ == "__main__":
    unittest.main()

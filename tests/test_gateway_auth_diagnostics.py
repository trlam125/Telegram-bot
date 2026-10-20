"""Unit tests for safe diagnostics; no Telegram or Supabase credentials needed."""
import logging
import os
import unittest
from unittest.mock import patch

from gateway_auth_diagnostics import log_auth_rejection, log_config_error


class AuthDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.a_key = "a_very_private_test_secret_that_must_not_be_logged_12345"
        self.b_key = "different_secret_must_never_be_logged_67890"

    def capture(self, callback):
        with self.assertLogs("telegram-notification-gateway.auth", level=logging.WARNING) as log:
            callback()
        return "\n".join(log.output)

    def test_logs_lengths_and_names_without_token_values(self):
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "fall_detection:" + self.a_key,
                                      "GATEWAY_API_KEY": "", "VERCEL_ENV": "production"}):
            logs = self.capture(lambda: log_auth_rejection("key_mismatch", {"fall_detection": self.a_key}, self.b_key))
        self.assertIn("reason=key_mismatch", logs)
        self.assertIn("configured_sources_and_lengths=fall_detection:", logs)
        self.assertIn("request_key_length=", logs)
        self.assertIn("vercel_env=production", logs)
        self.assertNotIn(self.a_key, logs)
        self.assertNotIn(self.b_key, logs)
        self.assertNotIn("GATEWAY_API_KEYS=fall_detection:" + self.a_key, logs)

    def test_absence_of_key(self):
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "", "GATEWAY_API_KEY": ""}):
            logs = self.capture(lambda: log_auth_rejection("no_configured_keys", {}, None))
        self.assertIn("configured_sources_and_lengths=none", logs)
        self.assertIn("request_key_length=0", logs)

    def test_detects_value_assignment_without_exposing_it(self):
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": "GATEWAY_API_KEYS=fall_detection:" + self.a_key}):
            logs = self.capture(lambda: log_auth_rejection("key_mismatch", {"GATEWAY_API_KEYS=fall_detection": self.a_key}, self.b_key))
        self.assertIn("value_contains_variable_name=True", logs)
        self.assertNotIn(self.a_key, logs)

    def test_detects_extraneous_quotes_and_spaces(self):
        with patch.dict(os.environ, {"GATEWAY_API_KEYS": '"fall_detection:' + self.a_key + '"'}):
            logs = self.capture(lambda: log_auth_rejection("key_mismatch", {"fall_detection": self.a_key}, '  "bad"  '))
        self.assertIn("value_starts_with_quote=True", logs)
        self.assertIn("request_has_outer_spaces=True", logs)
        self.assertIn("request_starts_with_quote=False", logs)  # starts with space, not quote

    def test_invalid_entry_logs_no_value(self):
        logs = self.capture(lambda: log_config_error(entry_index=2, duplicate=True))
        self.assertIn("entry_index=2 duplicate=True", logs)
        self.assertNotIn(self.a_key, logs)

    def test_sanitizes_deployment_metadata(self):
        with patch.dict(os.environ, {"VERCEL_URL": "test-host\nINJECTED:token", "VERCEL_ENV": "prod\n"}):
            logs = self.capture(lambda: log_auth_rejection("key_mismatch", {}, "test"))
        self.assertNotIn("\nINJECTED", logs)
        self.assertIn("vercel_url=test-host_INJECTED_token", logs)


if __name__ == "__main__":
    unittest.main()

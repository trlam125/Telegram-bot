"""Safe diagnostics for Gateway API-key authentication failures.

Never log tokens, token fragments, Authorization headers, or token hashes. Only
log presence, lengths and public Vercel deployment metadata. This module does
not implement authentication and does not expose any diagnostic endpoint.
"""

import logging
import os
import re

logger = logging.getLogger("telegram-notification-gateway.auth")
DIAGNOSTIC_VERSION = "auth-diag-1"


def _safe_label(value: str, limit: int = 48) -> str:
    """Prevent formatting/log injection via labels sourced from environment."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return cleaned[:limit] or "unnamed"


def _safe_deployment_metadata() -> tuple[str, str, str]:
    env = _safe_label(os.getenv("VERCEL_ENV", "local"), 24)
    host = _safe_label(os.getenv("VERCEL_URL", "unknown"), 120)
    commit = _safe_label(os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown")[:12], 12)
    return env, host, commit


def log_config_error(*, entry_index: int, duplicate: bool) -> None:
    """Report which entry is malformed, never its raw contents."""
    logger.error(
        "GATEWAY_AUTH_DIAG version=%s reason=invalid_api_keys_entry "
        "entry_index=%d duplicate=%s (no secrets logged)",
        DIAGNOSTIC_VERSION, entry_index, duplicate,
    )


def log_auth_rejection(reason: str, keys: dict[str, str], supplied_key: str | None) -> None:
    """Emit a safe one-line diagnostic visible in Vercel Runtime Logs.

    This is called only on authentication failures. Even if an attacker causes
    repeated failures, the returned HTTP error remains unchanged and contains
    none of these internal details.
    """
    raw_multi = os.getenv("GATEWAY_API_KEYS", "")
    raw_legacy = os.getenv("GATEWAY_API_KEY", "")
    env, host, commit = _safe_deployment_metadata()
    # The project names are only internal labels, and are cleaned before logs.
    source_lengths = ",".join(
        f"{_safe_label(name)}:{len(secret)}" for name, secret in sorted(keys.items())
    ) or "none"
    input_length = len(supplied_key) if supplied_key is not None else 0
    input_has_space = bool(supplied_key and supplied_key != supplied_key.strip())
    input_has_quotes = bool(supplied_key and supplied_key[:1] in ('"', "'"))
    # Common mistake: pasting NAME=value in the Vercel value field.
    pasted_assignment = raw_multi.lstrip().startswith("GATEWAY_API_KEYS=")
    # Common mistake: copying double quotes from shell examples into Vercel.
    quoted_env_value = raw_multi.strip().startswith(('"', "'"))
    logger.warning(
        "GATEWAY_AUTH_DIAG version=%s reason=%s vercel_env=%s vercel_url=%s "
        "commit=%s multi_key_env_set=%s legacy_key_env_set=%s "
        "configured_sources_and_lengths=%s request_key_length=%d "
        "request_has_outer_spaces=%s request_starts_with_quote=%s "
        "value_contains_variable_name=%s value_starts_with_quote=%s "
        "(API keys not logged)",
        DIAGNOSTIC_VERSION, _safe_label(reason, 32), env, host, commit,
        bool(raw_multi.strip()), bool(raw_legacy.strip()), source_lengths,
        input_length, input_has_space, input_has_quotes,
        pasted_assignment, quoted_env_value,
    )

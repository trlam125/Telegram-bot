"""Authenticated, Telegram-token-free gateway for other repositories.

The upstream project only needs this gateway URL and its own API key.
Recipients and Telegram credentials are managed exclusively by this bot.
"""

import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from telegram.error import Forbidden, RetryAfter, TelegramError

from supabase_db import supabase_db
from gateway_auth_diagnostics import log_auth_rejection, log_config_error

logger = logging.getLogger("telegram-notification-gateway")
router = APIRouter(prefix="/gateway/v1", tags=["Notification gateway"])


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=3500)
    title: str | None = Field(default=None, max_length=120)
    level: Literal["info", "warning", "critical"] = "info"
    # Same source + event_id is delivered at most once (see README for limitations).
    event_id: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class FallNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera: str = Field(default="Camera 01", min_length=1, max_length=100)
    event_time: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=150)
    event_id: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


def _configured_keys() -> dict[str, str]:
    """Read project:secret entries separated by semicolons; support one legacy key."""
    keys: dict[str, str] = {}
    raw = os.getenv("GATEWAY_API_KEYS", "").strip()
    if raw:
        for entry_index, item in enumerate(raw.split(";"), start=1):
            if not item.strip():
                continue
            project, separator, secret = item.strip().partition(":")
            project, secret = project.strip(), secret.strip()
            if not separator or not project or not secret or project in keys:
                log_config_error(entry_index=entry_index, duplicate=bool(project and project in keys))
                return {}
            keys[project] = secret
    single_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if single_key and "default" not in keys:
        keys["default"] = single_key
    return keys


def _authenticated_source(key: str | None) -> str:
    keys = _configured_keys()
    if not keys:
        log_auth_rejection("no_configured_keys", keys, key)
        raise HTTPException(status_code=503, detail="Gateway credentials not configured")
    if not key:
        log_auth_rejection("missing_key", keys, key)
        raise HTTPException(status_code=401, detail="Invalid gateway key")

    # Compare against all keys without short circuiting to avoid timing leaks.
    matched = [project for project, secret in keys.items()
               if secrets.compare_digest(key, secret)]
    if not matched:
        log_auth_rejection("key_mismatch", keys, key)
        raise HTTPException(status_code=401, detail="Invalid gateway key")
    return matched[0]


def _format_notification(payload: Notification, source: str) -> str:
    prefix = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}[payload.level]
    title = payload.title.strip() if payload.title else "Thông báo hệ thống"
    return f"{prefix} {title}\n\n{payload.message.strip()}\n\nNguồn: {source}"


async def _broadcast(request: Request, payload: Notification, source: str) -> dict:
    bot = request.app.state.telegram_app.bot
    try:
        recipients = await supabase_db.list_alert_subscribers()
    except Exception as exc:
        logger.exception("Cannot load gateway recipients")
        raise HTTPException(status_code=503, detail="Subscriber database unavailable") from exc

    # Prevent requests silently dropping recipients when serverless limits are reached.
    max_recipients = int(os.getenv("GATEWAY_MAX_RECIPIENTS", "1000"))
    if len(recipients) > max_recipients:
        raise HTTPException(
            status_code=503,
            detail="Too many subscribers for synchronous dispatch; configure a job queue",
        )

    if payload.event_id:
        try:
            claimed = await supabase_db.claim_gateway_event(source, payload.event_id)
        except Exception as exc:
            logger.exception("Cannot claim gateway event")
            raise HTTPException(status_code=503, detail="Event database unavailable") from exc
        if not claimed:
            return {"ok": True, "duplicate": True, "source": source, "event_id": payload.event_id}

    message = _format_notification(payload, source)
    sent = 0
    failed = 0
    blocked = 0
    # Limit sustained throughput (Telegram ~30 messages/second per bot to different chats).
    # Sequential sends also avoid bursts when different projects notify at once.
    interval = 1.0 / max(1, min(20, int(os.getenv("GATEWAY_MESSAGES_PER_SECOND", "12"))))

    try:
        for chat_id in recipients:
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                sent += 1
            except Forbidden:
                blocked += 1
                try:
                    await supabase_db.set_alert_subscription(chat_id, False)
                except Exception:
                    logger.exception("Failed to disable blocked chat %s", chat_id)
            except RetryAfter as exc:
                # Telegram requests backoff. Retry once, with a bounded wait.
                retry_delay = exc.retry_after
                if hasattr(retry_delay, "total_seconds"):
                    retry_delay = retry_delay.total_seconds()
                wait = max(0.0, min(float(retry_delay), 30.0))
                await asyncio.sleep(wait)
                try:
                    await bot.send_message(chat_id=chat_id, text=message)
                    sent += 1
                except Forbidden:
                    blocked += 1
                    try:
                        await supabase_db.set_alert_subscription(chat_id, False)
                    except Exception:
                        logger.exception("Failed to disable blocked chat %s", chat_id)
                except Exception:
                    failed += 1
                    logger.exception("Telegram rate-limit retry failed for chat %s", chat_id)
            except Exception:
                failed += 1
                logger.exception("Telegram delivery failed for chat %s", chat_id)
            await asyncio.sleep(interval)
    finally:
        if payload.event_id:
            try:
                await supabase_db.finish_gateway_event(
                    source, payload.event_id, sent=sent,
                    failed=failed, blocked=blocked,
                )
            except Exception:
                logger.exception("Cannot save gateway event outcome")

    return {
        "ok": failed == 0,
        "duplicate": False,
        "source": source,
        "event_id": payload.event_id,
        "subscribers": len(recipients),
        "sent": sent,
        "failed": failed,
        "blocked": blocked,
    }


@router.post("/notify")
async def notify(
    request: Request,
    payload: Notification,
    x_gateway_key: str | None = Header(default=None),
) -> dict:
    source = _authenticated_source(x_gateway_key)
    return await _broadcast(request, payload, source)


@router.post("/fall")
async def fall_alert(
    request: Request,
    payload: FallNotification,
    x_gateway_key: str | None = Header(default=None),
) -> dict:
    source = _authenticated_source(x_gateway_key)
    event_time = payload.event_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"📷 Camera: {payload.camera}",
        f"🕒 Thời gian: {event_time}",
    ]
    if payload.location:
        lines.append(f"📍 Vị trí: {payload.location}")
    lines.append("Phát hiện dấu hiệu người bị ngã. Vui lòng kiểm tra ngay!")
    message = Notification(
        title="CẢNH BÁO PHÁT HIỆN NGÃ",
        message="\n".join(lines),
        level="critical",
        event_id=payload.event_id,
    )
    return await _broadcast(request, message, source)

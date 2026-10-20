"""Authenticated, Telegram-token-free gateway for other repositories.

The upstream project only needs this gateway URL and its own API key.
Recipients and Telegram credentials are managed exclusively by this bot.
"""

import asyncio
import base64
import binascii
import io
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal, Annotated, Awaitable, Callable

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from telegram.error import Forbidden, RetryAfter, TelegramError

from supabase_db import supabase_db
from gateway_auth_diagnostics import log_auth_rejection, log_config_error
from gateway_media import MAX_UPLOAD_BYTES, MEDIA_METHODS, send_media, validate_reference

logger = logging.getLogger("telegram-notification-gateway")
router = APIRouter(prefix="/gateway/v1", tags=["Notification gateway"])


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=3500)
    title: str | None = Field(default=None, max_length=120)
    level: Literal["info", "warning", "critical"] = "info"
    # Optional single JPEG snapshot. <= 2 MiB decoded, to stay below Vercel body limits.
    image_base64: str | None = Field(default=None, max_length=2_800_000)
    # Same source + event_id is delivered at most once (see README for limitations).
    event_id: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class FallNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera: str = Field(default="Camera 01", min_length=1, max_length=100)
    image_base64: str | None = Field(default=None, max_length=2_800_000)
    event_time: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=150)
    event_id: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )



MediaType = Literal["photo", "video", "document", "audio", "voice", "animation"]
Level = Literal["info", "warning", "critical"]


class MediaReference(BaseModel):
    """Send a Telegram-hosted file_id or a file at an allowed HTTPS host."""
    model_config = ConfigDict(extra="forbid")

    media_type: MediaType
    file_url: str | None = Field(default=None, max_length=2048)
    file_id: str | None = Field(default=None, max_length=512)
    message: str = Field(default="Tệp đính kèm", max_length=3500)
    title: str | None = Field(default=None, max_length=120)
    level: Level = "info"
    event_id: str | None = Field(default=None, min_length=1, max_length=128,
                                 pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


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


MAX_PHOTO_BYTES = 2_000_000


def _photo_from_payload(payload: Notification) -> bytes | None:
    """Validate before claiming event_id; do not let invalid photos poison dedup."""
    if payload.image_base64 is None:
        return None
    try:
        photo = base64.b64decode(payload.image_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "image_base64 must be valid base64 JPEG") from None
    if (not photo or len(photo) > MAX_PHOTO_BYTES
            or not photo.startswith(b"\xff\xd8\xff")
            or not photo.endswith(b"\xff\xd9")):
        raise HTTPException(422, "image_base64 must be JPEG up to 2 MB")
    return photo


async def _send_to_chat(bot, chat_id: int, message: str, photo_bytes: bytes | None):
    if photo_bytes is None:
        await bot.send_message(chat_id=chat_id, text=message)
    else:
        # Each recipient receives a fresh in-memory upload, not a public image URL.
        # Telegram photo captions are limited to 1024 characters.
        await bot.send_photo(
            chat_id=chat_id, photo=io.BytesIO(photo_bytes),
            caption=message[:1024],
        )


async def _broadcast_with_sender(
    request: Request, source: str, event_id: str | None,
    sender: Callable[[object, int], Awaitable[None]],
) -> dict:
    """One shared subscriber, throttling and dedup pipeline for all media types."""
    bot = request.app.state.telegram_app.bot
    try:
        recipients = await supabase_db.list_alert_subscribers()
    except Exception as exc:
        logger.exception("Cannot load gateway recipients")
        raise HTTPException(status_code=503, detail="Subscriber database unavailable") from exc

    max_recipients = int(os.getenv("GATEWAY_MAX_RECIPIENTS", "1000"))
    if len(recipients) > max_recipients:
        raise HTTPException(
            status_code=503,
            detail="Too many subscribers for synchronous dispatch; configure a job queue",
        )

    if event_id:
        try:
            claimed = await supabase_db.claim_gateway_event(source, event_id)
        except Exception as exc:
            logger.exception("Cannot claim gateway event")
            raise HTTPException(status_code=503, detail="Event database unavailable") from exc
        if not claimed:
            return {"ok": True, "duplicate": True, "source": source, "event_id": event_id}

    sent = failed = blocked = 0
    interval = 1.0 / max(1, min(20, int(os.getenv("GATEWAY_MESSAGES_PER_SECOND", "12"))))
    try:
        for chat_id in recipients:
            try:
                await sender(bot, chat_id)
                sent += 1
            except Forbidden:
                blocked += 1
                try:
                    await supabase_db.set_alert_subscription(chat_id, False)
                except Exception:
                    logger.exception("Failed to disable blocked chat %s", chat_id)
            except RetryAfter as exc:
                retry_delay = exc.retry_after
                if hasattr(retry_delay, "total_seconds"):
                    retry_delay = retry_delay.total_seconds()
                await asyncio.sleep(max(0.0, min(float(retry_delay), 30.0)))
                try:
                    await sender(bot, chat_id)
                    sent += 1
                except Forbidden:
                    blocked += 1
                    try:
                        await supabase_db.set_alert_subscription(chat_id, False)
                    except Exception:
                        logger.exception("Failed to disable blocked chat %s", chat_id)
                except Exception:
                    failed += 1
                    logger.exception("Telegram retry failed for chat %s", chat_id)
            except Exception:
                failed += 1
                logger.exception("Telegram delivery failed for chat %s", chat_id)
            await asyncio.sleep(interval)
    finally:
        if event_id:
            try:
                await supabase_db.finish_gateway_event(
                    source, event_id, sent=sent, failed=failed, blocked=blocked,
                )
            except Exception:
                logger.exception("Cannot save gateway event outcome")

    return {
        "ok": failed == 0, "duplicate": False, "source": source,
        "event_id": event_id, "subscribers": len(recipients),
        "sent": sent, "failed": failed, "blocked": blocked,
    }


async def _broadcast(request: Request, payload: Notification, source: str) -> dict:
    photo_bytes = _photo_from_payload(payload)  # Validate before event claim.
    message = _format_notification(payload, source)

    async def deliver(bot, chat_id):
        await _send_to_chat(bot, chat_id, message, photo_bytes)

    result = await _broadcast_with_sender(request, source, payload.event_id, deliver)
    if not result["duplicate"]:
        result["photo"] = photo_bytes is not None
    return result


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
        image_base64=payload.image_base64,
    )
    return await _broadcast(request, message, source)


async def _broadcast_media(request: Request, *, source: str, media_type: str,
                           data: bytes | str, message: str, title: str | None,
                           level: Level, event_id: str | None,
                           filename: str | None = None) -> dict:
    # All media use the same formatting, recipient list and event_id semantics.
    notification = Notification(message=message or "Tệp đính kèm", title=title, level=level)
    caption = _format_notification(notification, source)

    async def deliver(bot, chat_id):
        await send_media(bot, chat_id, media_type, data, caption, filename)

    result = await _broadcast_with_sender(request, source, event_id, deliver)
    if not result["duplicate"]:
        result["media_type"] = media_type
    return result


@router.post("/media")
async def media_reference(
    request: Request,
    payload: MediaReference,
    x_gateway_key: str | None = Header(default=None),
) -> dict:
    """Send a Telegram file_id or a public HTTPS media URL on an allowed host."""
    source = _authenticated_source(x_gateway_key)
    reference = validate_reference(file_url=payload.file_url, file_id=payload.file_id)
    return await _broadcast_media(
        request, source=source, media_type=payload.media_type, data=reference,
        message=payload.message, title=payload.title, level=payload.level,
        event_id=payload.event_id,
    )


@router.post("/media/upload")
async def media_upload(
    request: Request,
    media_type: Annotated[MediaType, Form()],
    file: Annotated[UploadFile, File()],
    message: Annotated[str, Form(max_length=3500)] = "Tệp đính kèm",
    title: Annotated[str | None, Form(max_length=120)] = None,
    level: Annotated[Level, Form()] = "info",
    event_id: Annotated[str | None, Form(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")] = None,
    x_gateway_key: str | None = Header(default=None),
) -> dict:
    """Upload one small file (Vercel HTTP body limit is 4.5 MB)."""
    source = _authenticated_source(x_gateway_key)
    try:
        # Bound memory use; never read the entire user upload unchecked.
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise HTTPException(422, "Uploaded file must not be empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File exceeds direct upload limit (3.5 MB); use /media with an HTTPS URL or file_id")
        return await _broadcast_media(
            request, source=source, media_type=media_type, data=data,
            message=message, title=title, level=level, event_id=event_id,
            filename=file.filename,
        )
    finally:
        await file.close()

# Staged uploads use the same credentials / broadcast methods as the existing API.
from gateway_staged import router as staged_router
router.include_router(staged_router)

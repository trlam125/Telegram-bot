"""Authenticated HTTP endpoints for notifications and media."""
import logging
from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from telegram_bot.api.gateway_service import (Notification, FallNotification, MediaReference,
    MediaType, Level, _authenticated_source, _broadcast, _broadcast_media)
from telegram_bot.api.gateway_media import MAX_UPLOAD_BYTES, validate_reference
from telegram_bot.api.staged import router as staged_router

logger = logging.getLogger("telegram-notification-gateway")
router = APIRouter(prefix="/gateway/v1", tags=["Notification gateway"])

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

router.include_router(staged_router)

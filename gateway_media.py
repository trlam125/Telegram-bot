"""Validated media helpers for the Telegram notification gateway.

This module supplies the functions imported by gateway.py and gateway_staged.py.
URLs are accepted only from explicitly configured HTTPS hostnames.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import PurePath
from urllib.parse import urlsplit

from fastapi import HTTPException

MAX_UPLOAD_BYTES = 3_500_000
MEDIA_METHODS = {
    "photo": ("send_photo", "photo"),
    "video": ("send_video", "video"),
    "document": ("send_document", "document"),
    "audio": ("send_audio", "audio"),
    "voice": ("send_voice", "voice"),
    "animation": ("send_animation", "animation"),
}
_DEFAULT_FILENAMES = {
    "photo": "photo.jpg",
    "video": "video.mp4",
    "document": "document.bin",
    "audio": "audio.mp3",
    "voice": "voice.ogg",
    "animation": "animation.gif",
}


def clean_filename(filename: str | None, media_type: str) -> str:
    """Normalize a user-provided filename to a safe, flat storage key."""
    if media_type not in MEDIA_METHODS:
        raise HTTPException(422, "Unsupported media_type")
    raw = str(filename or "").replace("\\", "/").split("/")[-1].strip()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("._")
    return safe[:150] if safe else _DEFAULT_FILENAMES[media_type]


def validate_reference(*, file_url: str | None, file_id: str | None) -> str:
    """Accept a Telegram file_id or a URL on an explicitly allowlisted HTTPS host."""
    if bool(file_url) == bool(file_id):
        raise HTTPException(422, "Provide exactly one of file_url or file_id")
    if file_id is not None:
        value = file_id.strip()
        if not value or any(c.isspace() for c in value) or len(value) > 512:
            raise HTTPException(422, "Invalid Telegram file_id")
        return value

    assert file_url is not None
    value = file_url.strip()
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        raise HTTPException(422, "Invalid HTTPS URL") from None
    if (parsed.scheme.lower() != "https" or not hostname or port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or not parsed.path or parsed.fragment):
        raise HTTPException(422, "Invalid HTTPS media URL")
    allowed = {
        h.strip().lower().rstrip(".")
        for h in os.getenv("GATEWAY_MEDIA_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    if hostname not in allowed:
        raise HTTPException(422, "Media URL hostname not allowlisted")
    return value


async def send_media(bot, chat_id: int, media_type: str,
                     data: bytes | str, caption: str,
                     filename: str | None = None):
    """Send media by Telegram file_id, HTTPS URL, or small direct upload."""
    if media_type not in MEDIA_METHODS:
        raise HTTPException(422, "Unsupported media_type")
    method, param = MEDIA_METHODS[media_type]
    if isinstance(data, bytes):
        if not data:
            raise HTTPException(422, "Media must not be empty")
        stream = io.BytesIO(data)
        stream.name = clean_filename(filename, media_type)
        media = stream
    elif isinstance(data, str):
        media = data
    else:
        raise HTTPException(422, "Unsupported media content")
    # Telegram captions are capped at 1024 characters. Each message keeps its
    # own stream, so broadcasting uploaded media does not reuse consumed streams.
    kwargs = {"chat_id": chat_id, param: media, "caption": caption[:1024]}
    return await getattr(bot, method)(**kwargs)

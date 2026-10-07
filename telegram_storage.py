import logging
import os
from typing import Any, Optional

from telegram import Update
from telegram.ext import ContextTypes

from supabase_db import supabase_db

logger = logging.getLogger("telegram-storage")

_storage_raw = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip()
if not _storage_raw:
    raise RuntimeError("Thiếu TELEGRAM_STORAGE_CHAT_ID trong biến môi trường")

try:
    TELEGRAM_STORAGE_CHAT_ID = int(_storage_raw)
except ValueError as exc:
    raise RuntimeError("TELEGRAM_STORAGE_CHAT_ID phải là số dạng -100...") from exc


async def archive_media_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    media: Any,
    file_type: str,
    file_name: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Optional[int]:
    """Copy a user's media message to the private storage channel and index it in Supabase."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return None

    try:
        copied = await context.bot.copy_message(
            chat_id=TELEGRAM_STORAGE_CHAT_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        await supabase_db.save_file(
            telegram_user_id=user.id,
            storage_chat_id=TELEGRAM_STORAGE_CHAT_ID,
            storage_message_id=copied.message_id,
            telegram_file_id=getattr(media, "file_id", None),
            telegram_file_unique_id=getattr(media, "file_unique_id", None),
            file_name=file_name,
            mime_type=mime_type,
            file_type=file_type,
            file_size=getattr(media, "file_size", None),
            source_chat_id=chat.id,
            source_message_id=message.message_id,
        )
        logger.info(
            "Archived %s from user=%s to storage_message_id=%s",
            file_type,
            user.id,
            copied.message_id,
        )
        return copied.message_id
    except Exception as exc:
        # Storage/database failure should not destroy the AI interaction.
        logger.exception("Không thể lưu media vào Telegram storage/Supabase: %s", exc)
        return None

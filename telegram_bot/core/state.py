"""Supabase-backed per-user state and chat history."""
import logging
from functools import wraps
from typing import Dict, List
from telegram import Update
from telegram.ext import ContextTypes
from telegram_bot.db import supabase_db
from telegram_bot.services.notifications import owner_telegram_id
from telegram_bot.core.config import MAX_HISTORY_MESSAGES
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

def with_supabase_state(callback):
    """Load context.user_data from Supabase before a handler and save it after.

    This replaces PicklePersistence and works across restarts/redeploys.
    """
    @wraps(callback)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            return await callback(update, context)

        # Enforce the blocklist before any handler can reply, run AI or archive media.
        # Database errors fail closed rather than silently allowing a banned user.
        if user.id != owner_telegram_id():
            try:
                if await supabase_db.is_user_blocked(user.id):
                    return
            except Exception:
                logger.exception("Could not verify access for Telegram user %s", user.id)
                return

        try:
            await supabase_db.upsert_user(user)
            state = await supabase_db.load_user_state(user.id)
            context.user_data.clear()
            context.user_data.update(state)
        except Exception as exc:
            logger.exception("Không thể tải state Supabase cho user %s: %s", user.id, exc)
            # Continue with the current in-memory state so a temporary DB error
            # does not completely disable the bot.

        try:
            return await callback(update, context)
        finally:
            try:
                await supabase_db.save_user_state(user.id, dict(context.user_data))
            except Exception as exc:
                logger.exception("Không thể lưu state Supabase cho user %s: %s", user.id, exc)

    return wrapped

def get_history(context: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, str]]:
    history = context.user_data.get("history")
    if not isinstance(history, list):
        history = []
        context.user_data["history"] = history
    return history

def trim_history(history: List[Dict[str, str]]) -> None:
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[:-MAX_HISTORY_MESSAGES]

def ensure_started(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("started"))

def add_history(
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    answer: str,
) -> None:
    history = get_history(context)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    trim_history(history)
    context.user_data["history"] = history

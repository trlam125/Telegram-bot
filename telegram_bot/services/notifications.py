"""Global broadcast controls shared by bot commands, gateway and weather cron.

Only unsolicited/broadcast messages are affected. Interactive bot replies remain
available to every user. The owner ID is configured only on the server.
"""

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger("telegram-broadcast-controls")


def owner_telegram_id() -> int | None:
    raw = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if not raw:
        return None
    try:
        owner_id = int(raw)
    except ValueError:
        logger.error("TELEGRAM_OWNER_ID must be a positive numeric Telegram user ID")
        return None
    if owner_id <= 0:
        logger.error("TELEGRAM_OWNER_ID must be a positive numeric Telegram user ID")
        return None
    return owner_id


def is_owner_private_chat(update: Any) -> bool:
    owner = owner_telegram_id()
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    return bool(
        owner is not None and user is not None and chat is not None
        and getattr(chat, "type", None) == "private"
        and getattr(user, "id", None) == owner
        and getattr(chat, "id", None) == owner
    )


def alert_recipients(subscribers: Iterable[int], public_enabled: bool) -> list[int]:
    """Owner gets every gateway broadcast, even after opting out with /stop."""
    selected = {int(chat_id) for chat_id in subscribers} if public_enabled else set()
    owner = owner_telegram_id()
    if owner is not None:
        selected.add(owner)
    return sorted(selected)


def weather_recipients(subscribers: Iterable[dict], public_enabled: bool,
                       owner_city: str | None = None) -> list[dict]:
    """Weather bulletin also always reaches owner, without duplicate deliveries."""
    selected = (
        {int(row["telegram_id"]): dict(row) for row in subscribers}
        if public_enabled else {}
    )
    owner = owner_telegram_id()
    if owner is not None:
        selected[owner] = {
            "telegram_id": owner,
            "weather_daily_city": owner_city,
        }
    return [selected[chat_id] for chat_id in sorted(selected)]

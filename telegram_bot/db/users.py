"""Telegram user metadata and per-user conversation state."""
from typing import Any, Dict
from telegram_bot.db.client import _utc_now, _json_safe_dict


class UserRepository:
    async def upsert_user(self, user: Any) -> None:
        if user is None:
            return
        now = _utc_now()
        payload = {
            "telegram_id": int(user.id),
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
            "language_code": getattr(user, "language_code", None),
            "last_active_at": now,
        }
        response = await self._request(
            "POST",
            "bot_users",
            params={"on_conflict": "telegram_id"},
            json_body=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()

    async def load_user_state(self, telegram_user_id: int) -> Dict[str, Any]:
        response = await self._request(
            "GET",
            "bot_user_state",
            params={
                "telegram_user_id": f"eq.{int(telegram_user_id)}",
                "select": "state",
                "limit": "1",
            },
        )
        response.raise_for_status()
        rows = response.json() or []
        if not rows:
            return {}
        state = rows[0].get("state") or {}
        return state if isinstance(state, dict) else {}

    async def save_user_state(self, telegram_user_id: int, state: Dict[str, Any]) -> None:
        payload = {
            "telegram_user_id": int(telegram_user_id),
            "state": _json_safe_dict(state),
            "updated_at": _utc_now(),
        }
        response = await self._request(
            "POST",
            "bot_user_state",
            params={"on_conflict": "telegram_user_id"},
            json_body=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()

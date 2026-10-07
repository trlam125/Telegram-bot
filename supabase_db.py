import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("telegram-supabase")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-compatible copy of Telegram user_data."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class SupabaseDB:
    """Small async client for the Supabase Data API.

    The bot only needs a few PostgREST operations, so using httpx keeps the
    runtime light and avoids blocking the asyncio event loop.
    """

    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.key = (
            os.getenv("SUPABASE_SECRET_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        if not self.url:
            raise RuntimeError("Thiếu SUPABASE_URL trong biến môi trường")
        if not self.key:
            raise RuntimeError(
                "Thiếu SUPABASE_SECRET_KEY (khuyên dùng) hoặc SUPABASE_SERVICE_ROLE_KEY"
            )

        self.rest_url = f"{self.url}/rest/v1"
        self.headers = {
            "apikey": self.key,
            "Content-Type": "application/json",
            "User-Agent": "telegram-ai-bot-backend/1.0",
        }
        self.timeout = httpx.Timeout(20.0, connect=10.0)

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: Optional[Dict[str, str]] = None,
        json_body: Any = None,
        prefer: Optional[str] = None,
    ) -> httpx.Response:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                f"{self.rest_url}/{table}",
                params=params,
                json=json_body,
                headers=headers,
            )
        return response

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

    async def save_file(
        self,
        *,
        telegram_user_id: int,
        storage_chat_id: int,
        storage_message_id: int,
        telegram_file_id: Optional[str],
        telegram_file_unique_id: Optional[str],
        file_name: Optional[str],
        mime_type: Optional[str],
        file_type: str,
        file_size: Optional[int],
        source_chat_id: Optional[int],
        source_message_id: Optional[int],
    ) -> None:
        payload = {
            "telegram_user_id": int(telegram_user_id),
            "storage_chat_id": int(storage_chat_id),
            "storage_message_id": int(storage_message_id),
            "telegram_file_id": telegram_file_id,
            "telegram_file_unique_id": telegram_file_unique_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "file_type": file_type,
            "file_size": int(file_size) if file_size is not None else None,
            "source_chat_id": int(source_chat_id) if source_chat_id is not None else None,
            "source_message_id": int(source_message_id) if source_message_id is not None else None,
        }
        response = await self._request(
            "POST",
            "bot_files",
            params={"on_conflict": "storage_chat_id,storage_message_id"},
            json_body=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()

    async def claim_update(self, update_id: int) -> bool:
        """Claim a Telegram webhook update to suppress retries/duplicates.

        Failed claims older than 15 minutes are allowed to run again.
        """
        response = await self._request(
            "GET",
            "bot_updates",
            params={
                "update_id": f"eq.{int(update_id)}",
                "select": "update_id,status,updated_at",
                "limit": "1",
            },
        )
        response.raise_for_status()
        rows = response.json() or []
        now = datetime.now(timezone.utc)

        if rows:
            row = rows[0]
            status = str(row.get("status") or "")
            updated_at = row.get("updated_at")
            stale = False
            if updated_at:
                try:
                    parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                    stale = now - parsed > timedelta(minutes=15)
                except ValueError:
                    pass
            if status == "done" or (status == "processing" and not stale):
                return False

            response = await self._request(
                "PATCH",
                "bot_updates",
                params={"update_id": f"eq.{int(update_id)}"},
                json_body={"status": "processing", "updated_at": now.isoformat()},
                prefer="return=minimal",
            )
            response.raise_for_status()
            return True

        response = await self._request(
            "POST",
            "bot_updates",
            json_body={
                "update_id": int(update_id),
                "status": "processing",
                "updated_at": now.isoformat(),
            },
            prefer="return=minimal",
        )
        if response.status_code == 409:
            return False
        response.raise_for_status()
        return True

    async def finish_update(self, update_id: int, status: str = "done") -> None:
        response = await self._request(
            "PATCH",
            "bot_updates",
            params={"update_id": f"eq.{int(update_id)}"},
            json_body={"status": status, "updated_at": _utc_now()},
            prefer="return=minimal",
        )
        response.raise_for_status()


supabase_db = SupabaseDB()

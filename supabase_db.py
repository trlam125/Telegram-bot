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


    async def set_alert_subscription(self, telegram_id: int, enabled: bool) -> None:
        """Register or opt out a private-chat user for ALL gateway notifications."""
        response = await self._request(
            "POST", "bot_alert_subscribers",
            params={"on_conflict": "telegram_id"},
            json_body={"telegram_id": int(telegram_id), "enabled": enabled},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()

    async def list_alert_subscribers(self) -> list[int]:
        """Page through all subscribers; PostgREST may cap result rows."""
        result: list[int] = []
        offset = 0
        page_size = 500
        while True:
            response = await self._request(
                "GET", "bot_alert_subscribers",
                params={
                    "select": "telegram_id",
                    "enabled": "eq.true",
                    "order": "telegram_id.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            response.raise_for_status()
            rows = response.json() or []
            result.extend(int(row["telegram_id"]) for row in rows)
            if len(rows) < page_size:
                return result
            offset += page_size

    async def claim_gateway_event(self, source: str, event_id: str) -> bool:
        """Atomic unique INSERT, used to avoid duplicate alerts across deployments."""
        response = await self._request(
            "POST", "bot_gateway_events",
            json_body={"source": source, "event_id": event_id},
            prefer="return=minimal",
        )
        if response.status_code == 409:  # duplicate composite primary key
            return False
        response.raise_for_status()
        return True

    async def finish_gateway_event(
        self, source: str, event_id: str, *, sent: int, failed: int, blocked: int
    ) -> None:
        response = await self._request(
            "PATCH", "bot_gateway_events",
            params={"source": f"eq.{source}", "event_id": f"eq.{event_id}"},
            json_body={
                "status": "done" if not failed else "partial",
                "sent": sent, "failed": failed, "blocked": blocked,
                "finished_at": _utc_now(),
            },
            prefer="return=minimal",
        )
        response.raise_for_status()


    async def create_gateway_upload(self, row: dict) -> None:
        response = await self._request('POST', 'bot_gateway_uploads', json_body=row,
                                       prefer='return=minimal')
        response.raise_for_status()
    
    
    async def get_gateway_upload(self, source: str, upload_id: str) -> dict | None:
        response = await self._request('GET', 'bot_gateway_uploads', params={
            'source': f'eq.{source}', 'id': f'eq.{upload_id}', 'select': '*', 'limit': '1',
        })
        response.raise_for_status()
        rows = response.json() or []
        return rows[0] if rows else None
    
    
    async def claim_gateway_upload(self, source: str, upload_id: str) -> dict | None:
        # CAS-style claim, competing Vercel invocations cannot both win.
        from datetime import datetime, timedelta, timezone
        stale = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        response = await self._request('PATCH', 'bot_gateway_uploads', params={
            'source': f'eq.{source}', 'id': f'eq.{upload_id}',
            'or': f'(status.in.(pending,failed),and(status.eq.processing,updated_at.lt.{stale}))',
            'select': '*',
        }, json_body={'status': 'processing', 'updated_at': now},
            prefer='return=representation')
        response.raise_for_status()
        rows = response.json() or []
        return rows[0] if rows else None
    
    
    async def update_gateway_upload(self, source: str, upload_id: str, patch: dict):
        patch = {**patch, 'updated_at': _utc_now()}
        response = await self._request('PATCH', 'bot_gateway_uploads', params={
            'source': f'eq.{source}', 'id': f'eq.{upload_id}'
        }, json_body=patch, prefer='return=minimal')
        response.raise_for_status()
    
    
    async def list_stale_gateway_uploads(self, limit: int = 30) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        response = await self._request('GET', 'bot_gateway_uploads', params={
            'temp_deleted': 'eq.false', 'created_at': f'lt.{cutoff}',
            'select': 'id,source,object_path,status', 'order': 'created_at.asc',
            'limit': str(min(30, limit)),
        })
        response.raise_for_status()
        return response.json() or []


supabase_db = SupabaseDB()


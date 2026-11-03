"""Async PostgREST HTTP transport and shared database helpers."""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_safe_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class SupabaseClient:
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

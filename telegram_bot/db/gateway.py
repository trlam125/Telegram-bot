"""Gateway event deduplication and staged-upload metadata."""
from datetime import datetime, timedelta, timezone
from telegram_bot.db.client import _utc_now


class GatewayRepository:
    async def release_daily_weather_claim(self, event_id: str) -> None:
        """Allow retry when Telegram delivery itself failed."""
        response = await self._request(
            "DELETE", "bot_gateway_events",
            params={"source": "eq.daily-weather", "event_id": f"eq.{event_id}"},
            prefer="return=minimal",
        )
        response.raise_for_status()

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

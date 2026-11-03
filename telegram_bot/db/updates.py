"""Webhook retry/idempotence state."""
from datetime import datetime, timedelta, timezone
from telegram_bot.db.client import _utc_now


class UpdateRepository:
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

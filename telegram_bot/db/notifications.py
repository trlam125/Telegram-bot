"""Subscription preferences, broadcast toggle and daily weather recipients."""
from telegram_bot.db.client import _utc_now


class NotificationRepository:
    async def get_broadcast_enabled(self) -> bool:
        """Read the shared switch from Supabase on every delivery request."""
        response = await self._request(
            "GET", "bot_broadcast_settings",
            params={"select": "enabled", "id": "eq.1", "limit": "1"},
        )
        response.raise_for_status()
        rows = response.json() or []
        if len(rows) != 1 or not isinstance(rows[0].get("enabled"), bool):
            raise RuntimeError("Missing bot_broadcast_settings row; run SQL migration")
        return rows[0]["enabled"]

    async def set_broadcast_enabled(self, enabled: bool) -> None:
        """Change one global switch, stored across Vercel cold starts/deploys."""
        response = await self._request(
            "PATCH", "bot_broadcast_settings",
            params={"id": "eq.1", "select": "id"},
            json_body={"enabled": bool(enabled), "updated_at": _utc_now()},
            prefer="return=representation",
        )
        response.raise_for_status()
        if len(response.json() or []) != 1:
            raise RuntimeError("Missing bot_broadcast_settings row; run SQL migration")

    async def get_daily_weather_city(self, telegram_id: int) -> str | None:
        """Owner can receive weather even when their own daily setting is off."""
        response = await self._request(
            "GET", "bot_users",
            params={
                "select": "weather_daily_city",
                "telegram_id": f"eq.{int(telegram_id)}", "limit": "1",
            },
        )
        response.raise_for_status()
        rows = response.json() or []
        return rows[0].get("weather_daily_city") if rows else None

    async def set_alert_subscription(self, telegram_id: int, enabled: bool) -> None:
        """Register/opt out an individual from gateway notifications."""
        response = await self._request(
            "POST", "bot_alert_subscribers",
            params={"on_conflict": "telegram_id"},
            json_body={"telegram_id": int(telegram_id), "enabled": bool(enabled)},
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

    async def set_daily_weather_subscription(self, telegram_id: int, enabled: bool) -> None:
        """Update only weather notifications, never emergency alert settings."""
        response = await self._request(
            "PATCH", "bot_users",
            params={"telegram_id": f"eq.{int(telegram_id)}"},
            json_body={"weather_daily_enabled": bool(enabled)},
            prefer="return=minimal",
        )
        response.raise_for_status()

    async def set_daily_weather_city(self, telegram_id: int, city: str) -> None:
        response = await self._request(
            "PATCH", "bot_users",
            params={"telegram_id": f"eq.{int(telegram_id)}"},
            json_body={"weather_daily_city": city[:180]},
            prefer="return=minimal",
        )
        response.raise_for_status()

    async def list_daily_weather_recipients(self) -> list[dict]:
        """Read all users who have not opted out of daily weather."""
        result: list[dict] = []
        offset, page_size = 0, 500
        while True:
            response = await self._request(
                "GET", "bot_users",
                params={
                    "select": "telegram_id,weather_daily_city",
                    "weather_daily_enabled": "eq.true",
                    "order": "telegram_id.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            response.raise_for_status()
            rows = response.json() or []
            result.extend(rows)
            if len(rows) < page_size:
                return result
            offset += page_size

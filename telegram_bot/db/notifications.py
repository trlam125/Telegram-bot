"""Persisted notification preferences, owner controls and blocklist."""
from telegram_bot.db.client import _utc_now


class NotificationRepository:
    async def get_broadcast_settings(self) -> tuple[bool, int]:
        """Read switch and reset generation together to avoid mixed snapshots."""
        response = await self._request(
            "GET", "bot_broadcast_settings",
            params={"select": "enabled,subscription_epoch", "id": "eq.1", "limit": "1"},
        )
        response.raise_for_status()
        rows = response.json() or []
        if (len(rows) != 1 or not isinstance(rows[0].get("enabled"), bool)
                or not isinstance(rows[0].get("subscription_epoch"), int)):
            raise RuntimeError("Missing broadcast settings or migration 002")
        return rows[0]["enabled"], rows[0]["subscription_epoch"]

    async def get_broadcast_enabled(self) -> bool:
        enabled, _ = await self.get_broadcast_settings()
        return enabled

    async def set_broadcast_enabled(self, enabled: bool) -> None:
        response = await self._request(
            "PATCH", "bot_broadcast_settings",
            params={"id": "eq.1", "select": "id"},
            json_body={"enabled": bool(enabled), "updated_at": _utc_now()},
            prefer="return=representation",
        )
        response.raise_for_status()
        if len(response.json() or []) != 1:
            raise RuntimeError("Missing bot_broadcast_settings row")

    async def reset_all_subscriptions(self) -> int:
        """Atomically invalidate earlier /start registrations, not user accounts."""
        response = await self._request(
            "POST", "rpc/bot_reset_all_subscriptions", json_body={},
        )
        response.raise_for_status()
        new_epoch = response.json()
        if not isinstance(new_epoch, int) or new_epoch < 1:
            raise RuntimeError("Invalid reset result; check migration 002")
        return new_epoch

    async def activate_subscription(self, telegram_id: int) -> bool:
        """Atomically register /start only when this user is not blocked."""
        response = await self._request(
            "POST", "rpc/bot_activate_subscription",
            json_body={"p_telegram_id": int(telegram_id)},
        )
        response.raise_for_status()
        if not isinstance(response.json(), bool):
            raise RuntimeError("Invalid subscription activation result")
        return response.json()

    async def is_known_user(self, telegram_id: int) -> bool:
        """A target must be an actual bot user; never create a phantom account."""
        response = await self._request(
            "GET", "bot_users",
            params={"select": "telegram_id", "telegram_id": f"eq.{int(telegram_id)}", "limit": "1"},
        )
        response.raise_for_status()
        return bool(response.json())

    async def set_user_blocked(self, telegram_id: int, blocked: bool) -> bool:
        """Change a known user's block state, revoking notification consent.

        False means no matching user (or no active block when unblocking).
        Migration 003 performs the same checks atomically in the database.
        """
        telegram_id = int(telegram_id)
        if not await self.is_known_user(telegram_id):
            return False
        if not blocked and not await self.is_user_blocked(telegram_id):
            return False
        response = await self._request(
            "POST", "rpc/bot_set_user_blocked",
            json_body={"p_telegram_id": telegram_id, "p_blocked": bool(blocked)},
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, bool):
            raise RuntimeError("Invalid block-state response; check migration 003")
        return result

    async def is_user_blocked(self, telegram_id: int) -> bool:
        response = await self._request(
            "GET", "bot_notification_access",
            params={"select": "blocked", "telegram_id": f"eq.{int(telegram_id)}", "limit": "1"},
        )
        response.raise_for_status()
        rows = response.json() or []
        return bool(rows[0]["blocked"]) if rows else False

    async def list_blocked_users(self) -> list[dict]:
        """Get all blocked IDs plus stored Telegram names (no new SQL migration).

        Pagination ensures no blocked users are silently omitted by PostgREST's
        row limit. Metadata is loaded in small batches to avoid long query URLs.
        """
        blocked_ids: list[int] = []
        offset, page_size = 0, 500
        while True:
            response = await self._request(
                "GET", "bot_notification_access",
                params={
                    "select": "telegram_id", "blocked": "eq.true",
                    "order": "telegram_id.asc", "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise RuntimeError("Invalid blocked-user query result")
            blocked_ids.extend(int(row["telegram_id"]) for row in rows)
            if len(rows) < page_size:
                break
            offset += page_size

        users_by_id: dict[int, dict] = {}
        batch_size = 100
        for start in range(0, len(blocked_ids), batch_size):
            batch = blocked_ids[start:start + batch_size]
            response = await self._request(
                "GET", "bot_users",
                params={
                    "select": "telegram_id,username,first_name,last_name",
                    "telegram_id": "in.(" + ",".join(map(str, batch)) + ")",
                    "limit": str(len(batch)),
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise RuntimeError("Invalid blocked-user metadata result")
            for row in rows:
                users_by_id[int(row["telegram_id"])] = row

        return [
            {"telegram_id": user_id,
             "username": users_by_id.get(user_id, {}).get("username"),
             "first_name": users_by_id.get(user_id, {}).get("first_name"),
             "last_name": users_by_id.get(user_id, {}).get("last_name")}
            for user_id in blocked_ids
        ]

    async def is_subscription_active(self, telegram_id: int) -> bool:
        """Useful for explaining /start is required after an owner reset."""
        _, epoch = await self.get_broadcast_settings()
        response = await self._request(
            "GET", "bot_notification_access",
            params={"select": "telegram_id", "telegram_id": f"eq.{int(telegram_id)}",
                    "blocked": "eq.false", "subscription_epoch": f"eq.{epoch}", "limit": "1"},
        )
        response.raise_for_status()
        return bool(response.json())

    async def list_active_subscription_ids(self, epoch: int) -> set[int]:
        """Fetch only users opted in during the current owner-controlled epoch."""
        result: set[int] = set()
        offset, page_size = 0, 500
        while True:
            response = await self._request(
                "GET", "bot_notification_access",
                params={"select": "telegram_id", "blocked": "eq.false",
                        "subscription_epoch": f"eq.{int(epoch)}",
                        "order": "telegram_id.asc", "limit": str(page_size),
                        "offset": str(offset)},
            )
            response.raise_for_status()
            rows = response.json() or []
            result.update(int(row["telegram_id"]) for row in rows)
            if len(rows) < page_size:
                return result
            offset += page_size

    async def get_daily_weather_city(self, telegram_id: int) -> str | None:
        response = await self._request(
            "GET", "bot_users",
            params={"select": "weather_daily_city", "telegram_id": f"eq.{int(telegram_id)}",
                    "limit": "1"},
        )
        response.raise_for_status()
        rows = response.json() or []
        return rows[0].get("weather_daily_city") if rows else None

    async def set_alert_subscription(self, telegram_id: int, enabled: bool) -> None:
        response = await self._request(
            "POST", "bot_alert_subscribers",
            params={"on_conflict": "telegram_id"},
            json_body={"telegram_id": int(telegram_id), "enabled": bool(enabled)},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()

    async def list_alert_subscribers(self) -> list[int]:
        result: list[int] = []
        offset, page_size = 0, 500
        while True:
            response = await self._request(
                "GET", "bot_alert_subscribers",
                params={"select": "telegram_id", "enabled": "eq.true",
                        "order": "telegram_id.asc", "limit": str(page_size),
                        "offset": str(offset)},
            )
            response.raise_for_status()
            rows = response.json() or []
            result.extend(int(row["telegram_id"]) for row in rows)
            if len(rows) < page_size:
                return result
            offset += page_size

    async def set_daily_weather_subscription(self, telegram_id: int, enabled: bool) -> None:
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
        result: list[dict] = []
        offset, page_size = 0, 500
        while True:
            response = await self._request(
                "GET", "bot_users",
                params={"select": "telegram_id,weather_daily_city",
                        "weather_daily_enabled": "eq.true",
                        "order": "telegram_id.asc", "limit": str(page_size),
                        "offset": str(offset)},
            )
            response.raise_for_status()
            rows = response.json() or []
            result.extend(rows)
            if len(rows) < page_size:
                return result
            offset += page_size

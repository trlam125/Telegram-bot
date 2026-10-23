"""Daily weather delivery to registered Telegram users, with individual opt-out.

A Vercel Cron GET endpoint. It requires CRON_SECRET and uses the existing
Supabase Data API tables (bot_users, bot_gateway_events).
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from telegram import Bot

from bot import TELEGRAM_BOT_TOKEN, fetch_weather_city, format_weather_fallback
from supabase_db import supabase_db

logger = logging.getLogger("telegram-daily-weather")
router = APIRouter(tags=["Daily weather"])
VIETNAM_TZ = timezone(timedelta(hours=7))


def _default_city() -> str:
    return (os.getenv("DAILY_WEATHER_CITY", "").strip()
            or os.getenv("DEFAULT_WEATHER_CITY", "").strip())


@router.get("/cron/daily-weather")
async def send_daily_weather(authorization: str | None = Header(default=None)) -> dict:
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret or not authorization or not secrets.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Cron authorization required")

    default_city = _default_city()
    if not default_city:
        raise HTTPException(status_code=503, detail="Configure DAILY_WEATHER_CITY")

    # Must query Supabase, not just send a Telegram message. If the DB fails,
    # do not claim the job has succeeded or silently skip recipients.
    try:
        recipients = await supabase_db.list_daily_weather_recipients()
    except Exception as exc:
        logger.exception("Failed to retrieve daily weather recipients from Supabase")
        raise HTTPException(status_code=503, detail="Recipient database unavailable") from exc

    today = datetime.now(VIETNAM_TZ).strftime("%Y-%m-%d")
    friendly_date = datetime.now(VIETNAM_TZ).strftime("%d/%m/%Y")
    forecast_cache: dict[str, str | None] = {}
    succeeded, already_sent, failed = 0, 0, 0

    # Initialize a dedicated Bot so this route works even in serverless
    # environments that do not call the application's lifespan hooks.
    async with Bot(TELEGRAM_BOT_TOKEN) as telegram_bot:
        for recipient in recipients:
            chat_id = int(recipient["telegram_id"])
            city = (recipient.get("weather_daily_city") or "").strip() or default_city
            cache_key = city.casefold()
            if cache_key not in forecast_cache:
                try:
                    weather = await fetch_weather_city(city)
                    forecast_cache[cache_key] = format_weather_fallback(weather) if weather else None
                except Exception:
                    logger.exception("Could not obtain weather for configured city %s", city)
                    forecast_cache[cache_key] = None
            forecast_text = forecast_cache[cache_key]
            if not forecast_text:
                failed += 1
                logger.warning("Skipping %s: weather lookup unavailable for %s", chat_id, city)
                continue

            event_id = f"{today}:{chat_id}"
            try:
                claimed = await supabase_db.claim_gateway_event("daily-weather", event_id)
            except Exception:
                logger.exception("Failed to reserve daily weather delivery for %s", chat_id)
                failed += 1
                continue
            if not claimed:
                already_sent += 1
                continue

            message = (
                f"BẢN TIN THỜI TIẾT BUỔI SÁNG - {friendly_date}\n\n"
                f"{forecast_text}\n\n"
                "Nguồn: Open-Meteo. Dự báo có thể thay đổi.\n"
                "Dùng /weather_off để ngừng nhận tin thời tiết (không ảnh hưởng cảnh báo khẩn cấp)."
            )
            try:
                await telegram_bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                failed += 1
                logger.exception("Daily weather delivery failed for chat %s", chat_id)
                # Permit retry when sending fails (e.g. transient network error).
                try:
                    await supabase_db.release_daily_weather_claim(event_id)
                except Exception:
                    logger.exception("Could not release failed delivery claim %s", event_id)
                continue

            succeeded += 1
            try:
                await supabase_db.finish_gateway_event(
                    "daily-weather", event_id, sent=1, failed=0, blocked=0
                )
            except Exception:
                # Keep the claim to prevent a duplicate message on manual retries.
                logger.exception("Weather sent but could not update event log for %s", chat_id)

    result = {"ok": failed == 0, "recipients": len(recipients),
              "sent": succeeded, "already_sent": already_sent, "failed": failed,
              "date": today}
    if failed:
        raise HTTPException(status_code=502, detail=result)
    return result

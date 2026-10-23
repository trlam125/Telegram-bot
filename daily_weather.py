"""Daily opt-in weather bulletin, triggered by Vercel Cron.

Uses the weather utilities already present in bot.py and performs a real
Supabase read each day. Weather is not an emergency/health alert.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from bot import fetch_weather_city, fetch_weather_coordinates, format_weather_fallback
from supabase_db import supabase_db

logger = logging.getLogger("telegram-daily-weather")
router = APIRouter(tags=["Daily weather"])
VIETNAM_TZ = timezone(timedelta(hours=7))


def _chat_ids() -> list[int]:
    raw = os.getenv("DAILY_WEATHER_CHAT_IDS", "").strip()
    if not raw:
        return []
    try:
        ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise ValueError("DAILY_WEATHER_CHAT_IDS must contain numeric Telegram chat IDs") from exc
    return list(dict.fromkeys(ids))


async def _get_weather():
    latitude = os.getenv("DAILY_WEATHER_LATITUDE", "").strip()
    longitude = os.getenv("DAILY_WEATHER_LONGITUDE", "").strip()
    if latitude and longitude:
        try:
            lat, lon = float(latitude), float(longitude)
        except ValueError as exc:
            raise ValueError("Invalid DAILY_WEATHER_LATITUDE/LONGITUDE") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Weather coordinates out of range")
        label = os.getenv("DAILY_WEATHER_LABEL", "Vị trí đã cấu hình").strip()
        return await fetch_weather_coordinates(lat, lon, label)

    city = os.getenv("DAILY_WEATHER_CITY", "").strip()
    if not city:
        raise ValueError("Set DAILY_WEATHER_CITY or both coordinates")
    weather = await fetch_weather_city(city)
    if not weather:
        raise ValueError(f"Could not find weather location: {city}")
    return weather


@router.get("/cron/daily-weather")
async def send_daily_weather(request: Request, authorization: str | None = Header(default=None)):
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret or not authorization or not secrets.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Cron authorization required")

    try:
        targets = _chat_ids()
        if not targets:
            raise ValueError("Set DAILY_WEATHER_CHAT_IDS to consenting recipients")
        weather = await _get_weather()
    except ValueError as exc:
        logger.error("Daily weather not configured: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Weather API failed")
        raise HTTPException(status_code=503, detail="Weather unavailable") from exc

    # A real database request (not merely a bot message) is necessary to show
    # activity to Supabase. The query also confirms the alerts DB can be reached.
    db_ok = True
    try:
        subscribers = await supabase_db.list_alert_subscribers()
        logger.info("Supabase reachable; %d alert subscribers", len(subscribers))
    except Exception:
        logger.exception("Supabase unavailable during daily weather job")
        db_ok = False

    now = datetime.now(VIETNAM_TZ)
    message = (
        "BẢN TIN THỜI TIẾT HẰNG NGÀY\n"
        f"Ngày {now:%d/%m/%Y}\n\n"
        f"{format_weather_fallback(weather)}\n\n"
        "Nguồn: Open-Meteo (dự báo có thể thay đổi)."
    )
    sent, failures = 0, 0
    for chat_id in targets:
        try:
            await request.app.state.telegram_app.bot.send_message(chat_id=chat_id, text=message)
            sent += 1
        except Exception:
            failures += 1
            logger.exception("Cannot deliver daily weather to an opted-in chat")

    if failures:
        # Return a failure for operational visibility, rather than silently
        # reporting success when a recipient did not receive a message.
        raise HTTPException(
            status_code=502,
            detail=f"Daily weather partially delivered ({sent} succeeded, {failures} failed); db_ok={db_ok}",
        )
    if not db_ok:
        raise HTTPException(status_code=503, detail="Weather delivered but Supabase query failed")
    return {"ok": True, "sent": sent, "db_ok": True}

"""Open-Meteo geocoding, forecasts and AI explanations."""
import logging
import re
from typing import Any, Dict, Optional, Tuple
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from telegram_bot.core.config import DEFAULT_WEATHER_CITY, WMO_WEATHER_CODES
from telegram_bot.db import supabase_db
from telegram_bot.services.ai import call_text_ai
from telegram_bot.services.text import send_long_message
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

def is_weather_query(text: str) -> bool:
    q = text.lower()
    return any(
        marker in q
        for marker in (
            "thời tiết",
            "nhiệt độ",
            "bao nhiêu độ",
            "weather",
            "temperature",
            "có mưa không",
            "trời có mưa",
            "mưa hôm nay",
        )
    )

def extract_weather_city(text: str) -> str:
    value = text.strip()
    patterns = [
        r"(?:thời tiết|nhiệt độ)\s+(?:ở|tại)?\s*([^?!.]+?)(?:\s+(?:hôm nay|hiện tại|bây giờ|ngày mai).*)?$",
        r"(?:ở|tại)\s+([^?!.]+?)(?:\s+(?:hôm nay|hiện tại|bây giờ|bao nhiêu độ|có mưa).*)?$",
        r"^(.+?)\s+(?:hôm nay|hiện tại|bây giờ)\s+.*(?:bao nhiêu độ|thời tiết|nhiệt độ|có mưa).*$",
        r"^(?:hôm nay|hiện tại|bây giờ)\s+(?:ở|tại)?\s*(.+?)\s+(?:bao nhiêu độ|thời tiết|nhiệt độ|có mưa).*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            city = match.group(1).strip(" ,.-")
            if 1 <= len(city) <= 80:
                return city
    return ""

async def geocode_city(city: str) -> Optional[Dict[str, Any]]:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {
        "name": city,
        "count": 1,
        "language": "vi",
        "format": "json",
    }
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    results = data.get("results") or []
    if not results:
        return None
    return results[0]

async def fetch_weather_coordinates(
    latitude: float,
    longitude: float,
    label: str,
) -> Dict[str, Any]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,weather_code,wind_speed_10m"
        ),
        "daily": (
            "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
            "sunrise,sunset"
        ),
        "forecast_days": 2,
        "timezone": "auto",
    }
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1

    return {
        "label": label,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": data.get("timezone") or "",
        "time": current.get("time"),
        "temperature": current.get("temperature_2m"),
        "apparent_temperature": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "weather_code": weather_code,
        "condition": WMO_WEATHER_CODES.get(weather_code, f"Mã thời tiết {weather_code}"),
        "wind_speed": current.get("wind_speed_10m"),
        "today_max": (daily.get("temperature_2m_max") or [None])[0],
        "today_min": (daily.get("temperature_2m_min") or [None])[0],
        "rain_probability": (daily.get("precipitation_probability_max") or [None])[0],
        "sunrise": (daily.get("sunrise") or [None])[0],
        "sunset": (daily.get("sunset") or [None])[0],
    }

async def fetch_weather_city(city: str) -> Optional[Dict[str, Any]]:
    place = await geocode_city(city)
    if not place:
        return None

    name_parts = [place.get("name"), place.get("admin1"), place.get("country")]
    label = ", ".join(str(part) for part in name_parts if part)
    return await fetch_weather_coordinates(
        float(place["latitude"]),
        float(place["longitude"]),
        label,
    )

def format_weather_context(weather: Dict[str, Any]) -> str:
    return (
        "Dữ liệu thời tiết thời gian thực từ Open-Meteo:\n"
        f"Địa điểm: {weather.get('label')}\n"
        f"Thời điểm dữ liệu: {weather.get('time')} ({weather.get('timezone')})\n"
        f"Nhiệt độ: {weather.get('temperature')} °C\n"
        f"Cảm giác như: {weather.get('apparent_temperature')} °C\n"
        f"Tình trạng: {weather.get('condition')}\n"
        f"Độ ẩm: {weather.get('humidity')}%\n"
        f"Lượng mưa hiện tại: {weather.get('precipitation')} mm\n"
        f"Gió: {weather.get('wind_speed')} km/h\n"
        f"Nhiệt độ cao nhất hôm nay: {weather.get('today_max')} °C\n"
        f"Nhiệt độ thấp nhất hôm nay: {weather.get('today_min')} °C\n"
        f"Xác suất mưa cao nhất hôm nay: {weather.get('rain_probability')}%\n"
        f"Bình minh: {weather.get('sunrise')}\n"
        f"Hoàng hôn: {weather.get('sunset')}"
    )

def format_weather_fallback(weather: Dict[str, Any]) -> str:
    return (
        f"Thời tiết tại {weather.get('label')}:\n"
        f"• Hiện tại: {weather.get('temperature')} °C, {weather.get('condition')}\n"
        f"• Cảm giác như: {weather.get('apparent_temperature')} °C\n"
        f"• Độ ẩm: {weather.get('humidity')}%\n"
        f"• Gió: {weather.get('wind_speed')} km/h\n"
        f"• Hôm nay: {weather.get('today_min')}–{weather.get('today_max')} °C\n"
        f"• Xác suất mưa tối đa: {weather.get('rain_probability')}%\n"
        f"• Cập nhật: {weather.get('time')} ({weather.get('timezone')})"
    )

async def answer_weather(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    city: Optional[str] = None,
    coordinates: Optional[Tuple[float, float]] = None,
) -> bool:
    try:
        if coordinates:
            lat, lon = coordinates
            weather = await fetch_weather_coordinates(lat, lon, "Vị trí Telegram đã gửi")
        else:
            selected_city = city or context.user_data.get("weather_city") or DEFAULT_WEATHER_CITY
            if not selected_city:
                return False
            weather = await fetch_weather_city(str(selected_city))
            if weather is None:
                await update.message.reply_text(
                    f"Tôi không tìm thấy địa điểm '{selected_city}'. Hãy thử /weather Hà Nội"
                )
                return True
            context.user_data["weather_city"] = selected_city
            if update.effective_user and update.effective_chat and update.effective_chat.type == "private":
                try:
                    await supabase_db.set_daily_weather_city(update.effective_user.id, str(selected_city))
                except Exception:
                    logger.exception("Could not save daily weather city for user %s", update.effective_user.id)

        weather_context = format_weather_context(weather)
        try:
            answer = await call_text_ai(context, query, extra_context=weather_context)
        except Exception as ai_exc:
            logger.warning("NVIDIA failed after weather fetch, using direct response: %s", ai_exc)
            answer = format_weather_fallback(weather)

        await send_long_message(update, answer)
        return True
    except Exception as exc:
        logger.exception("Weather error: %s", exc)
        await update.message.reply_text(
            "Không lấy được dữ liệu thời tiết lúc này. Hãy thử lại hoặc dùng /web để tìm trên web."
        )
        return True

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SECRET = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()
BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("WEBHOOK_BASE_URL", "")).strip().rstrip("/")

if not TOKEN:
    raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN")
if not BASE_URL:
    raise RuntimeError("Dùng: python set_webhook.py https://<cloud-run-url>")

WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"


async def main() -> None:
    payload = {
        "url": WEBHOOK_URL,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }
    if SECRET:
        payload["secret_token"] = SECRET

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    print(data)
    if data.get("ok"):
        print("Webhook:", WEBHOOK_URL)


if __name__ == "__main__":
    asyncio.run(main())

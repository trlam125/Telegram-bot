import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN")


async def main() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
            json={"drop_pending_updates": False},
        )
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())

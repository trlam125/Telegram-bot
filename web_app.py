import asyncio
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import DefaultDict

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update

from bot import build_application, setup_bot_commands
from supabase_db import supabase_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-webhook")

WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()
telegram_app = build_application()
_user_locks: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await telegram_app.initialize()
    await setup_bot_commands(telegram_app)
    await telegram_app.start()
    logger.info("Telegram application initialized for webhook mode")
    try:
        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="Telegram AI Bot", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {"ok": True, "service": "telegram-ai-bot", "mode": "webhook"}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if WEBHOOK_SECRET_TOKEN and x_telegram_bot_api_secret_token != WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    if update is None:
        raise HTTPException(status_code=400, detail="Invalid Telegram update")

    # Telegram can retry webhooks. Claim the update in Supabase so expensive AI/media
    # work is not performed twice.
    try:
        claimed = await supabase_db.claim_update(update.update_id)
    except Exception as exc:
        logger.exception("Could not claim update %s: %s", update.update_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if not claimed:
        return {"ok": True, "duplicate": True}

    user_id = update.effective_user.id if update.effective_user else 0
    lock = _user_locks[user_id]
    try:
        async with lock:
            await telegram_app.process_update(update)
        await supabase_db.finish_update(update.update_id, "done")
        return {"ok": True}
    except Exception as exc:
        logger.exception("Webhook processing failed for update %s: %s", update.update_id, exc)
        try:
            await supabase_db.finish_update(update.update_id, "failed")
        except Exception:
            logger.exception("Could not mark update %s as failed", update.update_id)
        raise HTTPException(status_code=500, detail="Update processing failed") from exc

"""Backward-compatible Uvicorn entrypoint: uvicorn web_app:app."""
from telegram_bot.api.web import app, telegram_app
__all__ = ['app', 'telegram_app']

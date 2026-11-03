"""Backward-compatible local polling entrypoint: python bot.py."""
from telegram_bot.application import build_application, setup_bot_commands, main

if __name__ == '__main__':
    main()

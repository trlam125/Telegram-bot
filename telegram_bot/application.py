"""Build the python-telegram-bot Application for polling or webhook."""
import logging
from telegram import (BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeChat,
    MenuButtonCommands)
from telegram.ext import (Application, CommandHandler, ContextTypes, MessageHandler, filters)
from telegram_bot.core.config import (TELEGRAM_BOT_TOKEN, TEXT_MODEL, VISION_MODEL,
    OMNI_MODEL, NVIDIA_BASE_URL, AUTO_WEB_SEARCH, AUTO_WEATHER)
from telegram_bot.core.state import with_supabase_state
from telegram_bot.handlers.commands import (BOT_COMMANDS, OWNER_BROADCAST_COMMANDS, start, stop_alerts,
    notify_off, notify_on, reset_subscribers, block_user, unblock_user, blocked_list, help_command, clear_command, reset_command,
    web_command, weather_command, weather_on, weather_off)
from telegram_bot.handlers.messages import (chat, handle_photo, handle_document,
    handle_video, handle_audio, handle_location, unknown_command, unsupported_message)
from telegram_bot.services.notifications import owner_telegram_id
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram bot error", exc_info=context.error)

async def setup_bot_commands(application: Application) -> None:
    """Dọn command cũ ở scope chung rồi đăng ký menu hiện tại."""
    scopes = [BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()]
    language_codes = ("vi", "en")

    for scope in scopes:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except Exception as exc:
            logger.warning(
                "Không thể xóa command cũ ở scope %s: %s",
                type(scope).__name__,
                exc,
            )

        for language_code in language_codes:
            try:
                await application.bot.delete_my_commands(
                    scope=scope,
                    language_code=language_code,
                )
            except Exception as exc:
                logger.debug(
                    "Không thể xóa command cũ scope=%s language=%s: %s",
                    type(scope).__name__,
                    language_code,
                    exc,
                )

        await application.bot.set_my_commands(BOT_COMMANDS, scope=scope)
        for language_code in language_codes:
            try:
                await application.bot.set_my_commands(
                    BOT_COMMANDS,
                    scope=scope,
                    language_code=language_code,
                )
            except Exception as exc:
                logger.debug(
                    "Không thể set command scope=%s language=%s: %s",
                    type(scope).__name__,
                    language_code,
                    exc,
                )

    # A user-specific scope outranks the global/private menus. This only exposes
    # admin command suggestions to the Telegram account in TELEGRAM_OWNER_ID.
    owner = owner_telegram_id()
    if owner is not None:
        owner_scope = BotCommandScopeChat(chat_id=owner)
        owner_commands = BOT_COMMANDS + OWNER_BROADCAST_COMMANDS
        await application.bot.set_my_commands(owner_commands, scope=owner_scope)
        for language_code in language_codes:
            await application.bot.set_my_commands(
                owner_commands, scope=owner_scope, language_code=language_code,
            )

    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    registered = await application.bot.get_my_commands(
        scope=BotCommandScopeAllPrivateChats()
    )
    logger.info(
        "Đã đăng ký %d Telegram commands: %s",
        len(registered),
        [command.command for command in registered],
    )

def build_application() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(setup_bot_commands)
        .build()
    )

    app.add_handler(CommandHandler("start", with_supabase_state(start)))
    app.add_handler(CommandHandler("notify_off", with_supabase_state(notify_off)))
    app.add_handler(CommandHandler("notify_on", with_supabase_state(notify_on)))
    app.add_handler(CommandHandler("reset_subscribers", with_supabase_state(reset_subscribers)))
    app.add_handler(CommandHandler("block", with_supabase_state(block_user)))
    app.add_handler(CommandHandler("unblock", with_supabase_state(unblock_user)))
    app.add_handler(CommandHandler("blocked_list", with_supabase_state(blocked_list)))
    app.add_handler(CommandHandler("stop", with_supabase_state(stop_alerts)))
    app.add_handler(CommandHandler("help", with_supabase_state(help_command)))
    app.add_handler(CommandHandler("clear", with_supabase_state(clear_command)))
    app.add_handler(CommandHandler("reset", with_supabase_state(reset_command)))
    app.add_handler(CommandHandler("web", with_supabase_state(web_command)))
    app.add_handler(CommandHandler("weather", with_supabase_state(weather_command)))
    app.add_handler(CommandHandler("weather_on", with_supabase_state(weather_on)))
    app.add_handler(CommandHandler("weather_off", with_supabase_state(weather_off)))

    app.add_handler(MessageHandler(filters.PHOTO, with_supabase_state(handle_photo)))
    app.add_handler(
        MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, with_supabase_state(handle_video))
    )
    app.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, with_supabase_state(handle_audio))
    )
    app.add_handler(MessageHandler(filters.Document.ALL, with_supabase_state(handle_document)))
    app.add_handler(MessageHandler(filters.LOCATION, with_supabase_state(handle_location)))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, with_supabase_state(chat))
    )
    app.add_handler(MessageHandler(filters.COMMAND, with_supabase_state(unknown_command)))
    app.add_handler(MessageHandler(~filters.TEXT, with_supabase_state(unsupported_message)))
    app.add_error_handler(error_handler)
    return app

def main() -> None:
    """Local development mode: polling. Production uses web_app.py + webhook."""
    app = build_application()

    print("=" * 66)
    print("Telegram bot - local polling mode")
    print(f"Text model       : {TEXT_MODEL}")
    print(f"Vision model     : {VISION_MODEL}")
    print(f"Omni media model: {OMNI_MODEL}")
    print(f"NVIDIA endpoint  : {NVIDIA_BASE_URL}")
    print(f"Auto web search  : {AUTO_WEB_SEARCH}")
    print(f"Auto weather     : {AUTO_WEATHER}")
    print("Persistence      : Supabase")
    print("File storage     : Telegram private channel")
    print("Bot dang chay... Nhan Ctrl+C de dung.")
    print("=" * 66)

    app.run_polling(allowed_updates=["message"])

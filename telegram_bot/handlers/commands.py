"""Telegram commands; no API server or AI implementation belongs here."""
import logging
from telegram import (BotCommand, BotCommandScopeChat, MenuButtonCommands, Update)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from telegram_bot.core.config import CLEAR_SCREEN_MAX_MESSAGES, DEFAULT_WEATHER_CITY
from telegram_bot.core.state import ensure_started
from telegram_bot.db import supabase_db
from telegram_bot.services.notifications import is_owner_private_chat
from telegram_bot.services.search import answer_with_web
from telegram_bot.services.weather import answer_weather
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

BOT_COMMANDS = [
    BotCommand("start", "Bắt đầu sử dụng bot và nhận thông báo"),
    BotCommand("stop", "Tắt thông báo từ gateway"),
    BotCommand("clear", "Xóa tin nhắn trên màn hình chat"),
    BotCommand("reset", "Xóa ngữ cảnh AI và tài liệu"),
    BotCommand("web", "Tìm thông tin trên web"),
    BotCommand("weather", "Xem thời tiết và lưu thành phố cho bản tin"),
    BotCommand("weather_on", "Bật nhận bản tin thời tiết hằng ngày"),
    BotCommand("weather_off", "Tắt bản tin thời tiết (vẫn nhận cảnh báo)"),
    BotCommand("help", "Xem hướng dẫn sử dụng"),
]

OWNER_BROADCAST_COMMANDS = [
    BotCommand("notify_off", "Tắt gửi thông báo cho tất cả trừ chủ bot"),
    BotCommand("notify_on", "Bật lại thông báo cho mọi người đã đăng ký"),
]

async def ensure_chat_command_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xóa command cũ ở scope của chat rồi đăng ký lại menu hiện tại."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type != "private":
        return

    scope = BotCommandScopeChat(chat.id)

    # Telegram lưu command theo cả scope và language_code. Một command list cũ
    # ở scope chat/language có thể ưu tiên hơn danh sách default mới. Vì vậy
    # phải xóa các biến thể thường gặp trước khi set lại.
    language_codes = {"vi", "en"}
    if user and user.language_code:
        language_codes.add(user.language_code.lower())

    await context.bot.delete_my_commands(scope=scope)
    for language_code in sorted(language_codes):
        try:
            await context.bot.delete_my_commands(
                scope=scope,
                language_code=language_code,
            )
        except TelegramError as exc:
            logger.debug(
                "Không thể xóa command scope chat=%s language=%s: %s",
                chat.id,
                language_code,
                exc,
            )

    # Đăng ký danh sách mới cho scope chung của chat và các ngôn ngữ liên quan.
    chat_commands = BOT_COMMANDS + OWNER_BROADCAST_COMMANDS if is_owner_private_chat(update) else BOT_COMMANDS
    await context.bot.set_my_commands(chat_commands, scope=scope)
    for language_code in sorted(language_codes):
        try:
            await context.bot.set_my_commands(
                chat_commands,
                scope=scope,
                language_code=language_code,
            )
        except TelegramError as exc:
            logger.debug(
                "Không thể set command scope chat=%s language=%s: %s",
                chat.id,
                language_code,
                exc,
            )

    await context.bot.set_chat_menu_button(
        chat_id=chat.id,
        menu_button=MenuButtonCommands(),
    )

    registered = await context.bot.get_my_commands(scope=scope)
    logger.info(
        "Command menu chat %s: %s",
        chat.id,
        [command.command for command in registered],
    )

async def set_global_notifications(update: Update, enabled: bool) -> None:
    """Owner-only broadcast switch; never changes any individual's opt-in status."""
    if not is_owner_private_chat(update):
        await update.effective_message.reply_text("Lệnh này chỉ dành cho chủ sở hữu bot.")
        return
    try:
        await supabase_db.set_broadcast_enabled(enabled)
    except Exception:
        logger.exception("Could not update global notification switch")
        await update.effective_message.reply_text(
            "Không lưu được trạng thái thông báo trên Supabase. Không thay đổi cài đặt."
        )
        return
    if enabled:
        message = ("Đã BẬT thông báo cho tất cả người đã đăng ký nhận tin. "
                   "Chủ bot luôn nhận thông báo.")
    else:
        message = ("Đã TẮT thông báo cho những người khác. "
                   "Chỉ chủ bot tiếp tục nhận cảnh báo gateway và bản tin thời tiết tự động.")
    await update.effective_message.reply_text(message)

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_global_notifications(update, False)

async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_global_notifications(update, True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # /start bắt đầu một phiên mới sạch sẽ nhưng vẫn giữ bot ở trạng thái đã khởi động.
    context.user_data.clear()
    context.user_data["started"] = True

    subscription_ok = True
    if update.effective_chat and update.effective_chat.type == "private":
        try:
            await supabase_db.set_alert_subscription(update.effective_user.id, True)
        except Exception:
            subscription_ok = False
            logger.exception("Không thể đăng ký cảnh báo cho user %s", update.effective_user.id)

    # Do not change weather_daily_enabled: /weather_off survives /start.
    # Đồng bộ command cho chính private chat này.
    # Nhờ vậy khi người dùng gõ '/', Telegram có danh sách lệnh để gợi ý.
    try:
        await ensure_chat_command_menu(update, context)
    except Exception as exc:
        logger.warning("Không thể đồng bộ command menu cho chat %s: %s", update.effective_chat.id if update.effective_chat else None, exc)

    await update.message.reply_text(
        "Xin chào! Tôi là một trợ lý AI mini được thiết kế để hoạt động trên Telegram. "
        "Tôi ưu tiên trả lời bằng tiếng Việt nếu bạn sử dụng tiếng Việt trong cuộc trò chuyện."
        + ("\n\n🔔 Đã bật nhận thông báo từ các hệ thống kết nối. Gửi /stop để tắt."
           if subscription_ok and update.effective_chat and update.effective_chat.type == "private"
           else ("\n\nĐể nhận cảnh báo, hãy dùng /start trong chat riêng với bot."
                 if update.effective_chat and update.effective_chat.type != "private"
                 else "\n\n⚠️ Chưa bật được thông báo. Hãy thử /start lại sau khi quản trị viên kiểm tra database."))
        + "\n\n🌤️ Thời tiết tự động: /weather Hà Nội để chọn thành phố, /weather_on để bật, /weather_off để tắt."
    )

async def stop_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Opt out of broadcasts without disabling AI chat functionality."""
    if not update.effective_chat or update.effective_chat.type != "private":
        await update.effective_message.reply_text("Vui lòng dùng /stop trong chat riêng với bot.")
        return
    try:
        await supabase_db.set_alert_subscription(update.effective_user.id, False)
    except Exception:
        logger.exception("Không thể hủy đăng ký user %s", update.effective_user.id)
        await update.message.reply_text("Không thể tắt thông báo lúc này, vui lòng thử lại.")
        return
    await update.message.reply_text(
        "🔕 Đã tắt thông báo từ các hệ thống kết nối. "
        "Bạn vẫn có thể chat với AI. Gửi /start để bật lại."
    )

async def weather_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != "private":
        await update.effective_message.reply_text("Hãy dùng lệnh này trong chat riêng với bot.")
        return
    try:
        await supabase_db.set_daily_weather_subscription(update.effective_user.id, True)
    except Exception:
        logger.exception("Failed to enable daily weather")
        await update.effective_message.reply_text("Không bật được thời tiết, hãy thử lại sau.")
        return
    await update.effective_message.reply_text(
        "Đã bật bản tin thời tiết buổi sáng. Dùng /weather Hà Nội để chọn thành phố. "
        "Cảnh báo khẩn cấp được quản lý riêng bằng /start và /stop."
    )

async def weather_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != "private":
        await update.effective_message.reply_text("Hãy dùng lệnh này trong chat riêng với bot.")
        return
    try:
        await supabase_db.set_daily_weather_subscription(update.effective_user.id, False)
    except Exception:
        logger.exception("Failed to disable daily weather")
        await update.effective_message.reply_text("Không tắt được thời tiết, hãy thử lại sau.")
        return
    await update.effective_message.reply_text(
        "Đã tắt bản tin thời tiết hằng ngày. "
        "Đăng ký cảnh báo đột quỵ không thay đổi. Dùng /weather_on để bật lại."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return

    await update.message.reply_text(
        "CÁCH DÙNG BOT V4\n\n"
        "/start - Bật nhận cảnh báo từ các project qua gateway.\n"
        "/stop - Ngừng nhận cảnh báo, vẫn chat AI bình thường.\n"
        "/clear - Xóa các tin nhắn gần đây khỏi màn hình chat, không xóa ngữ cảnh AI.\n"
        "/reset - Xóa ngữ cảnh AI và tài liệu, nhưng giữ tin nhắn trên màn hình.\n\n"
        "1. Chat bình thường: gửi tin nhắn. Bot tự tìm web với một số câu hỏi mang tính thời điểm.\n\n"
        "2. Tìm web chủ động:\n"
        "/web giá vàng hôm nay\n"
        "/web tin mới về NVIDIA\n\n"
        "3. Thời tiết:\n"
        "/weather_on - Bật bản tin thời tiết buổi sáng.\n"
        "/weather_off - Tắt bản tin thời tiết, không ảnh hưởng cảnh báo.\n"
        "/weather Hà Nội\n"
        "Sau khi dùng /weather Hà Nội một lần, bot sẽ ghi nhớ thành phố để bạn có thể hỏi 'hôm nay bao nhiêu độ?'.\n"
        "Bạn cũng có thể gửi Location của Telegram để bot xem thời tiết tại tọa độ đó.\n\n"
        "4. Tài liệu: gửi PDF/DOCX/TXT/MD/CSV rồi hỏi về nội dung.\n\n"
        "5. Ảnh: gửi ảnh kèm caption như 'Đọc chữ và giải thích ảnh này'.\n\n"
        "6. Video: quay bằng điện thoại rồi gửi MP4/MOV vào bot, có thể kèm caption như 'Tóm tắt video này'.\n\n"
        "7. Audio/Voice: gửi file âm thanh hoặc tin nhắn thoại để bot nghe, chép lời và phân tích.\n\n"
        "Lưu ý: media vẫn được lưu vào Storage trước. Giới hạn 20 MB mặc định chỉ áp dụng cho bước bot tải file về để AI phân tích; video phân tích tối đa 120 giây. PDF scan chỉ chứa ảnh có thể không trích xuất được chữ."
        + ("\n\nQuản trị thông báo: /notify_off - chỉ chủ bot nhận; "
           "/notify_on - bật lại cho tất cả người đã đăng ký."
           if is_owner_private_chat(update) else "")
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dọn lịch sử chat mà không gửi tin nhắn xác nhận.

    Không xóa phiên, ngữ cảnh AI hoặc các cài đặt. Bot API chỉ cho phép
    xóa những tin nhắn còn trong giới hạn của Telegram (thường dưới 48 giờ).
    """
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return

    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return

    if chat.type != "private":
        await message.reply_text("/clear màn hình chỉ hỗ trợ trong chat riêng với bot.")
        return

    current_id = message.message_id
    first_id = max(1, current_id - CLEAR_SCREEN_MAX_MESSAGES + 1)
    candidate_ids = list(range(first_id, current_id + 1))

    # Xóa cả tin nhắn /clear; không gửi thêm thông báo xác nhận.
    # Mỗi batch tối đa 100 message ID.
    for i in range(0, len(candidate_ids), 100):
        batch = candidate_ids[i:i + 100]
        try:
            await context.bot.delete_messages(chat_id=chat.id, message_ids=batch)
        except TelegramError as batch_exc:
            logger.debug("Bulk delete failed, fallback to single delete: %s", batch_exc)
            for message_id in batch:
                try:
                    await context.bot.delete_message(chat_id=chat.id, message_id=message_id)
                except TelegramError:
                    pass

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xóa ngữ cảnh AI nhưng không xóa tin nhắn trên màn hình Telegram."""
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return

    context.user_data.clear()
    context.user_data["started"] = True
    await update.message.reply_text("Đã xóa ngữ cảnh AI và tài liệu. Tin nhắn trên màn hình vẫn được giữ.")

async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return

    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Cách dùng: /web <nội dung cần tìm>\nVí dụ: /web giá vàng hôm nay")
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        ok = await answer_with_web(update, context, query)
        if not ok:
            await update.message.reply_text(
                "Không tìm được kết quả web lúc này. Hãy thử từ khóa khác hoặc thử lại sau."
            )
    except Exception as exc:
        logger.exception("/web error: %s", exc)
        await update.message.reply_text("Có lỗi khi tìm web. Hãy thử lại sau.")

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return

    city = " ".join(context.args).strip()
    selected = city or context.user_data.get("weather_city") or DEFAULT_WEATHER_CITY
    if not selected:
        await update.message.reply_text(
            "Hãy ghi thành phố, ví dụ: /weather Hà Nội"
        )
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    await answer_weather(
        update,
        context,
        query=f"Cho tôi biết thời tiết hiện tại và thời tiết hôm nay ở {selected}.",
        city=str(selected),
    )

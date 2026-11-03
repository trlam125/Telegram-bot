"""Text, photo, document, video, audio, and location handlers."""
import asyncio
import logging
import mimetypes
from pathlib import Path
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from telegram_bot.core.config import (AUTO_WEATHER, DEFAULT_WEATHER_CITY,
    MAX_DOCUMENT_CHARS, MAX_FILE_MB, MAX_VIDEO_MB, MAX_VIDEO_SECONDS, MAX_AUDIO_MB,
    SUPPORTED_DOCUMENT_EXTENSIONS, SUPPORTED_IMAGE_MIME_TYPES)
from telegram_bot.core.state import ensure_started, add_history
from telegram_bot.services.ai import (call_text_ai, call_vision_ai,
    call_audio_ai, polish_media_answer, analyze_video_with_fallback)
from telegram_bot.services.search import needs_web_search, answer_with_web
from telegram_bot.services.weather import (is_weather_query, extract_weather_city, answer_weather)
from telegram_bot.services.text import send_long_message
from telegram_bot.services.documents import extract_document_text
from telegram_bot.services.media_processing import audio_to_wav
from telegram_bot.services.storage import archive_media_message
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start để bắt đầu.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        if AUTO_WEATHER and is_weather_query(user_text):
            city = extract_weather_city(user_text)
            selected = city or context.user_data.get("weather_city") or DEFAULT_WEATHER_CITY
            if not selected:
                await update.message.reply_text(
                    "Bạn muốn xem thời tiết ở đâu?\n"
                    "Ví dụ: /weather Hà Nội"
                )
                return
            handled = await answer_weather(update, context, user_text, city=str(selected))
            if handled:
                return

        if needs_web_search(user_text):
            handled = await answer_with_web(update, context, user_text)
            if handled:
                return

        answer = await call_text_ai(context, user_text)
        await send_long_message(update, answer)
    except Exception as exc:
        logger.exception("Lỗi chat: %s", exc)
        await update.message.reply_text(
            "Không thể xử lý yêu cầu lúc này. Hãy kiểm tra NVIDIA API key, model và kết nối mạng."
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start trước.")
        return

    photo = update.message.photo[-1]
    await archive_media_message(
        update,
        context,
        media=photo,
        file_type="photo",
        file_name=f"photo_{photo.file_unique_id}.jpg",
        mime_type="image/jpeg",
    )

    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        telegram_file = await photo.get_file()
        data = bytes(await telegram_file.download_as_bytearray())

        prompt = (update.message.caption or "Hãy phân tích chi tiết ảnh này.").strip()
        answer = await call_vision_ai(prompt, data, "image/jpeg")
        add_history(context, f"[Người dùng gửi ảnh] {prompt}", answer)
        await send_long_message(update, answer)
    except Exception as exc:
        logger.exception("Lỗi khi phân tích ảnh: %s", exc)
        await update.message.reply_text(
            "Không thể phân tích ảnh. Hãy kiểm tra NVIDIA_VISION_MODEL hoặc thử ảnh khác."
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start trước.")
        return

    document = update.message.document
    filename = document.file_name or "document"
    mime_type = document.mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    storage_message_id = await archive_media_message(
        update,
        context,
        media=document,
        file_type="document",
        file_name=filename,
        mime_type=mime_type,
    )

    if document.file_size and document.file_size > MAX_FILE_MB * 1024 * 1024:
        if storage_message_id:
            await update.message.reply_text(
                f"Đã lưu file vào Storage. File vượt {MAX_FILE_MB} MB nên bot không tải về để AI phân tích."
            )
        else:
            await update.message.reply_text(
                f"File vượt {MAX_FILE_MB} MB nên bot không thể tải về để AI phân tích. "
                "Đồng thời chưa xác nhận được việc lưu vào Storage."
            )
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        telegram_file = await document.get_file()
        data = bytes(await telegram_file.download_as_bytearray())

        if mime_type in SUPPORTED_IMAGE_MIME_TYPES:
            prompt = (update.message.caption or "Hãy phân tích chi tiết ảnh này.").strip()
            answer = await call_vision_ai(prompt, data, mime_type)
            add_history(context, f"[Người dùng gửi ảnh {filename}] {prompt}", answer)
            await send_long_message(update, answer)
            return

        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:
            await update.message.reply_text(
                "Định dạng chưa hỗ trợ. Hãy gửi PDF, DOCX, TXT, MD, CSV, JPG, PNG hoặc WEBP."
            )
            return

        text = extract_document_text(filename, data).strip()
        if not text:
            await update.message.reply_text(
                "Không trích xuất được chữ từ tài liệu này. Nếu đây là PDF scan, "
                "hãy gửi trang cần đọc dưới dạng ảnh."
            )
            return

        original_length = len(text)
        truncated = original_length > MAX_DOCUMENT_CHARS
        if truncated:
            text = text[:MAX_DOCUMENT_CHARS]

        context.user_data["document_name"] = filename
        context.user_data["document_text"] = text
        context.user_data["history"] = []

        status = f"Đã đọc tài liệu: {filename}\nĐã nạp {len(text):,} ký tự"
        if truncated:
            status += f" / {original_length:,} ký tự (đã cắt theo MAX_DOCUMENT_CHARS)"

        caption = (update.message.caption or "").strip()
        if caption:
            await update.message.reply_text(status + "\n\nĐang xử lý yêu cầu trong caption...")
            answer = await call_text_ai(context, caption)
            await send_long_message(update, answer)
        else:
            await update.message.reply_text(
                status
                + "\n\nBây giờ bạn có thể hỏi về tài liệu, ví dụ:\n"
                + "• Tóm tắt tài liệu này\n"
                + "• Liệt kê các ý chính\n"
                + "• Giải thích phần ..."
            )

    except Exception as exc:
        logger.exception("Lỗi xử lý tài liệu: %s", exc)
        await update.message.reply_text(
            "Không thể đọc file này. Hãy kiểm tra file có bị lỗi/mã hóa hoặc thử định dạng khác."
        )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    video = update.message.video or update.message.video_note
    if not video:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start trước.")
        return

    video_name = getattr(video, "file_name", None) or (
        f"video_note_{video.file_unique_id}.mp4" if update.message.video_note else "video.mp4"
    )
    video_mime = getattr(video, "mime_type", None) or "video/mp4"
    storage_message_id = await archive_media_message(
        update,
        context,
        media=video,
        file_type="video_note" if update.message.video_note else "video",
        file_name=video_name,
        mime_type=video_mime,
    )

    file_size = getattr(video, "file_size", None)
    duration = float(getattr(video, "duration", 0) or 0)

    if file_size and file_size > MAX_VIDEO_MB * 1024 * 1024:
        if storage_message_id:
            await update.message.reply_text(
                f"Đã lưu video vào Storage. Video vượt {MAX_VIDEO_MB} MB nên bot không tải về để AI phân tích."
            )
        else:
            await update.message.reply_text(
                f"Video vượt {MAX_VIDEO_MB} MB nên bot không thể tải về để AI phân tích. "
                "Đồng thời chưa xác nhận được việc lưu vào Storage."
            )
        return

    if duration and duration > MAX_VIDEO_SECONDS:
        await update.message.reply_text(
            f"Video dài {duration:.0f} giây. Bot hiện nhận tối đa {MAX_VIDEO_SECONDS} giây "
            "để phù hợp giới hạn model NVIDIA. Hãy cắt video rồi gửi lại."
        )
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    prompt = (update.message.caption or "Hãy phân tích video này và cho tôi biết nội dung chính.").strip()
    mime_type = video_mime
    file_name = video_name
    suffix = Path(file_name).suffix.lower() or ".mp4"

    try:
        telegram_file = await video.get_file()
        data = bytes(await telegram_file.download_as_bytearray())

        if len(data) > MAX_VIDEO_MB * 1024 * 1024:
            await update.message.reply_text(
                f"Video tải về vượt {MAX_VIDEO_MB} MB. Hãy nén/cắt ngắn rồi gửi lại."
            )
            return

        answer = await analyze_video_with_fallback(
            context,
            prompt,
            data,
            mime_type,
            suffix,
            duration,
        )
        add_history(context, f"[Người dùng gửi video] {prompt}", answer)
        await send_long_message(update, answer)
    except Exception as exc:
        logger.exception("Lỗi xử lý video: %s", exc)
        await update.message.reply_text(
            "Không thể phân tích video này. Hãy thử video MP4 ngắn hơn, giảm dung lượng, "
            "hoặc kiểm tra NVIDIA_OMNI_MODEL trong .env."
        )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    media = update.message.voice or update.message.audio
    if not media:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start trước.")
        return

    media_name = getattr(media, "file_name", None) or (
        f"voice_{media.file_unique_id}.ogg" if update.message.voice else "audio.bin"
    )
    media_mime = getattr(media, "mime_type", None) or (
        "audio/ogg" if update.message.voice else "application/octet-stream"
    )
    storage_message_id = await archive_media_message(
        update,
        context,
        media=media,
        file_type="voice" if update.message.voice else "audio",
        file_name=media_name,
        mime_type=media_mime,
    )

    file_size = getattr(media, "file_size", None)
    if file_size and file_size > MAX_AUDIO_MB * 1024 * 1024:
        if storage_message_id:
            await update.message.reply_text(
                f"Đã lưu audio vào Storage. Audio vượt {MAX_AUDIO_MB} MB nên bot không tải về để AI phân tích."
            )
        else:
            await update.message.reply_text(
                f"Audio vượt {MAX_AUDIO_MB} MB nên bot không thể tải về để AI phân tích. "
                "Đồng thời chưa xác nhận được việc lưu vào Storage."
            )
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    prompt = (update.message.caption or "Hãy chép lời và tóm tắt nội dung âm thanh này.").strip()
    file_name = media_name
    suffix = Path(file_name).suffix.lower() or ".bin"

    try:
        telegram_file = await media.get_file()
        data = bytes(await telegram_file.download_as_bytearray())
        wav = await asyncio.to_thread(audio_to_wav, data, suffix)
        raw = await call_audio_ai(prompt, wav)
        answer = await polish_media_answer(context, prompt, raw, "audio")
        add_history(context, f"[Người dùng gửi audio/voice] {prompt}", answer)
        await send_long_message(update, answer)
    except Exception as exc:
        logger.exception("Lỗi xử lý audio/voice: %s", exc)
        await update.message.reply_text(
            "Không thể xử lý âm thanh này. Hãy thử MP3/WAV hoặc gửi lại voice ngắn hơn."
        )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return

    if not ensure_started(context):
        await update.message.reply_text("Bạn chưa khởi động bot. Hãy gửi /start trước.")
        return

    location = update.message.location
    context.user_data["weather_latitude"] = location.latitude
    context.user_data["weather_longitude"] = location.longitude

    await update.effective_chat.send_action(ChatAction.TYPING)
    await answer_weather(
        update,
        context,
        query="Cho tôi biết thời tiết hiện tại và thời tiết hôm nay tại vị trí tôi vừa gửi.",
        coordinates=(location.latitude, location.longitude),
    )

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return
    await update.message.reply_text("Lệnh không tồn tại. Gửi /help để xem hướng dẫn.")

async def unsupported_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_started(context):
        await update.message.reply_text("Hãy gửi /start trước để sử dụng bot.")
        return
    await update.message.reply_text(
        "Hiện bot hỗ trợ văn bản, ảnh, video, audio/voice, Location và tài liệu PDF/DOCX/TXT/MD/CSV."
    )

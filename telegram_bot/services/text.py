"""Consistent plain-text output to Telegram."""
import re
from telegram import Update

def clean_display_text(text: str) -> str:
    """Làm sạch Markdown phổ biến để Telegram không hiện các dấu *, **, __ thô."""
    text = (text or "").replace("\r\n", "\n")

    # Bỏ marker bold/italic kiểu Markdown mà model thường sinh ra.
    text = text.replace("**", "").replace("__", "")

    # Tiêu đề Markdown -> văn bản thường.
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)

    # Bullet Markdown bằng * hoặc - -> bullet Unicode gọn hơn.
    text = re.sub(r"(?m)^\s*\*\s+", "• ", text)
    text = re.sub(r"(?m)^\s*-\s+", "• ", text)

    return text.strip()

async def send_long_message(update: Update, text: str) -> None:
    if not update.message:
        return

    text = clean_display_text(text or "AI không trả về nội dung.")
    chunk_size = 4000

    while text:
        if len(text) <= chunk_size:
            await update.message.reply_text(text, disable_web_page_preview=True)
            break

        cut = text.rfind("\n", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = text.rfind(" ", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = chunk_size

        await update.message.reply_text(
            text[:cut].rstrip(), disable_web_page_preview=True
        )
        text = text[cut:].lstrip()

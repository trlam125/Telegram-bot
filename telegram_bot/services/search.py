"""DuckDuckGo search and AI-enriched answers."""
import asyncio
import logging
from typing import Any, Dict, List
from ddgs import DDGS
from telegram import Update
from telegram.ext import ContextTypes
from telegram_bot.core.config import AUTO_WEB_SEARCH, WEB_MAX_RESULTS
from telegram_bot.services.ai import call_text_ai
from telegram_bot.services.text import send_long_message
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

def looks_like_news_query(text: str) -> bool:
    q = text.lower()
    return any(
        keyword in q
        for keyword in (
            "tin tức",
            "tin mới",
            "news",
            "breaking",
            "mới xảy ra",
            "vừa xảy ra",
        )
    )

def needs_web_search(text: str) -> bool:
    if not AUTO_WEB_SEARCH:
        return False

    q = text.lower()
    current_markers = (
        "hôm nay",
        "hiện tại",
        "bây giờ",
        "mới nhất",
        "gần đây",
        "vừa mới",
        "latest",
        "current",
        "today",
        "right now",
        "tin tức",
        "tin mới",
        "news",
        "giá vàng",
        "giá bitcoin",
        "giá btc",
        "giá eth",
        "giá cổ phiếu",
        "tỷ giá",
        "bao nhiêu tiền",
        "kết quả trận",
        "kết quả bóng đá",
        "lịch thi đấu",
        "đang diễn ra",
        "phiên bản mới nhất",
        "release mới",
        "ceo hiện tại",
        "chủ tịch hiện tại",
        "tổng thống hiện tại",
    )
    return any(marker in q for marker in current_markers)

def _search_web_sync(query: str, max_results: int, news: bool) -> List[Dict[str, Any]]:
    ddgs = DDGS(timeout=10)
    if news:
        raw = ddgs.news(query, region="vn-vi", max_results=max_results)
    else:
        raw = ddgs.text(query, region="vn-vi", max_results=max_results)
    return list(raw or [])

async def search_web(query: str, news: bool = False) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(
            _search_web_sync, query, WEB_MAX_RESULTS, news
        )
    except Exception as exc:
        logger.warning("Web search failed for %r: %s", query, exc)
        # Fallback region because some engines may reject vn-vi.
        try:
            def fallback() -> List[Dict[str, Any]]:
                ddgs = DDGS(timeout=10)
                if news:
                    return list(ddgs.news(query, max_results=WEB_MAX_RESULTS) or [])
                return list(ddgs.text(query, max_results=WEB_MAX_RESULTS) or [])

            return await asyncio.to_thread(fallback)
        except Exception as fallback_exc:
            logger.exception("Web search fallback failed: %s", fallback_exc)
            return []

def format_web_context(results: List[Dict[str, Any]], news: bool = False) -> str:
    chunks = [
        "Các kết quả tìm kiếm web bên dưới được đánh số [1], [2], ... . "
        "Khi trả lời, nếu dùng một kết quả cụ thể thì có thể nhắc số nguồn tương ứng."
    ]
    for i, item in enumerate(results, start=1):
        title = str(item.get("title") or "Không tiêu đề").strip()
        body = str(item.get("body") or item.get("description") or "").strip()
        url = str(item.get("url") or item.get("href") or "").strip()
        date = str(item.get("date") or "").strip()
        source = str(item.get("source") or "").strip()

        line = f"[{i}] {title}"
        if date:
            line += f" | {date}"
        if source:
            line += f" | {source}"
        if body:
            line += f"\nTóm tắt: {body}"
        if url:
            line += f"\nURL: {url}"
        chunks.append(line)

    return "\n\n".join(chunks)

def format_source_list(results: List[Dict[str, Any]]) -> str:
    lines = []
    for i, item in enumerate(results, start=1):
        title = str(item.get("title") or "Nguồn").strip()
        url = str(item.get("url") or item.get("href") or "").strip()
        if url:
            lines.append(f"[{i}] {title}\n{url}")
    if not lines:
        return ""
    return "\n\nNguồn web:\n" + "\n".join(lines)

async def answer_with_web(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> bool:
    news = looks_like_news_query(query)
    results = await search_web(query, news=news)
    if not results:
        return False

    web_context = format_web_context(results, news=news)
    answer = await call_text_ai(context, query, extra_context=web_context)
    answer += format_source_list(results)
    await send_long_message(update, answer)
    return True

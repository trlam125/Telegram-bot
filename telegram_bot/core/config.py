"""Environment-backed settings, shared AI client and constants."""
import os
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
).strip()

TEXT_MODEL = os.getenv(
    "NVIDIA_TEXT_MODEL", "nvidia/nemotron-3-super-120b-a12b"
).strip()
VISION_MODEL = os.getenv(
    "NVIDIA_VISION_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
).strip()
OMNI_MODEL = os.getenv(
    "NVIDIA_OMNI_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
).strip()

MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "16"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2048"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.5"))
MAX_DOCUMENT_CHARS = int(os.getenv("MAX_DOCUMENT_CHARS", "30000"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))
WEB_MAX_RESULTS = int(os.getenv("WEB_MAX_RESULTS", "5"))
AUTO_WEB_SEARCH = os.getenv("AUTO_WEB_SEARCH", "true").lower() in {"1", "true", "yes", "on"}
AUTO_WEATHER = os.getenv("AUTO_WEATHER", "true").lower() in {"1", "true", "yes", "on"}
DEFAULT_WEATHER_CITY = os.getenv("DEFAULT_WEATHER_CITY", "").strip()
CLEAR_SCREEN_MAX_MESSAGES = int(os.getenv("CLEAR_SCREEN_MAX_MESSAGES", "300"))
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "20"))
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "120"))
VIDEO_FALLBACK_FRAMES = int(os.getenv("VIDEO_FALLBACK_FRAMES", "8"))
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "20"))
MEDIA_REQUEST_TIMEOUT = int(os.getenv("MEDIA_REQUEST_TIMEOUT", "180"))

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    (
        "Bạn là một trợ lý AI mini hoạt động trong Telegram. "
        "Ưu tiên trả lời bằng tiếng Việt nếu người dùng dùng tiếng Việt. "
        "Trả lời rõ ràng, chính xác, hữu ích và dễ hiểu. "
        "Nếu là câu hỏi lập trình, hãy đưa ví dụ code khi phù hợp. "
        "Khi có nội dung tài liệu đi kèm, dùng tài liệu làm ngữ cảnh khi câu hỏi liên quan. "
        "Khi có dữ liệu thời tiết hoặc kết quả tìm kiếm web đi kèm, đó là dữ liệu bên ngoài mới hơn "
        "kiến thức nền của bạn; hãy ưu tiên dữ liệu đó. Không tự bịa dữ liệu thời gian thực. "
        "Nếu không chắc chắn, hãy nói rõ thay vì bịa thông tin. "
        "Khi trả lời cho Telegram, không dùng ký hiệu Markdown để nhấn mạnh như **, __ hoặc tiêu đề #; "
        "ưu tiên văn bản thuần và dấu • khi cần liệt kê."
    ),
)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN trong file .env")

if not NVIDIA_API_KEY:
    raise RuntimeError("Thiếu NVIDIA_API_KEY trong file .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-nvidia-ai-v4.2")

client = AsyncOpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)

SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}
SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

WMO_WEATHER_CODES = {
    0: "Trời quang",
    1: "Chủ yếu quang",
    2: "Có mây rải rác",
    3: "Nhiều mây",
    45: "Sương mù",
    48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn vừa",
    55: "Mưa phùn dày",
    56: "Mưa phùn đóng băng nhẹ",
    57: "Mưa phùn đóng băng mạnh",
    61: "Mưa nhẹ",
    63: "Mưa vừa",
    65: "Mưa lớn",
    66: "Mưa đóng băng nhẹ",
    67: "Mưa đóng băng mạnh",
    71: "Tuyết nhẹ",
    73: "Tuyết vừa",
    75: "Tuyết lớn",
    77: "Hạt tuyết",
    80: "Mưa rào nhẹ",
    81: "Mưa rào vừa",
    82: "Mưa rào mạnh",
    85: "Mưa tuyết nhẹ",
    86: "Mưa tuyết mạnh",
    95: "Dông",
    96: "Dông kèm mưa đá nhẹ",
    99: "Dông kèm mưa đá mạnh",
}

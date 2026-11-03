# Telegram AI Bot — cấu trúc module

Dự án Telegram Bot hoàn chỉnh đã được tái cấu trúc thành các module. Không thay API, database schema hay cách deploy Vercel. **Không cần tạo bot Telegram hoặc Supabase mới.**

## 1. Cấu trúc dự án

```text
Telegram-bot/
├── app.py                         # Entrypoint Vercel: app
├── bot.py                         # Giữ lệnh chạy cũ: python bot.py (polling)
├── web_app.py                     # Giữ lệnh cũ: uvicorn web_app:app
├── set_webhook.py                 # Giữ lệnh cũ: python set_webhook.py URL
├── telegram_bot/
│   ├── application.py             # Tạo Application, đăng ký lệnh và message handlers
│   ├── core/
│   │   ├── config.py              # Cấu hình .env và NVIDIA client
│   │   └── state.py               # Trạng thái chat trong Supabase
│   ├── handlers/
│   │   ├── commands.py            # /start /stop /clear /reset /notify_on /notify_off ...
│   │   └── messages.py            # Chat, ảnh, PDF, DOCX, video, audio, vị trí
│   ├── services/
│   │   ├── ai.py                  # Gọi NVIDIA AI text/vision/audio/video
│   │   ├── search.py              # Tìm kiếm DDGS
│   │   ├── weather.py             # Open-Meteo, định dạng dự báo
│   │   ├── documents.py           # PDF/DOCX/TXT/MD/CSV
│   │   ├── media_processing.py    # FFmpeg (media helpers)
│   │   ├── storage.py             # Lưu file Telegram Storage
│   │   ├── notifications.py       # Chế độ chỉ chủ bot nhận thông báo
│   │   └── text.py                # Làm sạch/chia tin nhắn dài
│   ├── api/
│   │   ├── web.py                # FastAPI + webhook /telegram/webhook
│   │   ├── gateway.py            # Các route /gateway/v1
│   │   ├── gateway_service.py    # Xác thực, gửi cảnh báo, chống trùng
│   │   ├── gateway_media.py      # Kiểm tra file/URL media
│   │   ├── staged.py             # API staged upload
│   │   ├── staged_storage.py     # Supabase private storage transit
│   │   ├── auth_diagnostics.py   # Logging lỗi xác thực gateway
│   │   └── cron.py               # /cron/daily-weather
│   └── db/
│       ├── __init__.py           # supabase_db: facade chung
│       ├── client.py             # HTTP PostgREST client
│       ├── users.py              # Users và conversation state
│       ├── files.py              # Metadata file
│       ├── updates.py            # Chống xử lý lại Telegram update
│       ├── notifications.py      # Đăng ký nhận cảnh báo và thời tiết
│       └── gateway.py            # Gateway events và staged uploads
├── scripts/set_webhook.py
├── examples/
├── tests/
├── migrations/001_bot_broadcast_settings.sql
├── START_HERE.md
├── migrations/README.md
├── .env.example
├── requirements.txt
└── vercel.json
```

## 2. Những gì được giữ nguyên

- Telegram commands: `/start`, `/stop`, `/clear`, `/reset`, `/help`, `/web`, `/weather`, `/weather_on`, `/weather_off`, `/notify_off`, `/notify_on`.
- `/clear` xóa các tin nhắn Telegram có thể xóa **không gửi tin xác nhận**; không xóa lịch sử/ngữ cảnh AI.
- `/notify_off`: chỉ `TELEGRAM_OWNER_ID` còn nhận **cảnh báo gateway và bản tin thời tiết**. Người khác vẫn có thể chat AI; `/notify_on` bật lại gửi cho tất cả người đã đăng ký. Chỉ chủ bot được đổi trạng thái.
- AI text, nhận dạng ảnh, PDF/DOCX, âm thanh, video, tìm kiếm web, vị trí và dự báo Open-Meteo.
- Gateway API: `POST /gateway/v1/notify`, `POST /gateway/v1/fall`, `POST /gateway/v1/media`, `POST /gateway/v1/media/upload`, staged upload và cron.
- Entrypoint `app.py`, cấu hình Vercel, secrets `.env` và dữ liệu Supabase hiện tại.

## 3. Thay project cũ bằng project mới

**Khuyến nghị:** Xem `START_HERE.md` trước khi thay thế project cũ; sao lưu `.env` và Git. Giải nén **toàn bộ ZIP** vào thư mục mới, không chỉ chép một vài file Python vào project cũ: rất nhiều module đã được di chuyển.

Nếu đang dùng repo Git, có thể thay toàn bộ file source trong working tree sau khi backup và commit trên một nhánh riêng (`refactor/telegram-bot`). Giữ lại thư mục `.git` và file `.env` cá nhân, không commit secret. Xóa các file root cũ đã được chuyển vào `telegram_bot/`:

```text
daily_weather.py
gateway.py
gateway_auth_diagnostics.py
gateway_media.py
gateway_staged.py
gateway_staged_storage.py
media_utils.py
notification_controls.py
supabase_db.py
telegram_storage.py
```

Đừng xóa `app.py`, `bot.py`, `web_app.py`, `set_webhook.py` vì đó là các entrypoint tương thích. Xem chi tiết ở `DEPLOY.md`.

## 4. Chạy thử trên Windows / PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # chỉ khi chưa có file .env thật
```

Điền token/secret thật vào `.env` cục bộ (không đưa lên GitHub).

**Chỉ chọn một chế độ:**

- Local polling: `python bot.py` (ngắt hoặc xóa webhook cũ trước, tránh chạy đồng thời với webhook).
- Webhook server: `python -m uvicorn app:app --host 0.0.0.0 --port 8000`.
- Thiết lập lại webhook sau khi deploy: `python set_webhook.py https://TEN-DOMAIN-VERCEL`.

Không cần chạy script webhook nếu URL `/telegram/webhook` không đổi và webhook đang hoạt động bình thường.

## 5. Kiểm thử

```powershell
python -m pytest -q
python -m compileall -q telegram_bot app.py bot.py web_app.py set_webhook.py
```

`pytest` cần được cài trong môi trường phát triển (`python -m pip install pytest`). Có 11 bài kiểm thử tự động cho cấu trúc, endpoint/cron, danh sách lệnh, /clear, subscriber và Supabase repository. Khi kiểm tra môi trường thiếu Telegram SDK, route import smoke đã được thực hiện bằng *stub* SDK, **không** thay thế kiểm thử tích hợp Telegram/Vercel/Supabase thực tế.

## 6. Các sửa lỗi nhỏ đi kèm

- Khôi phục `supabase_db.set_alert_subscription(...)`: hàm bị thiếu trong file gốc dù `/start`, `/stop` và gateway đều gọi đến.
- Sửa tên helper `bytes_to_data_uri` không tồn tại tại fallback xử lý âm thanh thành `image_to_data_uri`.
- Phân tách `gateway_service.py` để `staged.py` không còn import vòng từ router gateway.

Không thay schema Supabase, không thay tên environment variables và không ghi/xóa dữ liệu production trong quá trình tái cấu trúc.

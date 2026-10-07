# Telegram AI Bot - Vercel + Supabase + Telegram Storage

Bản này dùng kiến trúc production không cần máy cá nhân chạy liên tục:

```text
Telegram user
    |
    v
Telegram webhook
    |
    v
Vercel FastAPI Function
    |---------------------> NVIDIA / Web / Weather
    |
    +----> Supabase PostgreSQL
    |        - user
    |        - history/state
    |        - document context
    |        - metadata file
    |        - webhook deduplication
    |
    +----> Telegram private channel
             - image
             - PDF/DOCX
             - video
             - audio/voice
```

Sau khi deploy và set webhook, có thể tắt VS Code, PowerShell và máy tính; bot vẫn chạy trên Vercel.

## 1. Các file dành cho Vercel

- `app.py`: entrypoint FastAPI mà Vercel tự nhận diện.
- `vercel.json`: đặt thời gian chạy tối đa 300 giây cho Function.
- `.vercelignore`: loại secrets/cache/file local khỏi bundle.
- `web_app.py`: webhook FastAPI thực tế.
- `supabase_db.py`: state/history/metadata qua Supabase.
- `telegram_storage.py`: copy media sang private Telegram channel.

`Dockerfile` và `.dockerignore` cũ được giữ lại để tương thích nếu sau này muốn dùng container host khác; Vercel không cần hai file này.

## 2. Supabase

Trong Supabase -> SQL Editor, chạy toàn bộ `supabase_schema.sql` một lần.

Cần lấy:

```text
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
```

Không đưa secret key lên GitHub.

## 3. Telegram Storage

Bot phải là Administrator của private channel storage.

Biến môi trường cần có:

```text
TELEGRAM_STORAGE_CHAT_ID=-100xxxxxxxxxx
```

File thật nằm trong Telegram channel. Supabase chỉ lưu metadata và `storage_message_id`/`file_id`.

## 4. Đưa project lên GitHub

Upload project lên một repo GitHub.

Không upload:

```text
.env
.venv/
bot_data.pkl
```

`.gitignore` và `.vercelignore` đã chặn các file local phổ biến.

Cấu trúc quan trọng:

```text
app.py
bot.py
web_app.py
supabase_db.py
telegram_storage.py
media_utils.py
requirements.txt
vercel.json
supabase_schema.sql
.gitignore
.vercelignore
.env.example
```

## 5. Import vào Vercel - không cần cài gì trên máy

1. Mở Vercel trên trình duyệt.
2. `Add New -> Project`.
3. Import repo GitHub của bot.
4. Vercel tự nhận FastAPI từ `app.py`.
5. Không cần Build Command.
6. Không cần Output Directory.
7. Thêm Environment Variables trước khi deploy.

Các biến bắt buộc:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_STORAGE_CHAT_ID
WEBHOOK_SECRET_TOKEN
SUPABASE_URL
SUPABASE_SECRET_KEY
NVIDIA_API_KEY
```

Các biến đang có sẵn giá trị mặc định nhưng có thể thêm để cấu hình:

```text
NVIDIA_BASE_URL
NVIDIA_TEXT_MODEL
NVIDIA_VISION_MODEL
NVIDIA_OMNI_MODEL
MAX_HISTORY_MESSAGES
MAX_TOKENS
TEMPERATURE
MAX_DOCUMENT_CHARS
MAX_FILE_MB
MAX_VIDEO_MB
MAX_VIDEO_SECONDS
VIDEO_FALLBACK_FRAMES
MAX_AUDIO_MB
MEDIA_REQUEST_TIMEOUT
AUTO_WEB_SEARCH
AUTO_WEATHER
WEB_MAX_RESULTS
DEFAULT_WEATHER_CITY
CLEAR_SCREEN_MAX_MESSAGES
```

Không cần biến `PORT` trên Vercel.

## 6. Deploy

Bấm `Deploy` trên Vercel.

Sau khi thành công sẽ có URL dạng:

```text
https://ten-project.vercel.app
```

Mở URL gốc, kết quả mong đợi:

```json
{"ok":true,"service":"telegram-ai-bot","mode":"webhook"}
```

Health check:

```text
https://ten-project.vercel.app/healthz
```

Kết quả:

```json
{"ok":true}
```

## 7. Set Telegram webhook không cần cài CLI

Webhook của project là:

```text
https://ten-project.vercel.app/telegram/webhook
```

Mở trình duyệt và gọi Telegram Bot API một lần:

```text
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://ten-project.vercel.app/telegram/webhook&secret_token=<WEBHOOK_SECRET_TOKEN>&drop_pending_updates=true
```

Thay `<BOT_TOKEN>` và `<WEBHOOK_SECRET_TOKEN>` bằng giá trị thật.

Kết quả mong đợi:

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

Không chia sẻ URL chứa bot token. Nếu lo lịch sử trình duyệt, mở tab riêng tư/incognito rồi đóng tab sau khi set xong.

Kiểm tra webhook:

```text
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

`url` phải là URL `/telegram/webhook` của Vercel và không nên có `last_error_message`.

## 8. Test production

Không chạy `python bot.py` cùng lúc với webhook production.

Trên Telegram thử:

```text
/start
```

Sau đó thử:

- chat AI
- `/weather Ha Noi`
- `/web ...`
- gửi ảnh
- gửi PDF/DOCX
- gửi video nhỏ
- gửi voice/audio

Kiểm tra đồng thời:

- bot vẫn trả lời khi máy cá nhân tắt;
- file xuất hiện trong private Telegram storage channel;
- Supabase `bot_files` có metadata;
- Supabase `bot_user_state` có state/history.

## 9. Local polling vẫn dùng được

Nếu muốn quay lại test local, trước hết xóa webhook rồi mới chạy polling:

```powershell
python delete_webhook.py
python bot.py
```

Sau khi test xong cần set lại webhook production.

## 10. Lưu ý Vercel

- FastAPI chạy dưới dạng một Vercel Function.
- Hobby + Fluid Compute có giới hạn tối đa 300 giây mỗi invocation.
- Bundle Python phải nằm trong giới hạn của Vercel; `requirements.txt` đã bỏ `uvicorn[standard]` vì Vercel không cần Uvicorn để serve FastAPI.
- File local trên Function là tạm thời; project này không dùng local disk làm persistence.
- File dài hạn ở Telegram Storage, state/database ở Supabase.
- Nếu một video/audio xử lý vượt giới hạn Function thì request có thể timeout; khi đó cần giảm giới hạn media hoặc tách media worker ở giai đoạn sau.

## 11. Bảo mật

Tuyệt đối không commit:

```text
.env
TELEGRAM_BOT_TOKEN
NVIDIA_API_KEY
SUPABASE_SECRET_KEY
SUPABASE_SERVICE_ROLE_KEY
WEBHOOK_SECRET_TOKEN
```

Nếu secret đã từng bị push vào repo public, phải rotate/revoke secret cũ chứ chỉ thêm `.gitignore` là chưa đủ.

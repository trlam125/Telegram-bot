# Telegram AI Bot - Telegram Storage + Supabase + Cloud Run

Ban nay da duoc chuyen sang kien truc production:

```text
Telegram user
    |
    v
Telegram webhook
    |
    v
Google Cloud Run (Python/FastAPI)
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
             - anh
             - PDF/DOCX
             - video
             - audio/voice
```

May tinh ca nhan co the tat sau khi deploy. Cloud Run nhan webhook truc tiep tu Telegram.

## 1. Da thay doi gi?

- Bo `PicklePersistence` / `bot_data.pkl` khoi runtime.
- `context.user_data` duoc load/save tu Supabase sau moi update.
- Anh, document, video, audio/voice duoc copy vao private Telegram Storage Channel.
- Metadata file duoc ghi vao bang `bot_files` cua Supabase.
- Them webhook server `web_app.py` cho Cloud Run.
- Them co che `bot_updates` de tranh Telegram webhook retry lam AI xu ly hai lan.
- Van giu `python bot.py` de test local bang polling.

## 2. Tao database Supabase

1. Tao/open Supabase project.
2. Vao `SQL Editor`.
3. Copy toan bo noi dung `supabase_schema.sql` va bam Run.
4. Vao `Settings -> API Keys` / `Connect`.
5. Lay:
   - Project URL -> `SUPABASE_URL`
   - Secret key dang `sb_secret_...` -> `SUPABASE_SECRET_KEY`

Khong dua secret key len GitHub.

Sau khi chay SQL se co 4 bang:

- `bot_users`
- `bot_user_state`
- `bot_files`
- `bot_updates`

## 3. Cau hinh `.env`

Copy `.env.example` thanh `.env`:

```powershell
Copy-Item .env.example .env
```

Dien cac bien quan trong:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_STORAGE_CHAT_ID=-100...
WEBHOOK_SECRET_TOKEN=mot_chuoi_ngau_nhien

SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...

NVIDIA_API_KEY=nvapi-...
```

`WEBHOOK_SECRET_TOKEN` chi nen gom chu, so, `_` va `-`.

## 4. Test local truoc khi deploy

Neu bot dang co webhook cu, xoa webhook truoc:

```powershell
python delete_webhook.py
```

Cai dependencies:

```powershell
python -m pip install -r requirements.txt
```

Chay:

```powershell
python bot.py
```

Test:

1. `/start`
2. Chat vai cau.
3. Tat bot, chay lai, hoi tiep -> history/state van lay tu Supabase.
4. Gui anh/PDF/video/audio -> file phai xuat hien trong `Lam's storage`.
5. Supabase `bot_files` phai co metadata file.

`bot_data.pkl` cu khong con duoc doc/ghi.

## 5. Deploy Google Cloud Run

Can cai Google Cloud CLI va dang nhap:

```powershell
gcloud auth login
gcloud config set project YOUR_GOOGLE_CLOUD_PROJECT_ID
```

Project co san `Dockerfile`, nen dung Cloud Run source deploy truc tiep.

Lenh mau PowerShell (thay cac gia tri `...`):

```powershell
gcloud run deploy telegram-ai-bot --source . --region asia-southeast1 --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300 --max-instances 1 --concurrency 10 --set-env-vars "TELEGRAM_BOT_TOKEN=...,TELEGRAM_STORAGE_CHAT_ID=-100...,WEBHOOK_SECRET_TOKEN=...,SUPABASE_URL=https://xxxxx.supabase.co,SUPABASE_SECRET_KEY=sb_secret_...,NVIDIA_API_KEY=nvapi-...,NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1,NVIDIA_TEXT_MODEL=nvidia/nemotron-3-super-120b-a12b,NVIDIA_VISION_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning,NVIDIA_OMNI_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
```

`--max-instances 1` duoc de san cho bot hien tai vi state cua cung mot user nen duoc xu ly tuan tu. Sau nay neu can scale lon hon thi nen bo sung distributed locking/queue.

Sau khi deploy, lay URL:

```powershell
$URL = gcloud run services describe telegram-ai-bot --region asia-southeast1 --format="value(status.url)"
$URL
```

Vi du:

```text
https://telegram-ai-bot-xxxxx-as.a.run.app
```

## 6. Dang ky Telegram webhook

Tren may local, `.env` phai co cung `TELEGRAM_BOT_TOKEN` va `WEBHOOK_SECRET_TOKEN` da deploy.

Chay:

```powershell
python set_webhook.py $URL
```

Neu thanh cong se thay:

```text
'ok': True
Webhook: https://.../telegram/webhook
```

Tu luc nay Telegram gui message thang den Cloud Run. Ban co the tat may tinh.

## 7. Kiem tra sau khi deploy

Mo URL Cloud Run tren browser:

```text
https://YOUR_CLOUD_RUN_URL/healthz
```

Ket qua:

```json
{"ok": true}
```

Sau do nhan bot tren Telegram:

```text
/start
```

Thu tiep:

- chat AI
- `/weather Ha Noi`
- `/web ...`
- gui anh
- gui PDF
- gui video nho
- gui voice/audio

Kiem tra dong thoi:

- file nam trong `Lam's storage`
- `bot_files` co record moi
- `bot_user_state` co state cua user

## 8. Chuyen ve test local sau khi da deploy

Telegram khong the dung webhook va polling cho cung mot bot cung luc.

Truoc khi chay local:

```powershell
python delete_webhook.py
python bot.py
```

Sau khi test xong, dang ky lai webhook:

```powershell
python set_webhook.py https://YOUR_CLOUD_RUN_URL
```

## 9. File quan trong

```text
bot.py                  Logic bot + Supabase state
supabase_db.py          Async Supabase Data API client
telegram_storage.py     Copy media -> private Telegram channel
web_app.py              FastAPI webhook server cho Cloud Run
supabase_schema.sql     Schema database
set_webhook.py          Dang ky webhook production
delete_webhook.py       Xoa webhook de test polling local
Dockerfile              Container Cloud Run
.env.example            Mau bien moi truong
```

## Bao mat

Khong commit:

- `.env`
- `TELEGRAM_BOT_TOKEN`
- `NVIDIA_API_KEY`
- `SUPABASE_SECRET_KEY`
- legacy `SUPABASE_SERVICE_ROLE_KEY`

`.env` da nam trong `.gitignore` va `.dockerignore`.

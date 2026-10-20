# Telegram AI Bot + Notification Gateway

Dự án gốc vẫn giữ bot AI (NVIDIA), web search, thời tiết, phân tích PDF / DOCX / ảnh / video / audio, lưu file vào Telegram Storage và lưu state vào Supabase. Bản nâng cấp này bổ sung **gateway gửi thông báo Telegram dùng chung cho nhiều repository**.

## 1. Mô hình kết nối

```text
Fall Detection     Flood Detection    IoT / Spring Boot      Repo khác
      |                   |                  |                 |
      +-------------------+------------------+-----------------+
                              |
                   HTTPS + X-Gateway-Key
                              |
                   Telegram Gateway (FastAPI)
                    + Supabase subscribers
                              |
                       Telegram Bot API
                              |
                   Tất cả người dùng /start
```

- Chỉ **repository Telegram-bot** cấu hình `TELEGRAM_BOT_TOKEN`, database và `TELEGRAM_STORAGE_CHAT_ID` (biến thứ hai là kênh lưu media, KHÔNG phải danh sách người nhận cảnh báo).
- Mỗi repository bên ngoài chỉ cần **2 biến**: `TELEGRAM_GATEWAY_URL` và `TELEGRAM_GATEWAY_KEY`, không cần token hay `chat_id`.
- Người dùng bấm `/start` trong **chat riêng** để nhận toàn bộ thông báo gateway. `/stop` ngừng nhận, vẫn dùng AI bình thường. `/start` lại để bật nhận.
- Những người đã bấm `/start` trước khi cập nhật được đăng ký qua câu lệnh migration trong SQL, nếu state `started=true` vẫn được lưu trong Supabase.
- Các project bên ngoài mặc định gửi tới **cùng danh sách người nhận**. Nếu sau này cần mỗi project có một nhóm người đăng ký riêng, phải bổ sung lọc chủ đề/quyền nhận.

## 2. Cập nhật Supabase (BẮT BUỘC)

1. Mở Supabase → **SQL Editor** → New query.
2. Chạy **toàn bộ** file `supabase_schema.sql`. Các câu `CREATE TABLE IF NOT EXISTS` không xóa dữ liệu đã lưu. Cuối file tạo bảng `bot_alert_subscribers`, `bot_gateway_events` và backfill người từng `/start`.
3. Backend phải sử dụng `SUPABASE_SECRET_KEY` hoặc `SUPABASE_SERVICE_ROLE_KEY`, không sử dụng public anon key.
4. Bảng có bật RLS, chỉ backend dùng secret/service key để đọc/ghi.

## 3. Cấu hình & deploy

### Các biến môi trường của Telegram-bot

Giữ nguyên biến cũ trong `.env.example` (Telegram, Supabase, NVIDIA, Storage, webhook). Bổ sung:

```env
# Các API key khác nhau cho từng repository (ngăn một repo biết key của repo khác)
GATEWAY_API_KEYS=fall_detection:YOUR_FALL_RANDOM_KEY;flood_detection:YOUR_FLOOD_RANDOM_KEY;iot:YOUR_IOT_RANDOM_KEY
GATEWAY_MAX_RECIPIENTS=1000
GATEWAY_MESSAGES_PER_SECOND=12
```

**Hoặc** đơn giản dùng `GATEWAY_API_KEY=YOUR_SINGLE_RANDOM_KEY` thay cho `GATEWAY_API_KEYS` (client sẽ hiển thị nguồn `default`).

Tạo ngẫu nhiên mỗi key bằng:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Không commit `.env` hoặc key vào GitHub. Các tên project trong `GATEWAY_API_KEYS` là nhãn nguồn hiển thị ở cuối thông báo, không phải tên Telegram bot. Đặt key ở server env của từng project, không đưa vào JavaScript chạy trong browser hoặc ứng dụng di động public (ai có key đều có thể gửi thông báo cho tất cả người đăng ký).

### Deploy Vercel (project hiện dùng FastAPI webhook)

1. Deploy repository lên Vercel, entrypoint `app.py` sử dụng `web_app.py` như trước.
2. Vào Vercel → Settings → Environment Variables; khai báo biến cũ và `GATEWAY_API_KEYS` rồi **redeploy**.
3. Đảm bảo webhook Telegram vẫn trỏ đến `https://YOUR-DEPLOYMENT/telegram/webhook`. Nếu đổi domain, từ máy có `.env` hợp lệ chạy:

   ```bash
   python set_webhook.py https://YOUR-DEPLOYMENT
   ```

4. Bot phải hoạt động bình thường khi gõ `/start`; sau đó `/stop` rồi `/start` để xác nhận đã đăng ký.
5. Kiểm tra gateway bằng các ví dụ mục 4. **Không cần đăng ký webhook lần nữa chỉ vì bổ sung API gateway nếu domain không đổi.**

**Chạy backend server thông thường / Docker:**

```bash
pip install -r requirements.txt
uvicorn web_app:app --host 0.0.0.0 --port 8080
```

Hoặc: `docker build -t telegram-gateway .` và chạy container có truyền file biến môi trường. Chạy polling `python bot.py` chỉ phục vụ chat AI, **không mở API gateway**; để dùng gateway phải chạy FastAPI / webhook, tránh chạy polling đồng thời với webhook cùng token.

## 4. Gọi gateway từ BẤT KỲ repository nào

Các endpoint đều cần HTTPS và header `X-Gateway-Key`.

### A. Thông báo tổng quát

`POST https://YOUR-DEPLOYMENT/gateway/v1/notify`

```json
{
  "title": "Hệ thống giám sát ngập",
  "message": "Mực nước tại Camera 02 đã vượt ngưỡng.",
  "level": "warning",
  "event_id": "flood-cam02-20261020-001"
}
```

- `message`: bắt buộc, tối đa 3500 ký tự.
- `title`: tùy chọn, tối đa 120 ký tự.
- `level`: `info`, `warning`, `critical`.
- `event_id`: tùy chọn; nếu truyền cùng ID 2 lần **từ cùng source key**, lần thứ hai không gửi trùng. Hãy đặt ID riêng cho mỗi sự kiện thực tế, không dùng cùng một ID cho mọi cảnh báo.
- Người gửi **không truyền `chat_id` hay Telegram token**; gateway tự tìm người nhận trong Supabase.

Ví dụ `curl` (bash/Linux):

```bash
curl -X POST "https://YOUR-DEPLOYMENT/gateway/v1/notify" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: YOUR_PROJECT_KEY" \
  -d '{"title":"Kiểm tra gateway","message":"Thông báo thử","level":"info","event_id":"test-001"}'
```

Ví dụ **PowerShell** (Windows):

```powershell
$headers = @{ "X-Gateway-Key" = $env:TELEGRAM_GATEWAY_KEY }
$body = @{ title = "Test"; message = "Hello from PowerShell"; level = "info" } | ConvertTo-Json
Invoke-RestMethod -Uri "$env:TELEGRAM_GATEWAY_URL/gateway/v1/notify" -Method Post -Headers $headers -ContentType "application/json" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

### B. Cảnh báo phát hiện ngã

`POST https://YOUR-DEPLOYMENT/gateway/v1/fall`

```json
{
  "camera": "Camera 01",
  "event_time": "2026-10-20 20:15:30",
  "location": "Phòng khách",
  "event_id": "fall-cam01-1729458930"
}
```

`camera` có mặc định `Camera 01`, `event_time` để trống sẽ dùng giờ UTC từ server, `location` tùy chọn. Gateway tự định dạng cảnh báo quan trọng và gửi tới tất cả người `/start`.

### C. Dùng Python (không cần SDK Telegram)

Copy file [`examples/gateway_client.py`](examples/gateway_client.py) sang repo phát hiện ngã / ngập lụt, rồi cấu hình 2 biến trong **repo nguồn**, không phải repo Telegram-bot:

```env
TELEGRAM_GATEWAY_URL=https://YOUR-DEPLOYMENT
TELEGRAM_GATEWAY_KEY=YOUR_PROJECT_KEY
```

```python
from gateway_client import notify, notify_fall

notify("Mực nước vượt ngưỡng", title="Flood Detection", level="warning")
notify_fall("Camera 01", event_time="2026-10-20 20:15:30", event_id="fall-unique-001")
```

### D. Spring Boot (Java)

Trong repository Spring Boot chỉ khai báo:

```properties
telegram.gateway.url=${TELEGRAM_GATEWAY_URL}
telegram.gateway.key=${TELEGRAM_GATEWAY_KEY}
```

Ví dụ sử dụng `RestClient` (Spring Framework 6.1+):

```java
var response = RestClient.create()
    .post()
    .uri(System.getenv("TELEGRAM_GATEWAY_URL") + "/gateway/v1/notify")
    .header("X-Gateway-Key", System.getenv("TELEGRAM_GATEWAY_KEY"))
    .contentType(MediaType.APPLICATION_JSON)
    .body(Map.of("title", "IoT Smart Home", "message", "Cảm biến báo động", "level", "warning"))
    .retrieve()
    .body(String.class);
```

Không cần thêm thư viện bot Telegram trong các repository nguồn. Việc gửi HTTP vẫn phụ thuộc việc backend gateway được host và reachable.

### API trả về

```json
{
  "ok": true,
  "duplicate": false,
  "source": "fall_detection",
  "event_id": "fall-unique-001",
  "subscribers": 3,
  "sent": 3,
  "failed": 0,
  "blocked": 0
}
```

- `401`: key sai / thiếu; `503`: chưa cấu hình gateway, lỗi database hoặc vượt giới hạn số người nhận; `422`: dữ liệu không hợp lệ.
- `duplicate: true`: request có `event_id` đã ghi nhận nên không phát lại.
- `blocked`: người đã chặn bot (gateway tự tắt đăng ký người đó).
- `ok: false`: ít nhất một lượt gửi lỗi; cần xem log.
- `GET /healthz` có sẵn cho kiểm tra server, không kiểm tra được Telegram/Supabase.

## 5. Lưu ý vận hành / giới hạn hiện tại

- Gateway **xử lý đồng bộ trên cùng HTTP request** và gửi text. Không có job queue, không có retry bền vững sau khi serverless bị timeout. Dùng phù hợp nhóm người nhận nhỏ-vừa; nhiều nghìn người nên dùng hàng đợi/worker và lưu trạng thái gửi theo từng người.
- `event_id` chống lặp bằng bảng Supabase, nhưng nếu request bị ngắt *sau khi đã gửi một phần*, cùng `event_id` sẽ bị chặn để tránh gửi trùng; không tự gửi bù người chưa nhận. Để có bảo đảm exactly-once/per-recipient phải thiết kế delivery queue riêng.
- Telegram có hạn mức gửi và có thể trả lỗi 429; gateway chủ động giảm tốc, retry 429 một lần. Nếu nhiều project gửi đồng thời, tổng lưu lượng có thể vượt giới hạn toàn bot.
- Không cấp gateway key trong frontend. Có key thì bất kỳ server nào giữ key đều có thể broadcast tới mọi người. Muốn hạn chế spam cần bổ sung phân quyền, rate limit theo source hoặc giới hạn IP ngoài gateway.
- Tất cả người bấm `/start` đều nhận **mọi loại thông báo** từ tất cả project kết nối; vì vậy không gửi dữ liệu cá nhân, vị trí/ảnh nhạy cảm nếu chưa có sự đồng ý của người nhận. `/stop` tắt nhận hoàn toàn.
- Bản này không thay đổi cách bot dùng NVIDIA hay media storage. Nếu bạn cần gateway gửi ảnh/video/tệp, có thể bổ sung endpoint mới; endpoint hiện tại chỉ gửi văn bản.

## 6. Các file đã thay đổi

| File | Mục đích |
| --- | --- |
| `bot.py` | `/start` đăng ký cảnh báo, `/stop` bỏ đăng ký, cập nhật Telegram menu và help |
| `supabase_db.py` | CRUD subscription và bảng chống trùng sự kiện |
| `supabase_schema.sql` | Thêm 2 bảng + SQL chuyển người dùng cũ |
| `gateway.py` (mới) | API key theo repository, `/notify`, `/fall`, broadcast và giới hạn tốc độ |
| `web_app.py` | Gắn router gateway vào FastAPI đang có |
| `.env.example` | Mẫu biến cấu hình gateway |
| `examples/gateway_client.py` | Client dùng lại ở các repo ngoài |
| `tests/test_gateway.py` | Kiểm thử gateway không gọi Telegram thật |


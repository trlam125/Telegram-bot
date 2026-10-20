"""Vercel Python Function: restricted FallGuard -> Telegram notifications.

Deploy under api/v1/notify.py in the Telegram-bot Vercel project.
No imports from bot.py: the bot's AI/webhook initialization is unaffected.
Credentials are read exclusively from Vercel environment variables.
"""

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hmac
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import secrets
import urllib.error
import urllib.request

MAX_BODY_BYTES = 2_200_000
MAX_JPEG_BYTES = 1_500_000
MAX_SOURCE_CHARS = 80
JSON_HEADER = 'application/json; charset=utf-8'


def _result(status, code=None):
    return status, {'ok': status == 200, **({'error': code} if code else {})}


def _safe_source(raw):
    if not isinstance(raw, str) or not 1 <= len(raw) <= MAX_SOURCE_CHARS:
        return None
    source = re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
    source = ' '.join(source.split())
    return source or None


def _jpeg_from_base64(value):
    if not isinstance(value, str) or len(value) > ((MAX_JPEG_BYTES + 2) // 3) * 4:
        return None
    if len(value) % 4 or not re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', value):
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(data) < 4 or len(data) > MAX_JPEG_BYTES:
        return None
    if not (data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')):
        return None
    if base64.b64encode(data).decode('ascii') != value:
        return None
    return data


def _telegram_call(token, method, body, headers, *, transport=None):
    """Call fixed Telegram API endpoint; never leak URL/token in responses."""
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=body,
        headers=headers,
        method='POST',
    )
    # Treat redirects as errors rather than sending the bot token to a new host.
    if transport is None:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        transport = urllib.request.build_opener(_NoRedirect()).open
    try:
        with transport(request, timeout=15) as response:
            if getattr(response, 'status', 200) != 200:
                return _result(502, 'telegram_unavailable')
            reply = json.loads(response.read(32_768).decode('utf-8'))
            if isinstance(reply, dict) and reply.get('ok') is True:
                return _result(200)
            return _result(502, 'telegram_rejected')
    except urllib.error.HTTPError as error:
        if error.code == 429:
            return _result(429, 'rate_limited')
        return _result(502, 'telegram_unavailable')
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return _result(502, 'telegram_unavailable')


def _send_text(token, chat_id, message, transport):
    raw = json.dumps({'chat_id': chat_id, 'text': message}, ensure_ascii=False).encode('utf-8')
    return _telegram_call(token, 'sendMessage', raw, {'Content-Type': 'application/json'}, transport=transport)


def _send_photo(token, chat_id, message, photo, transport):
    boundary = '----FallGuard' + secrets.token_hex(16)
    parts = []
    for key, value in [('chat_id', chat_id), ('caption', message[:1024])]:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode('utf-8')
        )
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="fall-alert.jpg"\r\n'
         'Content-Type: image/jpeg\r\n\r\n').encode('ascii')
    )
    parts.append(photo)
    parts.append(f'\r\n--{boundary}--\r\n'.encode('ascii'))
    body = b''.join(parts)
    return _telegram_call(token, 'sendPhoto', body,
                          {'Content-Type': 'multipart/form-data; boundary=' + boundary},
                          transport=transport)


def _header(headers, name):
    # Works with HTTPMessage (Vercel runtime) and ordinary test mappings.
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ''


def process_notification(headers, raw_body, *, env=None, transport=None):
    """Validate one request and forward one restricted event. Easy to unit test."""
    config = os.environ if env is None else env
    bot_token = config.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = config.get('TELEGRAM_CHAT_ID', '')
    expected_key = config.get('GATEWAY_API_KEY', '')
    if not all([bot_token, chat_id, expected_key]) or len(expected_key) < 32:
        return _result(503, 'gateway_unconfigured')

    provided = _header(headers, 'Authorization')
    if not provided.startswith('Bearer ') or not hmac.compare_digest(provided[7:], expected_key):
        return _result(401, 'unauthorized')
    content_type = _header(headers, 'Content-Type').split(';', 1)[0].lower().strip()
    if content_type != 'application/json':
        return _result(415, 'json_required')
    if len(raw_body) > MAX_BODY_BYTES:
        return _result(413, 'too_large')
    try:
        event = json.loads(raw_body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return _result(400, 'invalid_json')
    if not isinstance(event, dict):
        return _result(400, 'invalid_event')

    now = datetime.now(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M:%S')
    photo = None
    if event.get('type') == 'test':
        if set(event) != {'type'}:
            return _result(400, 'invalid_test_event')
        message = f'✅ FallGuard - KIỂM TRA TELEGRAM\nGateway Vercel đã kết nối thành công.\nThời gian: {now}'
    elif event.get('type') == 'fall':
        if not set(event) <= {'type', 'person_id', 'score', 'confirmations', 'source', 'photo_b64'}:
            return _result(400, 'unexpected_field')
        pid, score, confirmations = event.get('person_id'), event.get('score'), event.get('confirmations')
        source = _safe_source(event.get('source'))
        if (type(pid) is not int or not 1 <= pid <= 999999
                or type(score) not in (int, float) or not 0 <= score <= 1
                or type(confirmations) is not int or not 1 <= confirmations <= 20
                or not source):
            return _result(400, 'invalid_fall_event')
        if 'photo_b64' in event:
            photo = _jpeg_from_base64(event['photo_b64'])
            if photo is None:
                return _result(400, 'invalid_photo')
        message = (f'🚨 FALLGUARD — CẢNH BÁO TÉ NGÃ\n'
                   f'Phát hiện dấu hiệu người bị ngã. Hãy kiểm tra ngay.\n'
                   f'Camera: {source}\nNgười trong khung hình: #{pid}\n'
                   f'Điểm model té ngã: {round(score * 100)}%\n'
                   f'Xác nhận liên tiếp: {confirmations} lần, duy trì tư thế ngã ít nhất 5 giây\n'
                   f'Thời gian: {now}\n\nĐây là cảnh báo AI; cần kiểm tra trực tiếp.')
    else:
        return _result(400, 'unsupported_event')
    if photo is not None:
        return _send_photo(bot_token, chat_id, message, photo, transport)
    return _send_text(bot_token, chat_id, message, transport)


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, body):
        encoded = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', JSON_HEADER)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self._reply(405, {'ok': False, 'error': 'method_not_allowed'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            length = -1
        if length < 0:
            self._reply(411, {'ok': False, 'error': 'length_required'})
            return
        if length > MAX_BODY_BYTES:
            self._reply(413, {'ok': False, 'error': 'too_large'})
            return
        body = self.rfile.read(length)
        status, response = process_notification(self.headers, body)
        self._reply(status, response)

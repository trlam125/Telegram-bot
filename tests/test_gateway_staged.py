"""Staged gateway route tests; no Vercel, Supabase or Telegram credentials."""
import io
import os
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Match the existing offline test setup.
if 'telegram.error' not in sys.modules:
    fake_telegram = types.ModuleType('telegram')
    fake_error = types.ModuleType('telegram.error')
    for name in ('Forbidden', 'RetryAfter', 'TelegramError'):
        setattr(fake_error, name, type(name, (Exception,), {}))
    fake_telegram.error = fake_error
    fake_db = types.ModuleType('supabase_db')
    fake_db.supabase_db = object()
    patch.dict(sys.modules, {'telegram': fake_telegram,
                             'telegram.error': fake_error,
                             'supabase_db': fake_db}).start()

import gateway
import gateway_staged

KEY = 'fall-key-is-long-enough-for-tests'


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.users = [101, 102]
        self.claims = 0
        self.records = set()

    async def create_gateway_upload(self, row):
        row = deepcopy(row)
        row['created_at'] = datetime.now(timezone.utc).isoformat()
        self.rows[(row['source'], row['id'])] = row

    async def get_gateway_upload(self, source, upload_id):
        return deepcopy(self.rows.get((source, upload_id)))

    async def claim_gateway_upload(self, source, upload_id):
        row = self.rows[(source, upload_id)]
        if row['status'] not in ('pending', 'failed'):
            return None
        row['status'] = 'processing'
        self.claims += 1
        return deepcopy(row)

    async def update_gateway_upload(self, source, upload_id, patch):
        self.rows[(source, upload_id)].update(patch)

    async def list_alert_subscribers(self):
        return self.users

    async def list_stale_gateway_uploads(self, limit=30):
        return []


class FakeBot:
    def __init__(self):
        self.records = []
        self.last_storage = 0

    async def send_document(self, **kw):
        self.records.append(kw)
        if kw['chat_id'] == -1001234567890:
            self.last_storage += 1
            return SimpleNamespace(document=SimpleNamespace(file_id='BQACAgAAA1234567890987654321'),
                                   message_id=600 + self.last_storage)
        return SimpleNamespace(document=SimpleNamespace(file_id='BQACAgAAA1234567890987654321'))


class StagedTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.bot = FakeBot()
        self.app = FastAPI()
        self.app.state.telegram_app = SimpleNamespace(bot=self.bot)
        self.app.include_router(gateway.router)
        self.client = TestClient(self.app)
        fake_storage = types.ModuleType('telegram_storage')
        fake_storage.TELEGRAM_STORAGE_CHAT_ID = -1001234567890
        self.patches = [
            patch.object(gateway_staged, 'supabase_db', self.db),
            patch.object(gateway, 'supabase_db', self.db),
            patch.dict(os.environ, {'GATEWAY_API_KEYS': 'fall_detection:' + KEY,
                                    'GATEWAY_API_KEY': '', 'GATEWAY_MESSAGES_PER_SECOND': '20'}),
            patch.dict(sys.modules, {'telegram_storage': fake_storage}),
        ]
        async def fake_sign(_):
            return 'https://project.supabase.co/storage/v1/object/upload/sign/bucket/file?token=opaque'
        async def fake_download(path, declared_size, media_type, filename):
            return io.BytesIO(b'hello')
        async def fake_delete(path):
            self.deleted.append(path)
        self.deleted = []
        self.patches.extend([
            patch.object(gateway_staged, 'create_signed_upload', fake_sign),
            patch.object(gateway_staged, 'download_transit', fake_download),
            patch.object(gateway_staged, 'delete_transit', fake_delete),
        ])
        for p in self.patches:
            p.start()
        sys.modules['telegram_storage'].TELEGRAM_STORAGE_CHAT_ID = -1001234567890
        self.headers = {'X-Gateway-Key': KEY}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def prepare(self, **kw):
        return self.client.post('/gateway/v1/media/staged/prepare',
                                json={'filename': 'report.pdf', 'file_size': 5,
                                      'media_type': 'document', **kw}, headers=self.headers)

    def test_complete_archives_once_broadcasts_file_id(self):
        prepared = self.prepare(event_id='event-17')
        self.assertEqual(prepared.status_code, 200, prepared.text)
        upload_id = prepared.json()['upload_id']
        resp = self.client.post(f'/gateway/v1/media/staged/{upload_id}/complete', headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()['sent'], 2)
        self.assertEqual([x['chat_id'] for x in self.bot.records], [-1001234567890, 101, 102])
        self.assertEqual([x['document'] for x in self.bot.records[1:]], ['BQACAgAAA1234567890987654321']*2)
        self.assertEqual(self.bot.last_storage, 1)
        self.assertEqual(len(self.deleted), 1)
        again = self.client.post(f'/gateway/v1/media/staged/{upload_id}/complete', headers=self.headers)
        self.assertTrue(again.json()['duplicate'])
        self.assertEqual(self.bot.last_storage, 1)

    def test_unauthorized_key_and_cross_source(self):
        self.assertEqual(self.client.post('/gateway/v1/media/staged/prepare',
                json={'filename':'x.pdf', 'file_size':5,'media_type':'document'},
                headers={'X-Gateway-Key':'bad'}).status_code, 401)
        uid = self.prepare().json()['upload_id']
        self.assertEqual(self.client.get(f'/gateway/v1/media/staged/{uid}',
                                        headers={'X-Gateway-Key':'bad'}).status_code, 401)

    def test_bad_photo_size(self):
        response = self.prepare(media_type='photo', file_size=9_000_001)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.db.rows)

    def test_competing_complete_requests_only_one_claims(self):
        uid = self.prepare().json()['upload_id']
        self.db.rows[('fall_detection', uid)]['status'] = 'processing'
        response = self.client.post(f'/gateway/v1/media/staged/{uid}/complete', headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.bot.records, [])

    def test_status_route(self):
        uid = self.prepare().json()['upload_id']
        self.assertEqual(self.client.get(f'/gateway/v1/media/staged/{uid}',headers=self.headers).json()['status'],'pending')

    def test_missing_file_fails_without_broadcast(self):
        async def missing(*args):
            raise gateway_staged.TransitError('File not found')
        uid = self.prepare().json()['upload_id']
        with patch.object(gateway_staged, 'download_transit', missing):
            response = self.client.post(f'/gateway/v1/media/staged/{uid}/complete',headers=self.headers)
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.db.rows[('fall_detection', uid)]['status'],'failed')
        self.assertEqual(self.bot.records,[])

    def test_existing_file_id_skip_second_archive_after_retry(self):
        uid = self.prepare().json()['upload_id']
        self.db.rows[('fall_detection', uid)].update({'telegram_file_id':'BQACAgAAA1234567890987654321','status':'failed'})
        response = self.client.post(f'/gateway/v1/media/staged/{uid}/complete',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.bot.last_storage,0)
        self.assertEqual(len(self.bot.records),2)

if __name__ == '__main__':
    unittest.main()

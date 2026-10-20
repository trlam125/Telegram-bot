"""Test actual Supabase Storage REST payload shapes with an in-memory HTTP transport."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

import httpx

if 'supabase_db' not in sys.modules:
    stub = types.ModuleType('supabase_db')
    stub.supabase_db = types.SimpleNamespace(url='https://unit.supabase.co', key='unit-service-key')
    patch.dict(sys.modules, {'supabase_db': stub}).start()

import gateway_staged_storage as storage


class StorageRESTTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.content = b'0123456789'
        def handler(request):
            self.calls.append(request)
            path = request.url.path
            if request.method == 'POST' and '/object/upload/sign/' in path:
                return httpx.Response(200, json={'url': path.replace('/storage/v1', '') + '?token=TOKEN'})
            if request.method == 'GET' and '/object/authenticated/' in path:
                return httpx.Response(200, content=self.content)
            if request.method == 'DELETE' and path.endswith('/object/telegram-gateway-temp'):
                return httpx.Response(200, json=[])
            return httpx.Response(404)
        self.original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        self.patch = patch.object(storage.httpx, 'AsyncClient',
                                  side_effect=lambda **kwargs: self.original_client(transport=transport, **kwargs))
        self.patch.start()
        self.dbpatch = patch.object(storage, 'supabase_db', types.SimpleNamespace(
            url='https://unit.supabase.co', key='unit-service-key'))
        self.dbpatch.start()
        self.env = patch.dict(os.environ, {'GATEWAY_TEMP_BUCKET': 'telegram-gateway-temp'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dbpatch.stop()
        self.patch.stop()

    def test_sign_and_delete(self):
        result = asyncio.run(storage.create_signed_upload('fall_detection/id123/a.pdf'))
        self.assertEqual(result, 'https://unit.supabase.co/storage/v1/object/upload/sign/telegram-gateway-temp/fall_detection/id123/a.pdf?token=TOKEN')
        self.assertEqual(self.calls[0].method, 'POST')
        self.assertEqual(self.calls[0].headers['apikey'], 'unit-service-key')
        asyncio.run(storage.delete_transit('fall_detection/id123/a.pdf'))
        self.assertEqual(self.calls[-1].method, 'DELETE')
        self.assertEqual(self.calls[-1].read(), b'{"prefixes":["fall_detection/id123/a.pdf"]}')

    def test_stream_download_with_exact_byte_count(self):
        result = asyncio.run(storage.download_transit('fall_detection/id123/a.pdf', 10, 'document', 'a.pdf'))
        try:
            self.assertTrue(result.name.endswith('.pdf'))
            self.assertEqual(result.read(), self.content)
        finally:
            result.close()

    def test_detect_size_mismatch(self):
        with self.assertRaises(storage.TransitError):
            asyncio.run(storage.download_transit('fall_detection/id123/a.pdf', 8, 'document', 'a.pdf'))

    def test_reject_oversized_photo_before_downloading(self):
        with self.assertRaises(storage.TransitError):
            asyncio.run(storage.download_transit('s/i/photo.jpg', 9_000_001, 'photo', 'photo.jpg'))
        self.assertEqual(self.calls, [])

if __name__ == '__main__':
    unittest.main()

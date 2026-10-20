"""Supabase private bucket transit: no Telegram credentials leave the gateway.

A project receives only a time-limited upload URL; the server alone can read and
remove objects in the private bucket. Uses official Supabase Storage REST routes.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from supabase_db import supabase_db

DEFAULT_BUCKET = 'telegram-gateway-temp'
MAX_TRANSIT_BYTES = 49_000_000  # Leave room below Telegram's normal 50 MB document cap.
MAX_PHOTO_TRANSIT_BYTES = 9_000_000


class TransitError(Exception):
    pass


def _bucket() -> str:
    bucket = os.getenv('GATEWAY_TEMP_BUCKET', DEFAULT_BUCKET).strip()
    if not bucket or not all(c.isascii() and (c.isalnum() or c in '-_') for c in bucket):
        raise TransitError('Invalid GATEWAY_TEMP_BUCKET')
    return bucket


def _url(suffix: str) -> str:
    return f'{supabase_db.url}/storage/v1/{suffix}'


def _path(path: str) -> str:
    # Paths are server generated, never supplied by API callers.
    return '/'.join(quote(seg, safe='') for seg in path.split('/'))


def _headers() -> dict[str, str]:
    return {'apikey': supabase_db.key, 'Authorization': f'Bearer {supabase_db.key}'}


async def create_signed_upload(path: str) -> str:
    endpoint = _url(f'object/upload/sign/{_bucket()}/{_path(path)}')
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(endpoint, json={}, headers=_headers())
        response.raise_for_status()
        data = response.json()
    relative = data.get('url') or data.get('signedUrl')
    if not isinstance(relative, str) or not relative:
        raise TransitError('Supabase did not return a signed upload URL')
    if relative.startswith('/storage/v1/'):
        url = supabase_db.url + relative
    elif relative.startswith('/object/'):
        url = _url(relative.lstrip('/'))
    else:
        url = urljoin(_url(''), relative)
    # Guard against unexpected redirect hosts or wrong object paths.
    actual, expected = urlsplit(url), urlsplit(endpoint)
    if (actual.scheme != 'https' or actual.netloc != expected.netloc
            or actual.path != expected.path or not actual.query.startswith('token=')):
        raise TransitError('Supabase returned an unexpected signed URL')
    return url


async def download_transit(path: str, declared_size: int, media_type: str, filename: str):
    """Stream private object into a temporary file; bounded memory use."""
    max_size = MAX_PHOTO_TRANSIT_BYTES if media_type == 'photo' else MAX_TRANSIT_BYTES
    if declared_size > max_size:
        raise TransitError('Declared file exceeds media size limit')
    suffix = Path(filename).suffix[:12] or '.bin'
    spool = tempfile.NamedTemporaryFile(mode='w+b', prefix='gw-', suffix=suffix)
    url = _url(f'object/authenticated/{_bucket()}/{_path(path)}')
    size = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=10.0)) as client:
            async with client.stream('GET', url, headers=_headers()) as response:
                if response.status_code == 404:
                    raise TransitError('File not found in temporary bucket; upload it first')
                response.raise_for_status()
                for_content_length = response.headers.get('content-length')
                if for_content_length and int(for_content_length) > max_size:
                    raise TransitError('Stored file too large')
                async for chunk in response.aiter_bytes(262144):
                    size += len(chunk)
                    if size > max_size or size > declared_size:
                        raise TransitError('Stored file exceeds declared size')
                    spool.write(chunk)
        if not size or size != declared_size:
            raise TransitError(f'Stored file size does not match declared size ({size}/{declared_size})')
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise


async def delete_transit(path: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request('DELETE', _url(f'object/{_bucket()}'),
                                        json={'prefixes': [path]}, headers=_headers())
        response.raise_for_status()

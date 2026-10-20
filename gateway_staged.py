"""Phase 1: signed Supabase upload -> private Telegram storage -> broadcast file_id.

Does not change /gateway/v1/notify, /fall, /media or /media/upload.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from gateway import Level, MediaType, Notification, _authenticated_source, _broadcast_media, _format_notification
from gateway_media import MEDIA_METHODS, clean_filename
from gateway_staged_storage import (
    MAX_PHOTO_TRANSIT_BYTES, MAX_TRANSIT_BYTES, TransitError,
    create_signed_upload, delete_transit, download_transit,
)
from supabase_db import supabase_db

logger = logging.getLogger('telegram-gateway.staged')
router = APIRouter(prefix='/media/staged', tags=['Staged media uploads'])


class PrepareUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=255)
    file_size: int = Field(ge=1, le=MAX_TRANSIT_BYTES)
    media_type: MediaType
    message: str = Field(default='Tệp đính kèm', max_length=3500)
    title: str | None = Field(default=None, max_length=120)
    level: Level = 'info'
    event_id: str | None = Field(default=None, min_length=1, max_length=128,
                                 pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')


def _file_id(message, media_type: str) -> str:
    media = getattr(message, media_type, None)
    if media_type == 'photo':
        media = (media or [])[-1] if media else None
    ident = getattr(media, 'file_id', None)
    if not ident:
        raise TransitError('Telegram storage did not return a file_id')
    return ident


def _created_at(row: dict) -> datetime:
    return datetime.fromisoformat(str(row['created_at']).replace('Z', '+00:00'))


async def _require_upload(source: str, upload_id: UUID) -> dict:
    try:
        row = await supabase_db.get_gateway_upload(source, str(upload_id))
    except Exception as exc:
        logger.exception('Unable to read upload record')
        raise HTTPException(503, 'Upload database unavailable') from exc
    if not row:
        raise HTTPException(404, 'Upload not found')
    return row


@router.post('/prepare')
async def prepare_upload(
    payload: PrepareUpload,
    x_gateway_key: str | None = Header(default=None),
):
    source = _authenticated_source(x_gateway_key)
    if payload.media_type == 'photo' and payload.file_size > MAX_PHOTO_TRANSIT_BYTES:
        raise HTTPException(413, 'Photo exceeds 9 MB limit; send as document if appropriate')
    # Telegram voice/video/audio/animation have additional format restrictions.
    filename = clean_filename(payload.filename, payload.media_type)
    upload_id = str(uuid4())
    path = f'{source}/{upload_id}/{filename}'
    row = {
        'id': upload_id, 'source': source, 'object_path': path,
        'filename': filename, 'file_size': payload.file_size,
        'media_type': payload.media_type, 'message': payload.message,
        'title': payload.title, 'level': payload.level,
        'event_id': payload.event_id, 'status': 'pending',
    }
    try:
        await supabase_db.create_gateway_upload(row)
    except Exception as exc:
        # Unique index on (source,event_id) blocks repeated preparations.
        if getattr(getattr(exc, 'response', None), 'status_code', None) in (409, 23505):
            raise HTTPException(409, 'event_id already used; reuse the original upload_id') from exc
        logger.exception('Cannot create staged upload')
        raise HTTPException(503, 'Upload database unavailable') from exc
    try:
        url = await create_signed_upload(path)
    except Exception as exc:
        logger.exception('Cannot sign staged upload')
        try:
            await supabase_db.update_gateway_upload(source, upload_id, {'status': 'failed'})
        except Exception:
            logger.exception('Could not mark failed signing attempt')
        raise HTTPException(503, 'Supabase signed upload URL unavailable') from exc

    return {
        'upload_id': upload_id, 'upload_url': url, 'method': 'PUT',
        'max_bytes': MAX_PHOTO_TRANSIT_BYTES if payload.media_type == 'photo' else MAX_TRANSIT_BYTES,
        'expires_in_seconds': 7200,
        'next': f'/gateway/v1/media/staged/{upload_id}/complete',
    }


@router.get('/{upload_id}')
async def upload_status(upload_id: UUID, x_gateway_key: str | None = Header(default=None)):
    source = _authenticated_source(x_gateway_key)
    row = await _require_upload(source, upload_id)
    return {k: row.get(k) for k in (
        'id', 'source', 'status', 'media_type', 'file_size', 'event_id',
        'sent', 'failed', 'blocked', 'telegram_file_id', 'storage_message_id',
    )}


@router.post('/{upload_id}/complete')
async def complete_upload(
    request: Request, upload_id: UUID,
    x_gateway_key: str | None = Header(default=None),
):
    source = _authenticated_source(x_gateway_key)
    row = await _require_upload(source, upload_id)
    if row['status'] in ('done', 'partial'):
        return {'ok': row['status'] == 'done', 'duplicate': True, 'upload_id': str(upload_id),
                'status': row['status'], 'sent': row.get('sent', 0),
                'failed': row.get('failed', 0), 'blocked': row.get('blocked', 0)}
    if row['status'] == 'pending' and (datetime.now(timezone.utc) - _created_at(row)) > timedelta(hours=2):
        raise HTTPException(410, 'Signed upload expired; prepare a new upload')
    # This conditional PostgREST PATCH atomically claims the job in the database.
    try:
        claimed = await supabase_db.claim_gateway_upload(source, str(upload_id))
    except Exception as exc:
        logger.exception('Cannot claim staged upload')
        raise HTTPException(503, 'Upload database unavailable') from exc
    if not claimed:
        raise HTTPException(409, 'Upload is already processing or complete; inspect GET status')

    file_id = claimed.get('telegram_file_id')
    sent = failed = blocked = 0
    try:
        if not file_id:
            spool = await download_transit(claimed['object_path'],
                                           int(claimed['file_size']), claimed['media_type'], claimed['filename'])
            try:
                # Archive once in Lam's storage. The only Telegram credentials live here.
                from telegram_storage import TELEGRAM_STORAGE_CHAT_ID
                method, param = MEDIA_METHODS[claimed['media_type']]
                caption = f'Gateway archive | {source} | {upload_id}'
                kwargs = {'chat_id': TELEGRAM_STORAGE_CHAT_ID, param: spool,
                          'caption': caption}
                stored = await getattr(request.app.state.telegram_app.bot, method)(**kwargs)
                file_id = _file_id(stored, claimed['media_type'])
                await supabase_db.update_gateway_upload(source, str(upload_id), {
                    'telegram_file_id': file_id, 'storage_message_id': stored.message_id,
                    'status': 'processing',
                })
            finally:
                spool.close()
        # Broadcast using the single reusable Telegram file_id, not N uploads.
        result = await _broadcast_media(
            request, source=source, media_type=claimed['media_type'], data=file_id,
            message=claimed['message'], title=claimed['title'], level=claimed['level'],
            event_id=None,
        )
        sent, failed, blocked = result['sent'], result['failed'], result['blocked']
        status = 'done' if failed == 0 else 'partial'
        await supabase_db.update_gateway_upload(source, str(upload_id), {
            'status': status, 'sent': sent, 'failed': failed, 'blocked': blocked,
        })
        try:
            await delete_transit(claimed['object_path'])
            await supabase_db.update_gateway_upload(source, str(upload_id), {'temp_deleted': True})
        except Exception:
            logger.warning('Temporary cleanup deferred for staged upload %s', upload_id)
        return {'ok': result['ok'], 'duplicate': False, 'upload_id': str(upload_id),
                'storage_message_id': stored.message_id if 'stored' in locals() else claimed.get('storage_message_id'),
                'status': status, 'sent': sent, 'failed': failed, 'blocked': blocked}
    except TransitError as exc:
        await _mark_failed(source, str(upload_id))
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        await _mark_failed(source, str(upload_id))
        raise
    except Exception as exc:
        logger.exception('Staged upload processing failed: id=%s', upload_id)
        await _mark_failed(source, str(upload_id))
        raise HTTPException(503, 'Staged delivery failed; check status before retry') from exc


async def _mark_failed(source: str, upload_id: str):
    try:
        await supabase_db.update_gateway_upload(source, upload_id, {'status': 'failed'})
    except Exception:
        logger.exception('Failed to mark staged upload %s', upload_id)


async def _cleanup_expired():
    """Delete at most 30 abandoned files; keep database tombstones."""
    try:
        rows = await supabase_db.list_stale_gateway_uploads(limit=30)
    except Exception as exc:
        logger.exception('Cannot inspect stale uploads')
        raise HTTPException(503, 'Upload database unavailable') from exc
    deleted = 0
    for row in rows:
        try:
            await delete_transit(row['object_path'])
            # Keep metadata tombstones for dedup; do not delete SQL records.
            await supabase_db.update_gateway_upload(row['source'], row['id'],
                                                   {'temp_deleted': True})
            deleted += 1
        except Exception:
            logger.exception('Cannot cleanup staged upload %s', row['id'])
    return {'checked': len(rows), 'deleted': deleted}


@router.post('/admin/cleanup')
async def cleanup_manual(x_gateway_key: str | None = Header(default=None)):
    """Use a project API key to run a manual cleanup of expired transit files."""
    _authenticated_source(x_gateway_key)
    return await _cleanup_expired()


@router.get('/admin/cleanup')
async def cleanup_cron(authorization: str | None = Header(default=None)):
    """Called daily by Vercel Cron; requires its CRON_SECRET authorization."""
    cron_secret = os.getenv('CRON_SECRET', '').strip()
    if not cron_secret or not authorization or not secrets.compare_digest(
        authorization, f'Bearer {cron_secret}'
    ):
        raise HTTPException(401, 'Cron authorization required')
    return await _cleanup_expired()

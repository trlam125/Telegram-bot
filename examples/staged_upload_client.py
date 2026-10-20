"""Stream a file to the private Supabase staging bucket without Telegram or Supabase credentials.

Usage (PowerShell):
  $env:TELEGRAM_GATEWAY_URL='https://your-bot.vercel.app'
  $env:TELEGRAM_GATEWAY_KEY='YOUR_PROJECT_KEY'
  python examples/staged_upload_client.py 'C:\\Videos\\clip.mp4' --type video --message 'Camera 01'
"""
import argparse
import mimetypes
import os
from pathlib import Path

import httpx


def stream_file(file, block=256 * 1024):
    while True:
        chunk = file.read(block)
        if not chunk:
            break
        yield chunk


def main():
    parser = argparse.ArgumentParser(description='Gateway upload large files via Supabase signed URL')
    parser.add_argument('file', type=Path)
    parser.add_argument('--type', dest='media_type', default='document',
                        choices=('photo', 'video', 'document', 'audio', 'voice', 'animation'))
    parser.add_argument('--message', default='Tệp đính kèm')
    parser.add_argument('--title', default=None)
    parser.add_argument('--level', default='info', choices=('info', 'warning', 'critical'))
    parser.add_argument('--event-id', default=None)
    args = parser.parse_args()

    url = os.environ.get('TELEGRAM_GATEWAY_URL', '').rstrip('/')
    key = os.environ.get('TELEGRAM_GATEWAY_KEY', '')
    if not url.startswith('https://') or not key:
        parser.error('Set TELEGRAM_GATEWAY_URL (https://...) and TELEGRAM_GATEWAY_KEY')
    if not args.file.is_file():
        parser.error(f'Not a file: {args.file}')
    size = args.file.stat().st_size
    headers = {'X-Gateway-Key': key}
    with httpx.Client(timeout=httpx.Timeout(180, connect=10), follow_redirects=False) as client:
        response = client.post(url + '/gateway/v1/media/staged/prepare', headers=headers, json={
            'filename': args.file.name, 'file_size': size, 'media_type': args.media_type,
            'message': args.message, 'title': args.title, 'level': args.level,
            'event_id': args.event_id,
        })
        response.raise_for_status()
        job = response.json()
        signed_url = job['upload_url']
        print('Prepared upload:', job['upload_id'])
        # Signed URL is a bearer capability; don't print it or send Gateway API key to Supabase.
        mime = mimetypes.guess_type(str(args.file))[0] or 'application/octet-stream'
        with args.file.open('rb') as stream:
            upload = client.put(signed_url, content=stream_file(stream),
                                headers={'Content-Type': mime, 'Content-Length': str(size)})
            upload.raise_for_status()
        print('Uploaded file directly to Supabase Storage')
        final = client.post(url + job['next'], headers=headers)
        final.raise_for_status()
        print('Telegram delivery:', final.json())
        print('Check status later: ' + url + '/gateway/v1/media/staged/' + job['upload_id'])


if __name__ == '__main__':
    main()

"""Disk-based body cache for Lark email messages."""

import base64
import gzip
import json
import os
import tempfile
import threading
from datetime import datetime, timezone

try:
    from .utils import (
        _DISK_BODY_CACHE_DIR,
        _attachment_cacheable,
        _disk_cache_budget_bytes,
        _log_exception,
    )
except ImportError:
    from utils import (
        _DISK_BODY_CACHE_DIR,
        _attachment_cacheable,
        _disk_cache_budget_bytes,
        _log_exception,
    )

_DISK_BODY_CACHE_MAX_ENTRY_BYTES = 4 * 1024 * 1024


def load_disk_body(cache_key):
    """Load a cached message body from disk.

    Returns (html, text, attachments) or None if not found / corrupted.
    """
    path = _DISK_BODY_CACHE_DIR / f'{cache_key}.json.gz'
    try:
        if not path.exists():
            return None
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            payload = json.load(f)
        try:
            path.touch()
        except Exception:
            pass
        attachments = []
        for att in payload.get('attachments', []):
            data = base64.b64decode(att.get('data_b64', '') or b'') if att.get('data_b64') else b''
            attachments.append({
                'attachment_id': att.get('attachment_id'),
                'attachment_type': att.get('attachment_type'),
                'name': att.get('name', 'attachment'),
                'size': att.get('size', 0),
                'content_type': att.get('content_type', 'application/octet-stream'),
                'disposition': att.get('disposition', 'attachment'),
                'content_id': att.get('content_id'),
                'data': data,
            })
        return payload.get('html'), payload.get('text'), attachments
    except Exception as e:
        _log_exception(f'Disk body cache read failed ({cache_key})', e)
        try:
            path.unlink()
        except Exception:
            pass
        return None


def store_disk_body(cache_key, html, text, attachments, msg_date=None):
    """Write a message body to disk cache in a background thread."""
    def run():
        try:
            _DISK_BODY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            serial = []
            for att in attachments or []:
                item = {
                    'attachment_id': att.get('attachment_id'),
                    'attachment_type': att.get('attachment_type'),
                    'name': att.get('name', 'attachment'),
                    'size': att.get('size', 0),
                    'content_type': att.get('content_type', 'application/octet-stream'),
                    'disposition': att.get('disposition', 'attachment'),
                    'content_id': att.get('content_id'),
                }
                if _attachment_cacheable(att):
                    item['data_b64'] = base64.b64encode(att.get('data', b'')).decode('ascii')
                serial.append(item)
            payload = {
                'html': html,
                'text': text,
                'attachments': serial,
                'message_date': (msg_date.isoformat() if msg_date else ''),
                'saved_at': datetime.now(timezone.utc).isoformat(),
            }
            encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            if len(encoded) > _DISK_BODY_CACHE_MAX_ENTRY_BYTES:
                return
            path = _DISK_BODY_CACHE_DIR / f'{cache_key}.json.gz'
            fd, tmp_path = tempfile.mkstemp(
                prefix=f'{cache_key}.',
                suffix='.tmp',
                dir=_DISK_BODY_CACHE_DIR,
            )
            try:
                with os.fdopen(fd, 'wb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb') as f:
                        f.write(encoded)
                os.replace(tmp_path, path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            prune_disk_body_cache()
        except Exception as e:
            _log_exception(f'Disk body cache write failed ({cache_key})', e)

    threading.Thread(target=run, daemon=True).start()


def prune_disk_body_cache():
    """Enforce the disk cache budget by removing the oldest entries."""
    try:
        if not _DISK_BODY_CACHE_DIR.exists():
            return
        budget_bytes = _disk_cache_budget_bytes()
        files = []
        total = 0
        for path in _DISK_BODY_CACHE_DIR.glob('*.json.gz'):
            try:
                stat = path.stat()
            except Exception:
                continue
            files.append((stat.st_mtime, path, stat.st_size))
            total += stat.st_size
        files.sort(key=lambda item: item[0])
        while total > budget_bytes and files:
            _, victim, size = files.pop(0)
            try:
                total -= size
                victim.unlink()
            except Exception:
                pass
    except Exception as e:
        _log_exception('Disk body cache prune failed', e)

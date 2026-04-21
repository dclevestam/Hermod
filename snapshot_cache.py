"""Message-list snapshot helpers."""

import gzip
import json
import os
import tempfile
import threading
from datetime import datetime, timezone

try:
    from .utils import _snapshot_path
except ImportError:
    from utils import _snapshot_path


def build_snapshot_payload(scope, accounts, msgs, default_folder):
    return {
        'scope': scope,
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'accounts': list(accounts or []),
        'messages': [
            {
                'uid': msg.get('uid', ''),
                'subject': msg.get('subject', '(no subject)'),
                'sender_name': msg.get('sender_name', ''),
                'sender_email': msg.get('sender_email', ''),
                'to_addrs': list(msg.get('to_addrs', [])),
                'cc_addrs': list(msg.get('cc_addrs', [])),
                'date': (msg.get('date').isoformat() if msg.get('date') else ''),
                'is_read': msg.get('is_read', True),
                'has_attachments': msg.get('has_attachments', False),
                'snippet': msg.get('snippet', ''),
                'folder': msg.get('folder', default_folder),
                'backend': msg.get('backend', ''),
                'account': msg.get('account', ''),
                'thread_id': msg.get('thread_id', ''),
                'thread_source': msg.get('thread_source', ''),
            }
            for msg in (msgs or [])[:100]
        ],
    }


def load_snapshot_payload(scope):
    path = _snapshot_path(scope)
    if not path.exists():
        return None
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            payload = json.load(f)
    except (OSError, EOFError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        # Corrupt or truncated snapshot (e.g. power loss mid-write).
        # Remove the bad file so we don't poison every subsequent
        # startup with the same error.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if isinstance(payload, dict):
        return payload
    return None


def store_snapshot_payload(scope, payload):
    path = _snapshot_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    fd, tmp_path = tempfile.mkstemp(
        prefix=f'{path.stem}.',
        suffix='.tmp',
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, 'wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb') as gz:
                gz.write(encoded)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def snapshot_result_applicable(requested_generation, current_generation, live_generation):
    if requested_generation is None:
        return current_generation is None
    if current_generation is not None and requested_generation != current_generation:
        return False
    if live_generation is not None and requested_generation <= live_generation:
        return False
    return True


class SnapshotSaveQueue:
    def __init__(self, writer=None, error_logger=None):
        self._writer = writer or store_snapshot_payload
        self._error_logger = error_logger
        self._lock = threading.Lock()
        self._pending = {}
        self._worker_running = False

    def enqueue(self, scope, payload):
        with self._lock:
            self._pending[scope] = payload
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _pop_pending(self):
        with self._lock:
            if not self._pending:
                self._worker_running = False
                return None
            scope = next(iter(self._pending))
            payload = self._pending.pop(scope)
            return scope, payload

    def _run(self):
        while True:
            item = self._pop_pending()
            if item is None:
                return
            scope, payload = item
            try:
                self._writer(scope, payload)
            except Exception as exc:
                if callable(self._error_logger):
                    self._error_logger(f'Snapshot save failed ({scope})', exc)

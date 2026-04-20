"""Message-list snapshot loading and body prefetch behavior."""

import threading
from datetime import datetime, timezone

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from .settings import get_settings
    from .snapshot_cache import (
        build_snapshot_payload,
        load_snapshot_payload,
        snapshot_result_applicable,
    )
    from .utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _DISK_BODY_CACHE_DIR,
        _body_cache_key,
        _snapshot_scope, _snapshot_path,
        _backend_for_identity,
        _log_exception, _perf_counter, _log_perf,
    )
    from .window_constants import BODY_CACHE_LIMIT, PREFETCH_WARMUP_LIMIT
except ImportError:
    from settings import get_settings
    from snapshot_cache import (
        build_snapshot_payload,
        load_snapshot_payload,
        snapshot_result_applicable,
    )
    from utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _DISK_BODY_CACHE_DIR,
        _body_cache_key,
        _snapshot_scope, _snapshot_path,
        _backend_for_identity,
        _log_exception, _perf_counter, _log_perf,
    )
    from window_constants import BODY_CACHE_LIMIT, PREFETCH_WARMUP_LIMIT


class MessageListCacheMixin:
    def _prefetch_bodies(self, msgs):
        if not msgs or not self._should_seed_recent_cache():
            return
        generation = self._prefetch_generation
        budget_mb = get_settings().get('disk_cache_budget_mb')
        limit = max(1, min(PREFETCH_WARMUP_LIMIT, budget_mb // 16 or 1))
        ordered = sorted(
            list(msgs),
            key=lambda m: m.get('date') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:limit]
        self._prefetch_bodies_for_messages(ordered, generation)

    def _prefetch_bodies_for_messages(self, msgs, generation=None):
        if not msgs:
            return
        if generation is None:
            generation = self._prefetch_generation

        def run():
            for candidate in msgs:
                if self._prefetch_generation != generation:
                    return
                backend = candidate.get('backend_obj')
                if not backend:
                    continue
                uid = candidate.get('uid')
                folder = candidate.get('folder')
                if not uid or not folder:
                    continue
                cache_key = (backend.identity, folder, uid)
                with self._cache_lock:
                    if cache_key in self._body_cache:
                        continue
                disk_key = _body_cache_key(backend.identity, folder, uid)
                if (_DISK_BODY_CACHE_DIR / f'{disk_key}.json.gz').exists():
                    continue
                try:
                    html, text, attachments = backend.fetch_body(uid, folder)
                    if self._prefetch_generation != generation:
                        return
                    with self._cache_lock:
                        self._body_cache[cache_key] = (html, text, attachments)
                        self._body_cache.move_to_end(cache_key)
                        while len(self._body_cache) > BODY_CACHE_LIMIT:
                            self._body_cache.popitem(last=False)
                    self._store_disk_body(disk_key, html, text, attachments, candidate.get('date'))
                except Exception as exc:
                    _log_exception(f'Prefetch failed ({backend.identity}, {folder}, {uid})', exc)

        threading.Thread(target=run, daemon=True).start()

    def _should_seed_recent_cache(self):
        return self.current_folder in (_UNIFIED, 'INBOX', 'inbox')

    def _message_list_context_key(self, backend=None, folder=None):
        backend = self.current_backend if backend is None else backend
        folder = self.current_folder if folder is None else folder
        if folder in (
            _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
            _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        ):
            return ('unified', folder)
        if backend and folder:
            return (backend.identity, folder)
        return None

    def _snapshot_messages_from_payload(self, records, default_folder, backend_context):
        msgs = []
        for record in records:
            try:
                date_val = record.get('date')
                date = datetime.fromisoformat(date_val) if date_val else datetime.now(timezone.utc)
            except Exception:
                date = datetime.now(timezone.utc)
            account = record.get('account', '')
            backend_obj = (
                backend_context
                if backend_context and account == backend_context.identity
                else _backend_for_identity(self.backends, account)
            )
            msgs.append({
                'uid': record.get('uid', ''),
                'subject': record.get('subject', '(no subject)'),
                'sender_name': record.get('sender_name', ''),
                'sender_email': record.get('sender_email', ''),
                'to_addrs': record.get('to_addrs', []),
                'cc_addrs': record.get('cc_addrs', []),
                'date': date,
                'is_read': record.get('is_read', True),
                'has_attachments': record.get('has_attachments', False),
                'snippet': record.get('snippet', ''),
                'folder': record.get('folder', default_folder),
                'backend': record.get('backend', ''),
                'account': account,
                'thread_id': record.get('thread_id', ''),
                'thread_source': record.get('thread_source', ''),
                'backend_obj': backend_obj,
            })
        return msgs

    def _queue_message_snapshot_load(self, generation=None, preserve_selected_key=None, request_key=None):
        scope = _snapshot_scope(self.current_backend, self.current_folder)
        if not scope:
            return False
        path = _snapshot_path(scope)
        if not path.exists():
            return False
        if request_key is not None and self._displayed_message_list_key == request_key and self._list_stack.get_visible_child_name() == 'list':
            return False
        accounts = sorted(backend.identity for backend in self.backends)
        backend_context = self.current_backend
        default_folder = self.current_folder
        op = self._start_background_op(
            'load snapshot',
            scope,
            'gzip/json snapshot read or stale snapshot apply',
        )

        def load():
            started = _perf_counter()
            try:
                payload = load_snapshot_payload(scope)
            except Exception as exc:
                _log_exception(f'Snapshot load failed ({scope})', exc)
                return
            finally:
                GLib.idle_add(self._end_background_op, op)
            if not payload:
                return
            stored_accounts_raw = payload.get('accounts')
            if scope == 'unified-inbox' and not stored_accounts_raw:
                return
            stored_accounts = sorted(stored_accounts_raw or [])
            if stored_accounts and stored_accounts != accounts:
                return
            records = list(payload.get('messages', []))

            def apply():
                if not snapshot_result_applicable(
                    generation,
                    self._message_load_generation,
                    self._message_live_generation,
                ):
                    return False
                msgs = self._snapshot_messages_from_payload(records, default_folder, backend_context)
                self._set_messages(
                    msgs,
                    generation,
                    preserve_selected_key,
                    'snapshot',
                    False,
                    False,
                )
                _log_perf('snapshot load', f'{scope} {len(msgs)} msgs', started=started)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=load, daemon=True).start()
        return True

    def _store_message_snapshot(self, msgs):
        scope = _snapshot_scope(self.current_backend, self.current_folder)
        if not scope:
            return
        started = _perf_counter()
        accounts = [backend.identity for backend in self.backends]
        default_folder = self.current_folder
        queued_msgs = [dict(msg) for msg in (msgs or [])[:100]]
        payload = build_snapshot_payload(scope, accounts, queued_msgs, default_folder)
        self._snapshot_save_queue.enqueue(scope, payload)
        _log_perf('snapshot save', f'{scope} {len(queued_msgs)} msgs queued', started=started)

"""Reader fetch, thread loading, and body cache orchestration."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from .backends import network_ready, is_transient_network_error
    from .body_cache import load_disk_body, prune_disk_body_cache, store_disk_body
    from .settings import get_settings
    from .utils import (
        _log_exception, _body_cache_key,
        _backend_for_identity, _backend_for_message,
        _perf_counter, _log_perf,
    )
    from .window_constants import BODY_CACHE_LIMIT
except ImportError:
    from backends import network_ready, is_transient_network_error
    from body_cache import load_disk_body, prune_disk_body_cache, store_disk_body
    from settings import get_settings
    from utils import (
        _log_exception, _body_cache_key,
        _backend_for_identity, _backend_for_message,
        _perf_counter, _log_perf,
    )
    from window_constants import BODY_CACHE_LIMIT


class ReaderControllerMixin:
    def _reader_backend_for_message(self, msg):
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        return backend

    def _load_body(self, msg, generation=None):
        backend = self._reader_backend_for_message(msg)
        uid = msg['uid']
        folder = msg.get('folder')
        backend_identity = backend.identity if backend is not None else (msg.get('account') or 'unknown')
        op = self._start_background_op(
            'load body',
            f'{backend_identity}/{folder}/{uid}',
            'backend fetch_body, IMAP lock contention, or network latency',
        )

        def fetch():
            try:
                html, text, attachments = self._read_message_body_payload(msg)
                GLib.idle_add(self._set_body, msg, html, text, attachments, generation)
                if get_settings().get('mark_read_on_open') and not msg.get('is_read') and backend is not None:
                    try:
                        backend.mark_as_read(uid, folder)
                        msg['is_read'] = True
                        self._sync_backend_cached_read_state(msg, True)
                    except Exception:
                        pass
            except Exception as exc:
                if is_transient_network_error(exc) or not network_ready():
                    self._offline_body_pending = True
                    if self._current_body is None:
                        GLib.idle_add(self._show_loading_viewer)
                else:
                    _log_exception(f'Load body failed ({backend_identity}, {folder}, {uid})', exc)
                    if self._current_body is not None:
                        GLib.idle_add(self._show_toast, f'Failed to load message: {exc}')
                    else:
                        GLib.idle_add(self._set_body_error, str(exc), generation)
            finally:
                GLib.idle_add(self._end_background_op, op)

        threading.Thread(target=fetch, daemon=True).start()

    def _demo_thread_payload(self, msg):
        thread_msgs = list(msg.get('thread_members') or [])
        records = []
        attachments = []
        selected_uid = msg.get('uid')
        total = len(thread_msgs)
        for thread_msg in thread_msgs:
            thread_msg = dict(thread_msg)
            thread_msg['thread_count'] = total
            thread_msg['thread_key'] = self._thread_key_for_msg(thread_msg)
            records.append({
                'msg': thread_msg,
                'html': None,
                'text': thread_msg.get('body_text') or thread_msg.get('snippet') or '',
                'attachments': thread_msg.get('attachments') or [],
                'body_text': self._extract_thread_body(None, thread_msg.get('body_text') or thread_msg.get('snippet') or ''),
                'inline_images': [],
                'selected': thread_msg.get('uid') == selected_uid,
            })
            for att in thread_msg.get('attachments') or []:
                att_copy = dict(att)
                att_copy['source_msg'] = thread_msg
                attachments.append(att_copy)
        return records, attachments

    def _load_thread_view(self, msg, generation=None):
        backend = self._reader_backend_for_message(msg)
        thread_id = (msg.get('thread_id') or '').strip()
        if msg.get('thread_source') == 'demo' and msg.get('thread_members'):
            records, attachments = self._demo_thread_payload(msg)
            GLib.idle_add(self._render_thread_view, msg, records, attachments, generation)
            return
        if not backend or not thread_id:
            self._load_body(msg, generation)
            return
        op = self._start_background_op(
            'load thread',
            f'{backend.identity}/{thread_id}',
            'backend thread fetch, body fetches, or mailbox latency',
        )
        if self._current_body is None:
            self._show_loading_viewer()

        def fetch():
            try:
                if not hasattr(backend, 'fetch_thread_messages'):
                    raise AttributeError('thread fetch unavailable')
                thread_msgs = backend.fetch_thread_messages(thread_id) or []
                if not thread_msgs:
                    GLib.idle_add(self._end_background_op, op)
                    GLib.idle_add(self._load_body, msg, generation)
                    return
                selected_uid = msg.get('uid')
                total = len(thread_msgs)
                selected_msg = next((thread_msg for thread_msg in thread_msgs if thread_msg.get('uid') == selected_uid), msg)
                try:
                    selected_html, selected_text, selected_attachments = self._read_message_body_payload(selected_msg)
                except Exception as exc:
                    _log_exception(
                        f'Thread body failed ({backend.identity}, {selected_msg.get("folder")}, {selected_msg.get("uid")})',
                        exc,
                    )
                    selected_html, selected_text, selected_attachments = None, '', []

                partial_records = []
                partial_attachments = []
                for thread_msg in thread_msgs:
                    uid = thread_msg.get('uid')
                    if uid == selected_uid:
                        record = self._thread_record_for_message(
                            thread_msg,
                            total,
                            html=selected_html,
                            text=selected_text,
                            attachments=list(selected_attachments or []),
                            selected=True,
                        )
                        for att in selected_attachments or []:
                            att_copy = dict(att)
                            att_copy['source_msg'] = dict(thread_msg)
                            partial_attachments.append(att_copy)
                    else:
                        record = self._thread_record_for_message(
                            thread_msg,
                            total,
                            html=None,
                            text=thread_msg.get('snippet') or '',
                            attachments=[],
                            selected=False,
                        )
                    partial_records.append(record)
                GLib.idle_add(self._render_thread_view, msg, partial_records, partial_attachments, generation)

                if get_settings().get('mark_read_on_open'):
                    for unread_msg in [thread_msg for thread_msg in thread_msgs if not thread_msg.get('is_read')]:
                        try:
                            backend.mark_as_read(unread_msg['uid'], unread_msg.get('folder'))
                            unread_msg['is_read'] = True
                            self._sync_backend_cached_read_state(unread_msg, True)
                        except Exception:
                            continue
                    if not msg.get('is_read'):
                        msg['is_read'] = True
                        self._sync_backend_cached_read_state(msg, True)

                rest_msgs = [thread_msg for thread_msg in thread_msgs if thread_msg.get('uid') != selected_uid]
                if not rest_msgs:
                    return

                max_workers = min(4, len(rest_msgs))
                fetched = {
                    selected_uid: (selected_html, selected_text, list(selected_attachments or [])),
                }
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(self._read_message_body_payload, thread_msg): thread_msg for thread_msg in rest_msgs}
                    for future in as_completed(futures):
                        thread_msg = futures[future]
                        try:
                            html, text, fetched_attachments = future.result()
                        except Exception as exc:
                            _log_exception(
                                f'Thread body failed ({backend.identity}, {thread_msg.get("folder")}, {thread_msg.get("uid")})',
                                exc,
                            )
                            html, text, fetched_attachments = None, '', []
                        fetched[thread_msg.get('uid')] = (html, text, list(fetched_attachments or []))

                full_records = []
                full_attachments = []
                for thread_msg in thread_msgs:
                    uid = thread_msg.get('uid')
                    html, text, fetched_attachments = fetched.get(uid, (None, '', []))
                    record = self._thread_record_for_message(
                        thread_msg,
                        total,
                        html=html,
                        text=text,
                        attachments=fetched_attachments,
                        selected=(uid == selected_uid),
                    )
                    full_records.append(record)
                    for att in fetched_attachments or []:
                        att_copy = dict(att)
                        att_copy['source_msg'] = dict(thread_msg)
                        full_attachments.append(att_copy)
                GLib.idle_add(self._render_thread_view, msg, full_records, full_attachments, generation)
            except Exception as exc:
                if is_transient_network_error(exc) or not network_ready():
                    self._offline_body_pending = True
                    if self._current_body is None:
                        GLib.idle_add(self._show_loading_viewer)
                else:
                    _log_exception(f'Load thread failed ({backend.identity if backend else (msg.get("account") or "unknown")}, {thread_id})', exc)
                    GLib.idle_add(self._set_body_error, str(exc), generation)
            finally:
                GLib.idle_add(self._end_background_op, op)

        threading.Thread(target=fetch, daemon=True).start()

    def _load_disk_body(self, cache_key):
        return load_disk_body(cache_key)

    def _store_disk_body(self, cache_key, html, text, attachments, msg_date=None):
        store_disk_body(cache_key, html, text, attachments, msg_date)

    def _prune_disk_body_cache(self):
        prune_disk_body_cache()

    def _read_message_body_payload(self, msg):
        started = _perf_counter()
        backend = self._reader_backend_for_message(msg)
        if backend is None:
            raise RuntimeError('No backend available for message')
        uid = msg['uid']
        folder = msg.get('folder')
        cache_key = (backend.identity, folder, uid)
        disk_cache_key = _body_cache_key(backend.identity, folder, uid)
        with self._cache_lock:
            cached_body = self._body_cache.get(cache_key)
        if cached_body is not None:
            _log_perf('body payload', f'memory cache {backend.identity}/{folder}/{uid}', started=started)
            return cached_body
        disk_body = self._load_disk_body(disk_cache_key)
        if disk_body is not None:
            with self._cache_lock:
                self._body_cache[cache_key] = disk_body
                self._body_cache.move_to_end(cache_key)
                while len(self._body_cache) > BODY_CACHE_LIMIT:
                    self._body_cache.popitem(last=False)
            _log_perf('body payload', f'disk cache {backend.identity}/{folder}/{uid}', started=started)
            return disk_body
        html, text, attachments = backend.fetch_body(uid, folder)
        with self._cache_lock:
            self._body_cache[cache_key] = (html, text, attachments)
            self._body_cache.move_to_end(cache_key)
            while len(self._body_cache) > BODY_CACHE_LIMIT:
                self._body_cache.popitem(last=False)
        self._store_disk_body(disk_cache_key, html, text, attachments, msg.get('date'))
        _log_perf('body payload', f'backend fetch {backend.identity}/{folder}/{uid}', started=started)
        return html, text, attachments

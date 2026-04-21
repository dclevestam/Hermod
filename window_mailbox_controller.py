"""Mailbox sync, unified fetch, unread-count, and provider-health orchestration."""

import collections
import threading
from datetime import datetime, timezone

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from .backends import network_ready, is_transient_network_error
    from .unified_refresh import UnifiedFetchSpec, collect_unified_messages
    from .utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _log_exception,
    )
except ImportError:
    from backends import network_ready, is_transient_network_error
    from unified_refresh import UnifiedFetchSpec, collect_unified_messages
    from utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _log_exception,
    )


class MailboxControllerMixin:
    def _set_error(self, message, generation=None):
        """Record a fetch error surfaced from a background worker.

        Historically this method was called without being defined, so every
        non-transient fetch failure crashed the worker thread. We keep the
        implementation minimal and defensive: log the message, clear the
        loading flag for the affected generation, and surface a toast if the
        window already has a toast overlay wired up. Richer UI surfacing can
        hang off this hook later.
        """
        try:
            text = str(message or '').strip()
        except Exception:
            text = ''
        if text:
            _log_exception('mailbox fetch error', RuntimeError(text))
        try:
            self._set_message_loading(False, generation)
        except Exception:
            pass
        if text and hasattr(self, '_show_toast'):
            try:
                self._show_toast(text if len(text) <= 160 else (text[:157] + '…'))
            except Exception:
                pass
        return False

    def _consume_backend_sync_notices(self, backend):
        if backend is None:
            return []
        try:
            if hasattr(backend, 'consume_sync_notices'):
                notices = backend.consume_sync_notices()
            elif hasattr(backend, 'consume_sync_notice'):
                notice = backend.consume_sync_notice()
                notices = [notice] if notice else []
            else:
                notices = []
        except Exception:
            notices = []
        return [notice for notice in notices if notice]

    def _startup_status_apply_notices(self, identity, notices, default_ready_detail='Ready'):
        seen_warning = False
        seen_ready = False
        for notice in notices or []:
            status = str((notice or {}).get('kind') or '').strip().lower()
            detail = str((notice or {}).get('detail') or '').strip()
            if status in {'warning', 'error'}:
                seen_warning = True
                self._set_startup_status_state(
                    identity,
                    status,
                    detail or 'Needs attention',
                )
            elif status == 'ready':
                seen_ready = True
        if not seen_warning and seen_ready:
            self._set_startup_status_state(identity, 'ready', default_ready_detail)
        elif not notices:
            self._set_startup_status_state(identity, 'ready', default_ready_detail)

    def _cached_messages_for_backend(self, backend, folder, limit):
        getter = getattr(backend, 'get_cached_messages', None)
        if not callable(getter):
            return []
        try:
            return list(getter(folder, limit=limit) or [])
        except Exception as exc:
            _log_exception(f'Provider cache load failed ({getattr(backend, "identity", "unknown")}, {folder})', exc)
            return []

    def _apply_provider_cached_messages(self, msgs, generation, preserve_selected_key=None):
        cached_msgs = list(msgs or [])
        if not cached_msgs:
            return False
        page_msgs, has_more = self._paged_messages(cached_msgs)
        self._set_messages(
            page_msgs,
            generation,
            preserve_selected_key,
            'provider-cache',
            False,
            False,
            has_more,
        )
        return True

    def _unified_folder_id_for_backend(self, backend, folder_name=None):
        if backend is None:
            return None
        if folder_name is None:
            return backend.FOLDERS[0][0] if getattr(backend, 'FOLDERS', None) else None
        return next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == folder_name), None)

    def _unified_cached_messages(self, folder_name=None, fetch_limit=50):
        messages = []
        for backend in list(self.backends):
            folder_id = self._unified_folder_id_for_backend(backend, folder_name)
            if not folder_id:
                continue
            messages.extend(self._cached_messages_for_backend(backend, folder_id, fetch_limit))
        messages.sort(
            key=lambda item: item.get('date') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return messages

    def _background_result_affects_current_view(self, result):
        changed_folders = set((result or {}).get('changed_folders') or ())
        if not changed_folders or self.current_folder is None:
            return False
        if self.current_folder == _UNIFIED:
            return any(self._count_bucket_for_folder(folder) == 'inbox' for folder in changed_folders)
        if self.current_folder == _UNIFIED_TRASH:
            return any(self._count_bucket_for_folder(folder) == 'trash' for folder in changed_folders)
        if self.current_folder == _UNIFIED_SPAM:
            return any(self._count_bucket_for_folder(folder) == 'spam' for folder in changed_folders)
        if self.current_folder == _UNIFIED_DRAFTS:
            return any(self._count_bucket_for_folder(folder) == 'drafts' for folder in changed_folders)
        if self.current_folder == _UNIFIED_SENT:
            return any(self._count_bucket_for_folder(folder) == 'sent' for folder in changed_folders)
        if self.current_folder == _UNIFIED_ARCHIVE:
            return any(self._count_bucket_for_folder(folder) == 'archive' for folder in changed_folders)
        if self.current_folder == _UNIFIED_FLAGGED:
            return True
        if not self.current_backend:
            return False
        return (
            (result or {}).get('account') == self.current_backend.identity
            and self.current_folder in changed_folders
        )

    def on_background_update(self, results, total_new=0):
        self.set_syncing(False)
        refresh_needed = False
        for result in results or []:
            notices = (result or {}).get('notice') or []
            if isinstance(notices, dict):
                notices = [notices]
            count_changed = False
            counts = dict((result or {}).get('counts') or {})
            backend_identity = str((result or {}).get('account', '') or '').strip()
            if backend_identity:
                current_counts = getattr(
                    self,
                    '_unread_counts',
                    collections.defaultdict(lambda: {
                        'inbox': 0, 'trash': 0, 'spam': 0,
                        'drafts': 0, 'sent': 0, 'archive': 0, 'flagged': 0,
                    }),
                )[backend_identity]
                for _key in ('inbox', 'trash', 'spam', 'drafts', 'sent', 'archive', 'flagged'):
                    if _key in counts and counts.get(_key) != current_counts.get(_key):
                        count_changed = True
            if getattr(self, '_startup_status_active', False):
                self._startup_status_apply_notices(
                    (result or {}).get('account', ''),
                    notices,
                    default_ready_detail='Ready',
                )
            self.update_account_counts(
                backend_identity,
                inbox_count=counts.get('inbox'),
                trash_count=counts.get('trash'),
                spam_count=counts.get('spam'),
                drafts_count=counts.get('drafts'),
                sent_count=counts.get('sent'),
                archive_count=counts.get('archive'),
                flagged_count=counts.get('flagged'),
            )
            refresh_needed = refresh_needed or self._background_result_affects_current_view(result)
            if count_changed and backend_identity:
                if self._current_view_uses_backend(backend_identity):
                    refresh_needed = True
        if getattr(self, '_startup_status_active', False):
            seen = set(getattr(self, '_startup_counts_seen', set()))
            for result in results or []:
                account = str((result or {}).get('account', '') or '').strip()
                if account:
                    seen.add(account)
            self._startup_counts_seen = seen
            if not self.backends or len(seen) >= len(self.backends):
                self._startup_counts_ready = True
            self._schedule_startup_status_completion(total_new=total_new)
        if total_new > 0:
            self.show_sync_badge(total_new)
        if refresh_needed:
            self.refresh_visible_mail(force=True)

    def _force_primary_probes(self):
        for backend in list(self.backends or []):
            force = getattr(backend, 'force_primary_probe', None)
            if callable(force):
                try:
                    force()
                except Exception:
                    pass

    def _refresh_current_message_list(self, force=False, preserve_selected=True):
        if not network_ready():
            return False
        focused = self.get_focus()
        preserve_key = None
        if preserve_selected and self._active_email_row is not None:
            active_msg = self._active_email_row.msg
            preserve_key = (
                active_msg.get('account', ''),
                active_msg.get('folder', ''),
                active_msg.get('uid', ''),
            )
        if preserve_selected and getattr(self, '_email_scroll', None) is not None:
            adj = self._email_scroll.get_vadjustment()
            if adj is not None:
                self._pending_list_scroll_value = adj.get_value()
        if (force or self._offline_refresh_pending) and self.current_folder:
            self._offline_refresh_pending = False
            if self.current_folder == _UNIFIED:
                self._load_unified_inbox(preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_FLAGGED:
                self._load_unified_inbox(preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_DRAFTS:
                self._load_unified_folder('Drafts', preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_SENT:
                self._load_unified_folder('Sent', preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_ARCHIVE:
                self._load_unified_folder('Archive', preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_TRASH:
                self._load_unified_folder('Trash', preserve_selected_key=preserve_key)
            elif self.current_folder == _UNIFIED_SPAM:
                self._load_unified_folder('Spam', preserve_selected_key=preserve_key)
            elif self.current_backend:
                self._load_messages(preserve_selected_key=preserve_key)
        if focused is not None and focused.get_root() is self:
            GLib.idle_add(self._restore_focus_widget, focused)
        return False

    def refresh_visible_mail(self, force=False, preserve_selected=True):
        self._refresh_current_message_list(force=force, preserve_selected=preserve_selected)
        if self._viewer_stack.get_visible_child_name() != 'viewer':
            return False
        if self._offline_body_pending and self._active_email_row is not None:
            self._offline_body_pending = False
            self._body_load_generation += 1
            active_msg = self._active_email_row.msg
            if active_msg.get('thread_count', 1) > 1:
                self._load_thread_view(active_msg, self._body_load_generation)
            else:
                self._load_body(active_msg, self._body_load_generation)
        return False

    def _load_messages(self, preserve_selected_key=None, sync_complete_callback=None):
        if (
            preserve_selected_key
            and self._pending_list_scroll_value is None
            and getattr(self, '_email_scroll', None) is not None
        ):
            adj = self._email_scroll.get_vadjustment()
            if adj is not None and adj.get_value() > 0:
                self._pending_list_scroll_value = adj.get_value()
        generation = self._begin_message_load()
        self._set_message_loading(True, generation)
        request_key = self._message_list_context_key()
        backend = self.current_backend
        folder = self.current_folder
        fetch_limit = self._message_fetch_limit()
        self._queue_message_snapshot_load(generation, preserve_selected_key, request_key=request_key)
        self._apply_provider_cached_messages(
            self._cached_messages_for_backend(backend, folder, fetch_limit),
            generation,
            preserve_selected_key,
        )
        if getattr(self, '_startup_status_active', False) and self.current_backend is not None:
            self._set_startup_status_title(
                'Starting mail',
                f'Checking {self._account_display_name_for(self.current_backend.identity)}',
            )
            self._set_startup_status_state(
                self.current_backend.identity,
                'checking',
                f'Checking {self.current_folder or "inbox"}',
            )
        if not network_ready():
            self._offline_refresh_pending = True
            self._set_message_loading(False, generation)
            if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            elif getattr(self, '_startup_status_active', False):
                self._schedule_startup_status_completion(force=True)
            return
        self._offline_refresh_pending = False
        if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        op = self._start_background_op(
            'load messages',
            f'{backend.identity}/{folder}',
            'backend fetch_messages, auth, or IMAP latency',
        )

        def fetch():
            try:
                msgs = backend.fetch_messages(folder, limit=fetch_limit)
                notices = self._consume_backend_sync_notices(backend)
                if getattr(self, '_startup_status_active', False):
                    self._startup_status_apply_notices(
                        backend.identity,
                        notices,
                        default_ready_detail='Ready',
                    )
                GLib.idle_add(self._render_account_health, backend.identity)
            except Exception as e:
                if is_transient_network_error(e) or not network_ready():
                    self._offline_refresh_pending = True
                else:
                    _log_exception(f'Load messages failed ({backend.identity}, {folder})', e)
                    GLib.idle_add(self._set_error, str(e), generation)
                    if getattr(self, '_startup_status_active', False):
                        GLib.idle_add(
                            self._set_startup_status_state,
                            backend.identity,
                            'error',
                            'Sync issue',
                        )
                GLib.idle_add(self._render_account_health, backend.identity)
                GLib.idle_add(self._set_message_loading, False, generation)
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                elif getattr(self, '_startup_status_active', False):
                    self._schedule_startup_status_completion(force=True)
                return
            finally:
                GLib.idle_add(self._end_background_op, op)
            page_msgs, has_more = self._paged_messages(msgs)
            GLib.idle_add(self._set_messages, page_msgs, generation, preserve_selected_key, 'live', True, True, has_more)
            if getattr(self, '_startup_status_active', False):
                self._startup_visible_ready = True
                self._schedule_startup_status_completion()
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            elif getattr(self, '_startup_status_active', False):
                self._schedule_startup_status_completion()

        threading.Thread(target=fetch, daemon=True).start()

    def _build_unified_fetch_specs(self, folder_name=None, fetch_limit=50, status_callback=None):
        specs = []
        for backend in list(self.backends):
            folder_id = None
            if folder_name is None:
                if backend.FOLDERS:
                    folder_id = backend.FOLDERS[0][0]
                error_label = f'Unified inbox error ({backend.identity})'
            else:
                folder_id = next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == folder_name), None)
                error_label = f'Unified {folder_name} error ({backend.identity})'
            if not folder_id:
                continue
            backend_identity = backend.identity
            folder_label = folder_name or 'Inbox'
            specs.append(
                UnifiedFetchSpec(
                    identity=backend_identity,
                    label=error_label,
                    fetch=lambda backend=backend, folder_id=folder_id, fetch_limit=fetch_limit, backend_identity=backend_identity, status_callback=status_callback, folder_label=folder_label: self._fetch_unified_account_messages(
                        backend,
                        backend_identity,
                        folder_id,
                        folder_label,
                        fetch_limit,
                        status_callback,
                    ),
                )
            )
        return specs

    def _fetch_unified_account_messages(self, backend, backend_identity, folder_id, folder_label, fetch_limit, status_callback):
        try:
            msgs = backend.fetch_messages(folder_id, limit=fetch_limit)
            notices = self._consume_backend_sync_notices(backend)
            if callable(status_callback):
                self._startup_status_apply_notices(
                    backend_identity,
                    notices,
                    default_ready_detail='Ready',
                )
            GLib.idle_add(self._render_account_health, backend_identity)
            return msgs
        except Exception as exc:
            if callable(status_callback):
                status_callback(backend_identity, 'error', 'Sync issue')
            GLib.idle_add(self._render_account_health, backend_identity)
            raise

    def _load_unified_messages(self, folder_name=None, preserve_selected_key=None, sync_complete_callback=None):
        if (
            preserve_selected_key
            and self._pending_list_scroll_value is None
            and getattr(self, '_email_scroll', None) is not None
        ):
            adj = self._email_scroll.get_vadjustment()
            if adj is not None and adj.get_value() > 0:
                self._pending_list_scroll_value = adj.get_value()
        generation = self._begin_message_load()
        self._set_message_loading(True, generation)
        request_key = self._message_list_context_key()
        fetch_limit = self._message_fetch_limit()
        self._queue_message_snapshot_load(generation, preserve_selected_key, request_key=request_key)
        self._apply_provider_cached_messages(
            self._unified_cached_messages(folder_name, fetch_limit),
            generation,
            preserve_selected_key,
        )
        if getattr(self, '_startup_status_active', False):
            self._set_startup_status_title(
                'Starting mail',
                'Checking accounts and restoring the inbox.',
            )
        if not network_ready():
            self._offline_refresh_pending = True
            self._set_message_loading(False, generation)
            if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            elif getattr(self, '_startup_status_active', False):
                self._schedule_startup_status_completion(force=True)
            return
        self._offline_refresh_pending = False
        if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        fetch_specs = self._build_unified_fetch_specs(
            folder_name,
            fetch_limit=fetch_limit,
            status_callback=self._set_startup_status_state if getattr(self, '_startup_status_active', False) else None,
        )
        op_kind = 'load unified inbox' if folder_name is None else f'load unified {folder_name.lower()}'
        op = self._start_background_op(
            op_kind,
            'all accounts',
            'one backend may be slow or blocked; check auth/network',
        )

        def fetch():
            try:
                result = collect_unified_messages(
                    fetch_specs,
                    transient_error_fn=is_transient_network_error,
                    network_ready_fn=network_ready,
                    error_logger=_log_exception,
                    progress_callback=(
                        (lambda spec, stage, error=None, count=None: GLib.idle_add(
                            self._set_startup_status_state,
                            spec.identity,
                            'checking' if stage == 'checking' else 'error' if stage == 'error' else 'ready',
                            'Checking mail' if stage == 'checking' else 'Sync issue' if stage == 'error' else 'Ready',
                        )) if getattr(self, '_startup_status_active', False) else None
                    ),
                    limit=fetch_limit,
                )
            finally:
                GLib.idle_add(self._end_background_op, op)
            if result.get('had_transient_error'):
                self._offline_refresh_pending = True
            if result.get('had_transient_error') and not result.get('messages'):
                GLib.idle_add(self._set_message_loading, False, generation)
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                elif getattr(self, '_startup_status_active', False):
                    self._schedule_startup_status_completion(force=True)
                return
            page_msgs, has_more = self._paged_messages(result.get('messages', []))
            GLib.idle_add(self._set_messages, page_msgs, generation, preserve_selected_key, 'live', True, True, has_more)
            if getattr(self, '_startup_status_active', False):
                self._startup_visible_ready = True
                self._schedule_startup_status_completion()
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            elif getattr(self, '_startup_status_active', False):
                self._schedule_startup_status_completion()

        threading.Thread(target=fetch, daemon=True).start()

    def _load_unified_inbox(self, preserve_selected_key=None, sync_complete_callback=None):
        self._load_unified_messages(
            folder_name=None,
            preserve_selected_key=preserve_selected_key,
            sync_complete_callback=sync_complete_callback,
        )

    def _load_unified_folder(self, folder_name, preserve_selected_key=None, sync_complete_callback=None):
        self._load_unified_messages(
            folder_name=folder_name,
            preserve_selected_key=preserve_selected_key,
            sync_complete_callback=sync_complete_callback,
        )

    def _count_bucket_for_folder(self, folder):
        folder = (folder or '').lower()
        if folder in (_UNIFIED, 'inbox'):
            return 'inbox'
        if 'trash' in folder or 'deleteditems' in folder:
            return 'trash'
        if 'spam' in folder or 'junk' in folder:
            return 'spam'
        if 'drafts' in folder or folder.endswith('/draft'):
            return 'drafts'
        if folder.endswith('/sent mail') or folder.endswith('/sent') or folder == 'sent' or 'sentitems' in folder:
            return 'sent'
        if 'archive' in folder or 'all mail' in folder or 'allmail' in folder:
            return 'archive'
        return None

    def _seed_unread_counts_from_messages(self, msgs):
        # Keep sidebar counts provider-backed. Visible rows can be only a page
        # slice, so using them here causes lower or shifting unread counts.
        return

    def _current_view_uses_backend(self, backend_identity):
        if not backend_identity:
            return False
        current_backend = getattr(self, 'current_backend', None)
        if current_backend and current_backend.identity == backend_identity:
            return True
        return getattr(self, 'current_folder', None) in (
            _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
            _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        )

    def _refresh_visible_mail_for_backend(self, backend_identity, *, force=False):
        if not self._current_view_uses_backend(backend_identity):
            return
        GLib.idle_add(self.refresh_visible_mail, bool(force), True)

    def _refresh_provider_counts_for_message(self, msg, backend=None):
        # source-of-truth: provider unread count only.
        backend = backend or msg.get('backend_obj')
        backend_identity = getattr(backend, 'identity', None) or msg.get('account') or None
        if not backend_identity or backend is None:
            return
        folder = msg.get('folder') or 'INBOX'
        bucket = self._count_bucket_for_folder(folder)
        if bucket is None:
            return

        def run():
            try:
                count = backend.get_unread_count(folder)
            except Exception:
                return

            def apply():
                kwargs = {
                    'inbox_count': None, 'trash_count': None, 'spam_count': None,
                    'drafts_count': None, 'sent_count': None,
                    'archive_count': None, 'flagged_count': None,
                }
                kwargs[f'{bucket}_count'] = count
                self.update_account_counts(backend_identity, **kwargs)
                self._refresh_visible_mail_for_backend(backend_identity, force=True)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=run, daemon=True).start()

    def _render_unread_counts(self, backend_identity):
        counts = self._unread_counts[backend_identity]
        inbox_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Inbox')))
        if inbox_row:
            inbox_row.set_count(counts['inbox'])

        trash_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Trash')))
        if trash_row:
            trash_row.set_count(counts['trash'], dim=True)

        spam_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Spam')))
        if spam_row:
            spam_row.set_count(counts['spam'], dim=True)

        drafts_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Drafts')))
        if drafts_row:
            drafts_row.set_count(counts.get('drafts', 0), dim=True)

        sent_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Sent')))
        if sent_row:
            sent_row.set_count(counts.get('sent', 0), dim=True)

        archive_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Archive')))
        if archive_row:
            archive_row.set_count(counts.get('archive', 0), dim=True)

        state = self._account_state.get(backend_identity)
        if state:
            state['header'].set_count(counts['inbox'])
        self._render_account_health(backend_identity)

        total = sum(account_counts['inbox'] for account_counts in self._unread_counts.values())
        if self._all_inboxes_row:
            self._all_inboxes_row.set_count(total)

        total_flagged = sum(account_counts.get('flagged', 0) for account_counts in self._unread_counts.values())
        if getattr(self, '_flagged_row', None):
            self._flagged_row.set_count(total_flagged)

        total_drafts = sum(account_counts.get('drafts', 0) for account_counts in self._unread_counts.values())
        if getattr(self, '_drafts_row', None):
            self._drafts_row.set_count(total_drafts, dim=True)

        total_sent = sum(account_counts.get('sent', 0) for account_counts in self._unread_counts.values())
        if getattr(self, '_sent_row', None):
            self._sent_row.set_count(total_sent, dim=True)

        total_archive = sum(account_counts.get('archive', 0) for account_counts in self._unread_counts.values())
        if getattr(self, '_archive_row', None):
            self._archive_row.set_count(total_archive, dim=True)

    def _refresh_all_unread_counts(self):
        for backend_identity in list(self._unread_counts.keys()):
            self._render_unread_counts(backend_identity)

    def _folder_id_for_name(self, backend_identity, display_name):
        state = self._account_state.get(backend_identity)
        if not state:
            return None
        backend = state['header'].backend
        return next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == display_name), None)

    def update_account_counts(
        self,
        backend_identity,
        inbox_count=None,
        trash_count=None,
        spam_count=None,
        drafts_count=None,
        sent_count=None,
        archive_count=None,
        flagged_count=None,
    ):
        # source-of-truth: backend counts, never visible page slices.
        counts = self._unread_counts[backend_identity]
        if inbox_count is not None:
            counts['inbox'] = inbox_count
        if trash_count is not None:
            counts['trash'] = trash_count
        if spam_count is not None:
            counts['spam'] = spam_count
        if drafts_count is not None:
            counts['drafts'] = drafts_count
        if sent_count is not None:
            counts['sent'] = sent_count
        if archive_count is not None:
            counts['archive'] = archive_count
        if flagged_count is not None:
            counts['flagged'] = flagged_count
        self._render_account_health(backend_identity)
        if getattr(self, '_startup_status_active', False):
            return
        self._render_unread_counts(backend_identity)

    def _backend_sync_health(self, backend):
        if backend is None:
            return None
        getter = getattr(backend, 'get_sync_health', None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _render_account_health(self, backend_identity):
        state = self._account_state.get(backend_identity)
        if not state:
            return False
        backend = state['header'].backend
        health = self._backend_sync_health(backend)
        if not health:
            state['header'].set_health(None)
            return False
        state['header'].set_health(
            health.get('state'),
            health.get('detail', ''),
            health.get('tooltip', ''),
        )
        return False

    def update_folder_count(self, backend_identity, folder_id, count):
        state = self._account_state.get(backend_identity)
        if not state:
            return
        backend = state['header'].backend
        folder_name = next((name for fid, name, _icon in backend.FOLDERS if fid == folder_id), None)
        row = self._folder_rows.get((backend_identity, folder_id))
        if row:
            row.set_count(count, dim=folder_name in ('Trash', 'Spam'))

    def _warm_startup_unread_counts(self):
        if not getattr(self, '_startup_status_active', False):
            return
        if getattr(self, '_startup_counts_warmup_started', False):
            return
        self._startup_counts_warmup_started = True
        # Leave unread counts hidden until background reconciliation has
        # produced provider-backed values.
        self._startup_counts_ready = False

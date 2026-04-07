"""Mailbox list, selection, loading, and sidebar behavior for LarkWindow."""

import collections
import threading
from datetime import datetime, timezone

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

try:
    from .backends import network_ready, is_transient_network_error
    from .compose import ComposeView
    from .settings import get_settings
    from .widgets import (
        EmailRow, AccountHeaderRow, FolderRow, LoadMoreListItem, LoadMoreRow,
        MessageListItem, MoreFoldersRow, UnifiedRow,
    )
    from .snapshot_cache import (
        build_snapshot_payload,
        load_snapshot_payload,
        snapshot_result_applicable,
    )
    from .unified_refresh import UnifiedFetchSpec, collect_unified_messages
    from .utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR,
        _body_cache_key,
        _snapshot_scope, _snapshot_path,
        _backend_for_identity, _backend_for_message,
        _log_exception, _perf_counter, _log_perf,
    )
    from .window_constants import (
        BODY_CACHE_LIMIT, MESSAGE_LIST_MAX_WIDTH, MESSAGE_LIST_MIN_WIDTH,
        MESSAGE_PAGE_STEP, PREFETCH_WARMUP_LIMIT,
    )
except ImportError:
    from backends import network_ready, is_transient_network_error
    from compose import ComposeView
    from settings import get_settings
    from widgets import (
        EmailRow, AccountHeaderRow, FolderRow, LoadMoreListItem, LoadMoreRow,
        MessageListItem, MoreFoldersRow, UnifiedRow,
    )
    from snapshot_cache import (
        build_snapshot_payload,
        load_snapshot_payload,
        snapshot_result_applicable,
    )
    from unified_refresh import UnifiedFetchSpec, collect_unified_messages
    from utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR,
        _body_cache_key,
        _snapshot_scope, _snapshot_path,
        _backend_for_identity, _backend_for_message,
        _log_exception, _perf_counter, _log_perf,
    )
    from window_constants import (
        BODY_CACHE_LIMIT, MESSAGE_LIST_MAX_WIDTH, MESSAGE_LIST_MIN_WIDTH,
        MESSAGE_PAGE_STEP, PREFETCH_WARMUP_LIMIT,
    )


class MessageListMixin:
    def _selected_message_key(self):
        row = self._active_email_row or self._selected_message_row()
        if row is None or not isinstance(row, MessageListItem):
            return None
        msg = row.msg
        return (
            msg.get('account', ''),
            msg.get('folder', ''),
            msg.get('uid', ''),
        )

    def _selected_message_row(self):
        if not hasattr(self, '_message_selection'):
            return None
        item = self._message_selection.get_selected_item()
        if isinstance(item, MessageListItem):
            return item
        return None

    def _visible_message_item(self, index):
        if index < 0 or index >= self._filtered_message_model.get_n_items():
            return None
        return self._filtered_message_model.get_item(index)

    def _has_visible_message_items(self):
        for index in range(self._filtered_message_model.get_n_items()):
            if isinstance(self._filtered_message_model.get_item(index), MessageListItem):
                return True
        return False

    def _find_visible_item_index(self, target_item):
        if target_item is None:
            return None
        for index in range(self._filtered_message_model.get_n_items()):
            if self._filtered_message_model.get_item(index) is target_item:
                return index
        return None

    def _find_store_index_for_key(self, key):
        if key is None:
            return None
        for index in range(self._message_store.get_n_items()):
            item = self._message_store.get_item(index)
            if not isinstance(item, MessageListItem):
                continue
            msg = item.msg
            if (
                msg.get('account', ''),
                msg.get('folder', ''),
                msg.get('uid', ''),
            ) == key:
                return index
        return None

    def _select_message_item(self, item, suppress=False, grab_focus=False):
        index = self._find_visible_item_index(item)
        if index is None:
            return False
        self._set_selected_visible_index(index, suppress=suppress, grab_focus=grab_focus)
        return True

    def _set_selected_visible_index(self, index, suppress=False, grab_focus=False):
        if index is None:
            return False
        if index < 0 or index >= self._filtered_message_model.get_n_items():
            return False
        self._suppress_email_selection = suppress
        self._message_selection.set_selected(index)
        self._suppress_email_selection = False
        if grab_focus:
            item = self._visible_message_item(index)
            if item is not None:
                item.grab_focus()
        return True

    def _show_load_more_row(self):
        return bool(self._message_has_more)

    def _message_fetch_limit(self):
        return max(1, int(self._message_page_limit)) + 1

    def _paged_messages(self, msgs):
        page_limit = max(1, int(self._message_page_limit))
        ordered = list(msgs or [])
        has_more = len(ordered) > page_limit
        return ordered[:page_limit], has_more

    def _reset_message_paging(self):
        self._message_page_limit = MESSAGE_PAGE_STEP
        self._message_has_more = False

    def _on_load_more_requested(self):
        if not self.current_folder:
            return
        if getattr(self, '_email_scroll', None) is not None:
            adj = self._email_scroll.get_vadjustment()
            if adj is not None:
                self._pending_list_scroll_value = adj.get_value()
        self._message_page_limit += MESSAGE_PAGE_STEP
        self.refresh_visible_mail(force=True, preserve_selected=True)

    def _populate_sidebar(self):
        s = get_settings()

        self._all_inboxes_row = UnifiedRow(_UNIFIED, 'All Inboxes', 'mail-inbox-symbolic')
        self._folder_rows[(_UNIFIED, _UNIFIED)] = self._all_inboxes_row
        self.folder_list.append(self._all_inboxes_row)

        for backend in self.backends:
            accent_class = self._account_class_for(backend.identity)
            header_row = AccountHeaderRow(backend.identity, accent_class=accent_class)
            header_row.backend = backend
            self.folder_list.append(header_row)

            folder_rows = []
            for folder_id, name, icon in backend.FOLDERS:
                row = FolderRow(folder_id, name, icon, indent=True, accent_class=accent_class)
                row.backend = backend
                row.set_visible(False)
                self._folder_rows[(backend.identity, folder_id)] = row
                self.folder_list.append(row)
                folder_rows.append(row)

            more_row = MoreFoldersRow(accent_class=accent_class)
            more_row.backend = backend
            more_row.set_visible(False)
            self.folder_list.append(more_row)

            self._account_state[backend.identity] = {
                'header': header_row,
                'folders': folder_rows,
                'more_row': more_row,
                'extra': [],
                'expanded': False,
            }

        if s.get('show_unified_trash') or s.get('show_unified_spam'):
            if s.get('show_unified_trash'):
                trash_row = UnifiedRow(_UNIFIED_TRASH, 'All Trash', 'user-trash-full-symbolic')
                self._folder_rows[(_UNIFIED_TRASH, _UNIFIED_TRASH)] = trash_row
                self.folder_list.append(trash_row)
            if s.get('show_unified_spam'):
                spam_row = UnifiedRow(_UNIFIED_SPAM, 'All Spam', 'mail-mark-junk-symbolic')
                self._folder_rows[(_UNIFIED_SPAM, _UNIFIED_SPAM)] = spam_row
                self.folder_list.append(spam_row)

    def _setup_shortcuts(self):
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _update_sync_status_labels(self):
        self._countdown_hint_lbl.set_label('Background')
        if self._network_offline:
            self._countdown_lbl.set_label('Offline')
        elif self._syncing:
            self._countdown_lbl.set_label('Checking')
        else:
            self._countdown_lbl.set_label('Connected')

    def set_network_offline(self, offline):
        offline = bool(offline)
        self._network_offline = offline
        view_name = self._viewer_stack.get_visible_child_name() if hasattr(self, '_viewer_stack') else None
        if offline:
            self._syncing = False
            self._sync_btn.remove_css_class('sync-online')
            self._sync_btn.add_css_class('sync-offline')
            if hasattr(self, '_sync_state_stack'):
                self._sync_state_stack.set_visible_child_name('offline')
            self._sync_btn.set_tooltip_text('No network connection')
            if view_name == 'viewer':
                self.title_widget.set_subtitle('No Network Connection')
        else:
            self._sync_btn.remove_css_class('sync-offline')
            self._sync_btn.add_css_class('sync-online')
            if hasattr(self, '_sync_state_stack'):
                self._sync_state_stack.set_visible_child_name('online')
            self._sync_btn.set_tooltip_text('Sync now (F5)')
            if view_name == 'viewer':
                self.title_widget.set_subtitle(self._content_subtitle)
        self._update_sync_status_labels()

    def set_syncing(self, syncing):
        if self._network_offline:
            self._syncing = False
            self._update_sync_status_labels()
            return
        self._syncing = bool(syncing)
        self._update_sync_status_labels()

    def _finish_sync(self, total_new=0):
        self.set_syncing(False)
        self._sync_in_flight = False
        if total_new > 0:
            self.show_sync_badge(total_new)
            self.refresh_visible_mail(force=True)

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
            counts = dict((result or {}).get('counts') or {})
            self.update_account_counts(
                (result or {}).get('account', ''),
                inbox_count=counts.get('inbox'),
                trash_count=counts.get('trash'),
                spam_count=counts.get('spam'),
            )
            refresh_needed = refresh_needed or self._background_result_affects_current_view(result)
        if total_new > 0:
            self.show_sync_badge(total_new)
        if refresh_needed:
            self.refresh_visible_mail(force=True)

    def _on_content_paned_position_changed(self, paned, _pspec):
        position = paned.get_position()
        clamped = max(MESSAGE_LIST_MIN_WIDTH, min(MESSAGE_LIST_MAX_WIDTH, position))
        if clamped != position:
            paned.set_position(clamped)

    def _on_row_activated(self, _, row):
        if isinstance(row, AccountHeaderRow):
            self._toggle_account(row.identity)
        elif isinstance(row, MoreFoldersRow):
            self._toggle_more_folders(row)

    def _toggle_account(self, identity):
        state = self._account_state[identity]
        state['expanded'] = not state['expanded']
        visible = state['expanded']
        state['header'].expanded = visible
        state['header'].chevron.set_from_icon_name(
            'pan-down-symbolic' if visible else 'pan-end-symbolic'
        )
        for row in state['folders']:
            row.set_visible(visible)
        state['more_row'].set_visible(visible)
        extra_visible = visible and state['more_row'].expanded
        for row in state['extra']:
            row.set_visible(extra_visible)

    def _toggle_more_folders(self, more_row):
        identity = more_row.backend.identity
        state = self._account_state[identity]
        if not more_row.loaded:
            if more_row.spinner.get_spinning():
                return
            more_row.spinner.set_spinning(True)

            def fetch():
                try:
                    folders = more_row.backend.fetch_all_folders()
                    GLib.idle_add(self._on_extra_folders_loaded, more_row, folders)
                except Exception:
                    GLib.idle_add(lambda: more_row.spinner.set_spinning(False))

            threading.Thread(target=fetch, daemon=True).start()
            return
        more_row.expanded = not more_row.expanded
        more_row.chevron.set_from_icon_name(
            'pan-down-symbolic' if more_row.expanded else 'pan-end-symbolic'
        )
        for row in state['extra']:
            row.set_visible(more_row.expanded)

    def _on_extra_folders_loaded(self, more_row, folders):
        identity = more_row.backend.identity
        state = self._account_state[identity]
        more_row.spinner.set_spinning(False)
        if not folders:
            more_row.set_visible(False)
            return
        more_row.loaded = True
        more_row.expanded = True
        more_row.chevron.set_from_icon_name('pan-down-symbolic')
        insert_pos = more_row.get_index() + 1
        new_rows = []
        for folder_id, name, icon in folders:
            row = FolderRow(folder_id, name, icon, indent=True)
            row.backend = more_row.backend
            self._folder_rows[(identity, folder_id)] = row
            self.folder_list.insert(row, insert_pos)
            insert_pos += 1
            new_rows.append(row)
        state['extra'] = new_rows

    def _setup_email_list_item(self, _factory, list_item):
        list_item.connect('notify::selected', self._on_email_list_item_selected_changed)

    def _bind_email_list_item(self, _factory, list_item):
        item = list_item.get_item()
        if isinstance(item, MessageListItem):
            child = EmailRow(
                item.msg,
                self._on_reply,
                self._on_reply_all,
                self._on_delete,
                accent_class=item.accent_class,
            )
        elif isinstance(item, LoadMoreListItem):
            child = LoadMoreRow(item.label, self._on_load_more_requested)
        else:
            child = Gtk.Box()
        list_item.set_child(child)
        if hasattr(item, 'bind_widget'):
            item.bind_widget(child)
        if hasattr(child, 'set_selected'):
            child.set_selected(list_item.get_selected())

    def _unbind_email_list_item(self, _factory, list_item):
        item = list_item.get_item()
        child = list_item.get_child()
        if hasattr(item, 'unbind_widget') and child is not None:
            item.unbind_widget(child)
        list_item.set_child(None)

    def _on_email_list_item_selected_changed(self, list_item, _pspec):
        child = list_item.get_child()
        if hasattr(child, 'set_selected'):
            child.set_selected(list_item.get_selected())
        item = list_item.get_item()
        if hasattr(item, 'set_selected'):
            item.set_selected(list_item.get_selected())

    def _on_email_list_activated(self, _list_view, position):
        item = self._visible_message_item(position)
        if isinstance(item, LoadMoreListItem):
            self._on_load_more_requested()
        elif isinstance(item, MessageListItem):
            self._set_selected_visible_index(position, suppress=True)
            self._request_commit_email_selection(item)

    def _on_folder_selected(self, _, row):
        if self._suppress_folder_selection:
            return
        if row is None:
            return
        if self._compose_active():
            self._commit_folder_selection(row, show_view=False)
            return
        self._commit_folder_selection(row, show_view=True)

    def _commit_folder_selection(self, row, show_view=True):
        changing_context = row is not self._active_folder_row
        self._active_folder_row = row
        self._active_email_row = None
        if changing_context:
            self._reset_message_paging()
        if show_view:
            self._show_mail_view()
        if isinstance(row, UnifiedRow):
            self.current_backend = None
            self.current_folder = row.folder_id
            self._set_context_title(row.folder_name, '')
            if row.folder_id == _UNIFIED:
                self._load_unified_inbox()
            elif row.folder_id == _UNIFIED_TRASH:
                self._load_unified_folder('Trash')
            elif row.folder_id == _UNIFIED_SPAM:
                self._load_unified_folder('Spam')
        elif isinstance(row, FolderRow):
            self.current_backend = row.backend
            self.current_folder = row.folder_id
            self._set_context_title(row.folder_name, row.backend.identity)
            self._load_messages()

    def _request_commit_email_selection(self, row):
        if self._compose_active():
            self._request_leave_compose(
                lambda: self._commit_email_selection(row),
                self._restore_email_selection,
            )
            return
        self._commit_email_selection(row)

    def _on_email_selected(self, *_):
        if self._suppress_email_selection:
            return
        return

    def _commit_email_selection(self, row):
        self._startup_autoselect_pending = False
        self._active_email_row = row
        mark_on_open = get_settings().get('mark_read_on_open')
        was_unread = not row.msg.get('is_read', True)
        self._show_mail_view()
        self._body_load_generation += 1
        if row.msg.get('thread_count', 1) > 1:
            self._load_thread_view(row.msg, self._body_load_generation)
        else:
            self._load_body(row.msg, self._body_load_generation)
        if mark_on_open:
            row.mark_read()
        if was_unread and mark_on_open:
            self._sync_backend_cached_read_state(row.msg, True)
            self._adjust_unread_count_for_message(row.msg, -1)

    def _restore_folder_selection(self):
        self._suppress_folder_selection = True
        self.folder_list.select_row(self._active_folder_row)
        self._suppress_folder_selection = False

    def _restore_email_selection(self):
        self._select_message_item(self._active_email_row, suppress=True)

    def _on_sync(self, _=None):
        self._flash_action_feedback(self._sync_btn)
        if self._sync_in_flight or self._syncing:
            return
        if self._network_offline or not network_ready():
            self.set_network_offline(True)
            return
        self._sync_in_flight = True
        self.set_syncing(True)
        self._offline_refresh_pending = False
        preserve_key = self._selected_message_key()
        if self.current_folder == _UNIFIED:
            self._load_unified_inbox(preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_TRASH:
            self._load_unified_folder('Trash', preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_SPAM:
            self._load_unified_folder('Spam', preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_backend:
            self._load_messages(preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)

    def _on_compose(self, _=None):
        self._flash_action_feedback(self._compose_btn)
        if self._compose_active():
            return
        backend = self.current_backend or (self.backends[0] if self.backends else None)
        if backend:
            self._present_compose(ComposeView(self, backend, self.backends, on_close=self._close_inline_compose))

    def _on_settings(self, _=None):
        if self._viewer_stack.get_visible_child_name() == 'settings':
            self._show_mail_view()
        elif self._compose_active():
            self._request_leave_compose(self._show_mail_view)
        else:
            self._show_settings_view()

    def _on_reply(self, msg):
        if self._compose_active():
            return
        backend = msg.get('backend_obj') or self.current_backend
        if backend:
            self._present_compose(
                ComposeView(self, backend, self.backends, reply_to=msg, on_close=self._close_inline_compose)
            )

    def _on_reply_all(self, msg):
        if self._compose_active():
            return
        backend = msg.get('backend_obj') or self.current_backend
        if backend:
            self._present_compose(
                ComposeView(
                    self,
                    backend,
                    self.backends,
                    reply_to=msg,
                    reply_all=True,
                    on_close=self._close_inline_compose,
                )
            )

    def _present_compose(self, compose_view):
        def _show():
            while (child := self._compose_holder.get_first_child()):
                self._compose_holder.remove(child)
            self._compose_view = compose_view
            self._compose_holder.append(compose_view)
            self._viewer_stack.set_visible_child_name('compose')
            self._settings_btn.set_icon_name('go-previous-symbolic')
            self._settings_btn.set_tooltip_text('Back')
            self.title_widget.set_title(compose_view.get_title())
            self.title_widget.set_subtitle('')

        if self._compose_active():
            self._request_leave_compose(_show)
            return
        _show()

    def _set_context_title(self, title, subtitle=''):
        self._content_title = title
        self._content_subtitle = subtitle or ''
        if self._viewer_stack.get_visible_child_name() != 'settings':
            self.title_widget.set_title(self._content_title)
            self.title_widget.set_subtitle('No Network Connection' if self._network_offline else self._content_subtitle)

    def _show_settings_view(self):
        def _show():
            self._viewer_stack.set_visible_child_name('settings')
            self._settings_btn.set_icon_name('go-previous-symbolic')
            self._settings_btn.set_tooltip_text('Back')
            self.title_widget.set_title('Settings')
            self.title_widget.set_subtitle('')

        if self._compose_active():
            self._request_leave_compose(_show)
            return
        _show()

    def _show_mail_view(self):
        self._viewer_stack.set_visible_child_name('viewer')
        self._settings_btn.set_icon_name('open-menu-symbolic')
        self._settings_btn.set_tooltip_text('Settings')
        self.title_widget.set_title(self._content_title)
        self.title_widget.set_subtitle('No Network Connection' if self._network_offline else self._content_subtitle)
        if self._background_refresh_pending and network_ready():
            self._background_refresh_pending = False
            GLib.idle_add(self.refresh_visible_mail, True)
        if self._offline_refresh_pending and network_ready():
            GLib.idle_add(self.refresh_visible_mail)

    def _on_delete(self, row, msg):
        key = (
            msg.get('account', ''),
            msg.get('folder', ''),
            msg.get('uid', ''),
        )
        store_index = self._find_store_index_for_key(key)
        if store_index is not None:
            self._message_store.remove(store_index)
        if not self._has_visible_message_items():
            self._list_stack.set_visible_child_name('empty')
        self._remove_backend_cached_message(msg)
        backend = msg.get('backend_obj') or self.current_backend
        if not backend:
            return

        def delete():
            try:
                backend.delete_message(msg['uid'], msg.get('folder'))
                GLib.idle_add(self._show_toast, 'Message deleted')
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Delete failed: {e}')

        threading.Thread(target=delete, daemon=True).start()

    def _on_mark_unread(self):
        row = self._selected_message_row()
        if not row:
            return
        msg = row.msg
        backend = msg.get('backend_obj') or self.current_backend
        if not backend:
            return
        if not msg.get('is_read', True):
            return
        row.mark_unread()
        msg['is_read'] = False
        self._sync_backend_cached_read_state(msg, False)
        self._adjust_unread_count_for_message(msg, 1)

        def do_mark():
            try:
                backend.mark_as_unread(msg['uid'], msg.get('folder'))
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Failed: {e}')

        threading.Thread(target=do_mark, daemon=True).start()

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().lower()
        self._message_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _email_filter(self, item, *_args):
        if not isinstance(item, MessageListItem):
            return True
        if not self._search_text:
            return True
        msg = item.msg
        return (
            self._search_text in msg.get('sender_name', '').lower()
            or self._search_text in msg.get('sender_email', '').lower()
            or self._search_text in msg.get('subject', '').lower()
        )

    def _on_key_pressed(self, controller, keyval, keycode, state):
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        if mods:
            if keyval == Gdk.KEY_F5:
                self._on_sync()
                return True
            return False

        key = chr(keyval) if 32 <= keyval < 127 else None
        if keyval == Gdk.KEY_F5:
            self._on_sync()
            return True
        if key == '/':
            self._search_entry.grab_focus()
            self._search_entry.select_region(0, -1)
            return True
        if key == 'c':
            self._on_compose()
            return True
        if key in ('n', 'j'):
            self._move_selection(1)
            return True
        if key in ('p', 'k'):
            self._move_selection(-1)
            return True
        if key == 'r':
            row = self._selected_message_row()
            if row:
                self._on_reply(row.msg)
            return True
        if key == 'a':
            row = self._selected_message_row()
            if row:
                self._on_reply_all(row.msg)
            return True
        if key == 'd':
            row = self._selected_message_row()
            if row:
                self._on_delete(row, row.msg)
            return True
        if key == 'u':
            self._on_mark_unread()
            return True
        if keyval == Gdk.KEY_Escape:
            if self._search_entry.get_text():
                self._search_entry.set_text('')
                return True
        return False

    def _move_selection(self, delta):
        count = self._filtered_message_model.get_n_items()
        if count == 0:
            return
        current_index = self._message_selection.get_selected()
        if current_index == Gtk.INVALID_LIST_POSITION:
            next_index = 0
        else:
            next_index = max(0, min(count - 1, current_index + delta))
        self._set_selected_visible_index(next_index, grab_focus=True)
        item = self._visible_message_item(next_index)
        if isinstance(item, MessageListItem):
            self._request_commit_email_selection(item)

    def _begin_message_load(self):
        self._message_load_generation += 1
        self._prefetch_generation += 1
        return self._message_load_generation

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
        if (force or self._offline_refresh_pending) and self.current_folder:
            self._offline_refresh_pending = False
            if self.current_folder == _UNIFIED:
                self._load_unified_inbox(preserve_selected_key=preserve_key)
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

    def _restore_focus_widget(self, widget):
        try:
            if widget is not None and widget.get_root() is self:
                widget.grab_focus()
        except Exception:
            pass
        return False

    def _load_messages(self, preserve_selected_key=None, sync_complete_callback=None):
        generation = self._begin_message_load()
        request_key = self._message_list_context_key()
        self._queue_message_snapshot_load(generation, preserve_selected_key, request_key=request_key)
        if not network_ready():
            self._offline_refresh_pending = True
            if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            return
        self._offline_refresh_pending = False
        if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        backend = self.current_backend
        folder = self.current_folder
        op = self._start_background_op(
            'load messages',
            f'{backend.identity}/{folder}',
            'backend fetch_messages, auth, or IMAP latency',
        )
        fetch_limit = self._message_fetch_limit()

        def fetch():
            try:
                msgs = backend.fetch_messages(folder, limit=fetch_limit)
            except Exception as e:
                if is_transient_network_error(e) or not network_ready():
                    self._offline_refresh_pending = True
                else:
                    _log_exception(f'Load messages failed ({backend.identity}, {folder})', e)
                    GLib.idle_add(self._set_error, str(e), generation)
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                return
            finally:
                GLib.idle_add(self._end_background_op, op)
            page_msgs, has_more = self._paged_messages(msgs)
            GLib.idle_add(self._set_messages, page_msgs, generation, preserve_selected_key, 'live', True, True, has_more)
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)

        threading.Thread(target=fetch, daemon=True).start()

    def _build_unified_fetch_specs(self, folder_name=None, fetch_limit=50):
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
            specs.append(
                UnifiedFetchSpec(
                    label=error_label,
                    fetch=lambda backend=backend, folder_id=folder_id, fetch_limit=fetch_limit: backend.fetch_messages(folder_id, limit=fetch_limit),
                )
            )
        return specs

    def _load_unified_messages(self, folder_name=None, preserve_selected_key=None, sync_complete_callback=None):
        generation = self._begin_message_load()
        request_key = self._message_list_context_key()
        self._queue_message_snapshot_load(generation, preserve_selected_key, request_key=request_key)
        if not network_ready():
            self._offline_refresh_pending = True
            if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            return
        self._offline_refresh_pending = False
        if self._displayed_message_list_key != request_key or self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        fetch_limit = self._message_fetch_limit()
        fetch_specs = self._build_unified_fetch_specs(folder_name, fetch_limit=fetch_limit)
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
                    limit=fetch_limit,
                )
            finally:
                GLib.idle_add(self._end_background_op, op)
            if result.get('had_transient_error'):
                self._offline_refresh_pending = True
            if result.get('had_transient_error') and not result.get('messages'):
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                return
            page_msgs, has_more = self._paged_messages(result.get('messages', []))
            GLib.idle_add(self._set_messages, page_msgs, generation, preserve_selected_key, 'live', True, True, has_more)
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)

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

    def _build_message_items(self, msgs, has_more=False):
        items = []
        for msg in msgs:
            accent_class = self._account_class_for(
                (msg.get('account') or (msg.get('backend_obj').identity if msg.get('backend_obj') else ''))
            )
            items.append(MessageListItem(msg, accent_class=accent_class))
        if has_more:
            items.append(LoadMoreListItem())
        return items

    def _set_messages(
        self,
        msgs,
        generation=None,
        preserve_selected_key=None,
        source='live',
        persist_snapshot=True,
        prefetch_bodies=True,
        has_more=False,
    ):
        if generation is not None and generation != self._message_load_generation:
            return False
        if source == 'snapshot' and not snapshot_result_applicable(
            generation,
            self._message_load_generation,
            self._message_live_generation,
        ):
            return False
        if source == 'live' and generation is not None:
            self._message_live_generation = max(self._message_live_generation, generation)
        started = _perf_counter()
        msgs = [dict(msg) for msg in (msgs or [])]
        self._displayed_message_list_key = self._message_list_context_key()
        self._message_has_more = bool(has_more)
        self._message_store.splice(0, self._message_store.get_n_items(), [])
        if not msgs:
            self._thread_groups = {}
            self._prefetch_generation += 1
            self._message_has_more = False
            self._empty_page.set_title('No messages')
            self._empty_page.set_description(None)
            self._list_stack.set_visible_child_name('empty')
            _log_perf('set messages', '0 msgs -> empty', started=started)
            return False
        groups = collections.OrderedDict()
        representatives = []
        singletons = []
        for m in msgs:
            m['thread_count'] = 1
            m['thread_key'] = None
            m.pop('thread_members', None)
            key = self._thread_key_for_msg(m)
            if key is None:
                singletons.append(m)
                continue
            group = groups.setdefault(key, [])
            group.append(m)
        self._thread_groups = groups
        for key, group in groups.items():
            group.sort(key=lambda item: item.get('date') or datetime.min.replace(tzinfo=timezone.utc))
            count = len(group)
            representative = dict(group[-1])
            representative['subject'] = self._thread_subject_for_messages(group)
            representative['thread_count'] = count
            representative['thread_key'] = key
            representatives.append(representative)
        ordered_msgs = sorted(
            representatives + singletons,
            key=lambda item: item.get('date') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        self._prefetch_generation += 1
        items = self._build_message_items(ordered_msgs, has_more=has_more)
        self._message_store.splice(0, 0, items)
        self._list_stack.set_visible_child_name('list')
        if self._pending_list_scroll_value is not None:
            GLib.idle_add(self._restore_pending_list_scroll)
        if persist_snapshot:
            self._store_message_snapshot(ordered_msgs)
        if prefetch_bodies:
            self._prefetch_bodies(ordered_msgs)
        self._active_email_row = None
        should_commit_selected = False
        if preserve_selected_key:
            for item in items:
                if not isinstance(item, MessageListItem):
                    continue
                msg = item.msg
                if (
                    msg.get('account', ''),
                    msg.get('folder', ''),
                    msg.get('uid', ''),
                ) == preserve_selected_key:
                    self._select_message_item(item, suppress=True)
                    self._active_email_row = item
                    break
            if self._active_email_row is None:
                preserved_group = None
                for key, group in groups.items():
                    if preserve_selected_key in {
                        (m.get('account', ''), m.get('folder', ''), m.get('uid', ''))
                        for m in group
                    }:
                        preserved_group = key
                        break
                if preserved_group is not None:
                    representative_item = next(
                        (item for item in items if isinstance(item, MessageListItem) and item.msg.get('thread_key') == preserved_group),
                        None,
                    )
                    if representative_item is not None:
                        self._select_message_item(representative_item, suppress=True)
                        self._active_email_row = representative_item
                        should_commit_selected = True
            if self._active_email_row is None:
                for index in range(self._filtered_message_model.get_n_items()):
                    item = self._filtered_message_model.get_item(index)
                    if isinstance(item, MessageListItem):
                        self._set_selected_visible_index(index, suppress=True)
                        self._active_email_row = item
                        should_commit_selected = True
                        break
            elif (
                self._active_email_row is not None and
                (
                    self._active_email_row.msg.get('account', ''),
                    self._active_email_row.msg.get('folder', ''),
                    self._active_email_row.msg.get('uid', ''),
                ) != preserve_selected_key
            ):
                should_commit_selected = True
        elif self._startup_autoselect_pending and self.current_folder in (_UNIFIED, 'INBOX', 'inbox'):
            for index in range(self._filtered_message_model.get_n_items()):
                item = self._filtered_message_model.get_item(index)
                if not isinstance(item, MessageListItem):
                    continue
                self._startup_autoselect_pending = False
                self._set_selected_visible_index(index, grab_focus=True)
                self._active_email_row = item
                should_commit_selected = True
                break
        if should_commit_selected and self._active_email_row is not None:
            self._commit_email_selection(self._active_email_row)
        _log_perf(
            'set messages',
            f'{len(msgs)} msgs -> {len(ordered_msgs)} rows + {1 if has_more else 0} pager, {len(groups)} thread groups [{source}]',
            started=started,
        )
        return False

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
                except Exception as e:
                    _log_exception(f'Prefetch failed ({backend.identity}, {folder}, {uid})', e)

        threading.Thread(target=run, daemon=True).start()

    def _should_seed_recent_cache(self):
        return self.current_folder in (_UNIFIED, 'INBOX', 'inbox')

    def _message_list_context_key(self, backend=None, folder=None):
        backend = self.current_backend if backend is None else backend
        folder = self.current_folder if folder is None else folder
        if folder in (_UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM):
            return ('unified', folder)
        if backend and folder:
            return (backend.identity, folder)
        return None

    def _snapshot_messages_from_payload(self, records, default_folder, backend_context):
        msgs = []
        for m in records:
            try:
                date_val = m.get('date')
                date = datetime.fromisoformat(date_val) if date_val else datetime.now(timezone.utc)
            except Exception:
                date = datetime.now(timezone.utc)
            account = m.get('account', '')
            backend_obj = (
                backend_context
                if backend_context and account == backend_context.identity
                else _backend_for_identity(self.backends, account)
            )
            msgs.append({
                'uid': m.get('uid', ''),
                'subject': m.get('subject', '(no subject)'),
                'sender_name': m.get('sender_name', ''),
                'sender_email': m.get('sender_email', ''),
                'to_addrs': m.get('to_addrs', []),
                'cc_addrs': m.get('cc_addrs', []),
                'date': date,
                'is_read': m.get('is_read', True),
                'has_attachments': m.get('has_attachments', False),
                'snippet': m.get('snippet', ''),
                'folder': m.get('folder', default_folder),
                'backend': m.get('backend', ''),
                'account': account,
                'thread_id': m.get('thread_id', ''),
                'thread_source': m.get('thread_source', ''),
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
        accounts = sorted(b.identity for b in self.backends)
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
            except Exception as e:
                _log_exception(f'Snapshot load failed ({scope})', e)
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
        accounts = [b.identity for b in self.backends]
        default_folder = self.current_folder
        queued_msgs = [dict(m) for m in (msgs or [])[:100]]
        payload = build_snapshot_payload(scope, accounts, queued_msgs, default_folder)
        self._snapshot_save_queue.enqueue(scope, payload)
        _log_perf('snapshot save', f'{scope} {len(queued_msgs)} msgs queued', started=started)

    def _count_bucket_for_folder(self, folder):
        folder = (folder or '').lower()
        if folder in (_UNIFIED, 'inbox'):
            return 'inbox'
        if 'trash' in folder or 'deleteditems' in folder:
            return 'trash'
        if 'spam' in folder or 'junk' in folder:
            return 'spam'
        return None

    def _adjust_unread_count_for_message(self, msg, delta):
        backend_identity = msg.get('account') or (msg.get('backend_obj').identity if msg.get('backend_obj') else None)
        if not backend_identity:
            return
        bucket = self._count_bucket_for_folder(msg.get('folder'))
        if bucket != 'inbox':
            return
        counts = self._unread_counts[backend_identity]
        counts['inbox'] = max(0, counts['inbox'] + delta)
        self.update_account_counts(backend_identity, inbox_count=counts['inbox'])

    def _folder_id_for_name(self, backend_identity, display_name):
        state = self._account_state.get(backend_identity)
        if not state:
            return None
        backend = state['header'].backend
        return next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == display_name), None)

    def update_account_counts(self, backend_identity, inbox_count=None, trash_count=None, spam_count=None):
        counts = self._unread_counts[backend_identity]
        if inbox_count is not None:
            counts['inbox'] = inbox_count
        if trash_count is not None:
            counts['trash'] = trash_count
        if spam_count is not None:
            counts['spam'] = spam_count

        inbox_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Inbox')))
        if inbox_row:
            inbox_row.set_count(counts['inbox'])

        trash_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Trash')))
        if trash_row:
            trash_row.set_count(counts['trash'], dim=True)

        spam_row = self._folder_rows.get((backend_identity, self._folder_id_for_name(backend_identity, 'Spam')))
        if spam_row:
            spam_row.set_count(counts['spam'], dim=True)

        state = self._account_state.get(backend_identity)
        if state:
            state['header'].set_count(counts['inbox'])

        total = sum(account_counts['inbox'] for account_counts in self._unread_counts.values())
        if self._all_inboxes_row:
            self._all_inboxes_row.set_count(total)

    def update_folder_count(self, backend_identity, folder_id, count):
        state = self._account_state.get(backend_identity)
        if not state:
            return
        backend = state['header'].backend
        folder_name = next((name for fid, name, _icon in backend.FOLDERS if fid == folder_id), None)
        row = self._folder_rows.get((backend_identity, folder_id))
        if row:
            row.set_count(count, dim=folder_name in ('Trash', 'Spam'))

        if folder_name == 'Inbox':
            self.update_account_counts(backend_identity, inbox_count=count)
        elif folder_name == 'Trash':
            self.update_account_counts(backend_identity, trash_count=count)
        elif folder_name == 'Spam':
            self.update_account_counts(backend_identity, spam_count=count)

    def show_sync_badge(self, n):
        if n > 0:
            self._sync_badge.set_label(f'+{n}')
            self._sync_badge.set_visible(True)
            GLib.timeout_add(5000, self._hide_sync_badge)

    def _hide_sync_badge(self):
        self._sync_badge.set_visible(False)
        return False

"""Message-list view, selection, and paging behavior for HermodWindow."""

import collections
import threading
from datetime import datetime, timezone

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

try:
    from .backends import network_ready, is_transient_network_error
    from .command_palette import CommandPalette
    from .compose import ComposeView
    from .settings import get_settings
    from .widgets import (
        DayGroupListItem, DayGroupRow, EmailRow, AccountHeaderRow, FolderRow,
        LoadMoreListItem, LoadMoreRow, MessageListItem, MoreFoldersRow,
        SidebarSectionRow, UnifiedRow,
    )
    from .snapshot_cache import snapshot_result_applicable
    from .utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _day_group_key, _day_group_label,
        _perf_counter, _log_perf,
        _pick_icon_name,
    )
    from .window_constants import (
        MESSAGE_LIST_MAX_WIDTH, MESSAGE_LIST_MIN_WIDTH, MESSAGE_PAGE_STEP,
    )
except ImportError:
    from backends import network_ready, is_transient_network_error
    from command_palette import CommandPalette
    from compose import ComposeView
    from settings import get_settings
    from widgets import (
        DayGroupListItem, DayGroupRow, EmailRow, AccountHeaderRow, FolderRow,
        LoadMoreListItem, LoadMoreRow, MessageListItem, MoreFoldersRow,
        SidebarSectionRow, UnifiedRow,
    )
    from snapshot_cache import snapshot_result_applicable
    from utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _UNIFIED_FLAGGED, _UNIFIED_DRAFTS, _UNIFIED_SENT, _UNIFIED_ARCHIVE,
        _day_group_key, _day_group_label,
        _perf_counter, _log_perf,
        _pick_icon_name,
    )
    from window_constants import (
        MESSAGE_LIST_MAX_WIDTH, MESSAGE_LIST_MIN_WIDTH, MESSAGE_PAGE_STEP,
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

    def _set_message_loading(self, loading, generation=None):
        if loading:
            self._message_loading = True
            if generation is not None:
                self._message_loading_generation = generation
        else:
            current_generation = getattr(self, '_message_loading_generation', None)
            if generation is not None and current_generation not in (None, generation):
                return
            self._message_loading = False
            self._message_loading_generation = None
        self._sync_message_toolbar_controls()

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
        if getattr(self, '_message_loading', False):
            return
        if getattr(self, '_email_scroll', None) is not None:
            adj = self._email_scroll.get_vadjustment()
            if adj is not None:
                self._pending_list_scroll_value = adj.get_value()
        self._set_message_loading(True)
        self._message_page_limit += MESSAGE_PAGE_STEP
        self.refresh_visible_mail(force=True, preserve_selected=True)

    def _on_sort_changed(self, order):
        if self._sort_order == order:
            return
        self._sort_order = order
        self.refresh_visible_mail(force=True, preserve_selected=True)

    def _populate_sidebar(self):
        s = get_settings()

        self.folder_list.append(SidebarSectionRow('MAILBOXES'))

        self._all_inboxes_row = UnifiedRow(_UNIFIED, 'All Inboxes', 'hermod-inbox-symbolic')
        self._folder_rows[(_UNIFIED, _UNIFIED)] = self._all_inboxes_row
        self.folder_list.append(self._all_inboxes_row)

        self._flagged_row = UnifiedRow(_UNIFIED_FLAGGED, 'Flagged', 'hermod-flag-symbolic')
        self._folder_rows[(_UNIFIED_FLAGGED, _UNIFIED_FLAGGED)] = self._flagged_row
        self.folder_list.append(self._flagged_row)

        self._drafts_row = UnifiedRow(_UNIFIED_DRAFTS, 'Drafts', 'hermod-pencil-symbolic')
        self._folder_rows[(_UNIFIED_DRAFTS, _UNIFIED_DRAFTS)] = self._drafts_row
        self.folder_list.append(self._drafts_row)

        self._sent_row = UnifiedRow(_UNIFIED_SENT, 'Sent', 'hermod-send-symbolic')
        self._folder_rows[(_UNIFIED_SENT, _UNIFIED_SENT)] = self._sent_row
        self.folder_list.append(self._sent_row)

        self._archive_row = UnifiedRow(_UNIFIED_ARCHIVE, 'Archive', 'hermod-archive-symbolic')
        self._folder_rows[(_UNIFIED_ARCHIVE, _UNIFIED_ARCHIVE)] = self._archive_row
        self.folder_list.append(self._archive_row)

        if s.get('show_unified_trash'):
            trash_row = UnifiedRow(_UNIFIED_TRASH, 'Trash', 'hermod-trash-symbolic')
            self._folder_rows[(_UNIFIED_TRASH, _UNIFIED_TRASH)] = trash_row
            self.folder_list.append(trash_row)
        if s.get('show_unified_spam'):
            spam_row = UnifiedRow(_UNIFIED_SPAM, 'All Spam', 'hermod-trash-symbolic')
            self._folder_rows[(_UNIFIED_SPAM, _UNIFIED_SPAM)] = spam_row
            self.folder_list.append(spam_row)

        if self.backends:
            self.folder_list.append(SidebarSectionRow('ACCOUNTS'))

        expand_by_default = len(self.backends) == 1
        for backend in self.backends:
            accent_class = self._account_class_for(backend.identity)
            header_row = AccountHeaderRow(backend.identity, accent_class=accent_class)
            header_row.set_label(backend.identity or getattr(backend, 'presentation_name', ''))
            header_row.backend = backend
            if expand_by_default:
                header_row.expanded = True
                header_row.chevron.set_from_icon_name(
                    _pick_icon_name('hermod-chevron-up-symbolic', 'pan-up-symbolic')
                )
            self.folder_list.append(header_row)

            folder_rows = []
            folders_list = [
                entry for entry in backend.FOLDERS
                if entry[1] != 'Spam'
            ]
            for i, (folder_id, name, icon) in enumerate(folders_list):
                is_last = (i == len(folders_list) - 1)
                row = FolderRow(folder_id, name, icon, indent=True, accent_class=accent_class, is_last=is_last)
                row.backend = backend
                row.set_visible(expand_by_default)
                self._folder_rows[(backend.identity, folder_id)] = row
                self.folder_list.append(row)
                folder_rows.append(row)

            more_row = MoreFoldersRow(accent_class=accent_class)
            more_row.backend = backend
            more_row.set_visible(expand_by_default)
            self.folder_list.append(more_row)

            self._account_state[backend.identity] = {
                'header': header_row,
                'folders': folder_rows,
                'more_row': more_row,
                'extra': [],
                'expanded': expand_by_default,
            }
            self._render_account_health(backend.identity)

    def _setup_shortcuts(self):
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _update_sync_status_labels(self):
        if self._network_offline:
            self._countdown_lbl.set_label('OFFLINE')
        elif self._syncing:
            self._countdown_lbl.set_label('SYNCING')
        else:
            self._countdown_lbl.set_label('ONLINE')

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
        if self._syncing:
            if hasattr(self, '_spin_finishing_id') and self._spin_finishing_id:
                GLib.source_remove(self._spin_finishing_id)
                self._spin_finishing_id = None
            self._sync_btn.add_css_class('sync-syncing')
        else:
            # Let the current rotation cycle finish rather than snapping back to 0°.
            # Wait one full animation cycle (1 s) then remove the spinning class.
            if hasattr(self, '_spin_finishing_id') and self._spin_finishing_id:
                GLib.source_remove(self._spin_finishing_id)
            def _clear_syncing():
                self._sync_btn.remove_css_class('sync-syncing')
                self._spin_finishing_id = None
                return False
            self._spin_finishing_id = GLib.timeout_add(1000, _clear_syncing)
        self._update_sync_status_labels()

    def _finish_sync(self, total_new=0):
        self.set_syncing(False)
        self._sync_in_flight = False
        if getattr(self, '_startup_status_active', False):
            self._startup_visible_ready = True
            self._schedule_startup_status_completion(total_new=total_new)
        elif total_new > 0:
            self.show_sync_badge(total_new)
            self.refresh_visible_mail(force=True)
        else:
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
            _pick_icon_name('hermod-chevron-up-symbolic', 'pan-up-symbolic')
            if visible
            else _pick_icon_name('hermod-chevron-down-symbolic', 'pan-down-symbolic')
        )
        for row in state['folders']:
            row.set_visible(visible)
        state['more_row'].set_visible(visible)
        extra_visible = visible and state['more_row'].expanded
        for row in state['extra']:
            row.set_visible(extra_visible)

    def _toggle_more_folders(self, more_row):
        import os, sys
        debug = os.environ.get('HERMOD_DEBUG_FOLDERS')
        identity = more_row.backend.identity
        if debug:
            print(f"[folders] _toggle_more_folders identity={identity} loaded={more_row.loaded} expanded={getattr(more_row, 'expanded', None)}", file=sys.stderr, flush=True)
        state = self._account_state.get(identity)
        if state is None:
            if debug:
                print(f"[folders] _toggle_more_folders ABORT: no state for {identity}", file=sys.stderr, flush=True)
            return
        if not more_row.loaded:
            if more_row.spinner.get_spinning():
                return
            more_row.spinner.set_spinning(True)
            done_event = threading.Event()
            result_holder = {'folders': None, 'error': None}

            def fetch():
                try:
                    result_holder['folders'] = more_row.backend.fetch_all_folders()
                    if debug:
                        print(f"[folders] fetch_all_folders returned {len(result_holder['folders'] or [])} folders for {identity}", file=sys.stderr, flush=True)
                except Exception as exc:
                    result_holder['error'] = exc
                    if debug:
                        print(f"[folders] fetch_all_folders EXCEPTION: {exc!r}", file=sys.stderr, flush=True)
                finally:
                    done_event.set()

            threading.Thread(target=fetch, daemon=True).start()

            def watchdog():
                # Give the backend ~12s; if it hasn't returned, clear the spinner
                # and surface a toast so the user isn't staring at a silent hang.
                if not done_event.wait(12.0):
                    if debug:
                        print(f"[folders] fetch_all_folders TIMEOUT for {identity}", file=sys.stderr, flush=True)
                    GLib.idle_add(lambda: (more_row.spinner.set_spinning(False), self._show_toast and self._show_toast('Loading folders is taking too long.')))
                    return
                if result_holder['error'] is not None:
                    GLib.idle_add(lambda: (more_row.spinner.set_spinning(False), self._show_toast and self._show_toast('Could not load extra folders.')))
                    return
                GLib.idle_add(self._on_extra_folders_loaded, more_row, result_holder['folders'])

            threading.Thread(target=watchdog, daemon=True).start()
            return
        more_row.set_expanded(not more_row.expanded)
        for row in state['extra']:
            row.set_visible(more_row.expanded)

    def _on_extra_folders_loaded(self, more_row, folders):
        import os, sys
        debug = os.environ.get('HERMOD_DEBUG_FOLDERS')
        identity = more_row.backend.identity
        state = self._account_state.get(identity)
        more_row.spinner.set_spinning(False)
        if state is None:
            if debug:
                print(f"[folders] _on_extra_folders_loaded ABORT: no state for {identity}", file=sys.stderr, flush=True)
            return
        if not folders:
            if debug:
                print(f"[folders] _on_extra_folders_loaded: no extra folders for {identity}, hiding More row", file=sys.stderr, flush=True)
            more_row.set_visible(False)
            return
        if debug:
            print(f"[folders] _on_extra_folders_loaded: inserting {len(folders)} extra folders for {identity}", file=sys.stderr, flush=True)
        more_row.loaded = True
        more_row.set_expanded(True)
        insert_pos = more_row.get_index() + 1
        new_rows = []
        folders_list = list(folders)
        accent_class = self._account_class_for(more_row.backend.identity)
        for i, (folder_id, name, icon) in enumerate(folders_list):
            is_last = (i == len(folders_list) - 1)
            row = FolderRow(folder_id, name, icon, indent=True, is_last=is_last, accent_class=accent_class)
            row.backend = more_row.backend
            row.set_visible(more_row.expanded)
            self._folder_rows[(identity, folder_id)] = row
            self.folder_list.insert(row, insert_pos)
            insert_pos += 1
            new_rows.append(row)
        state['extra'] = new_rows
        if debug:
            print(f"[folders] _on_extra_folders_loaded: DONE inserting {len(new_rows)} rows", file=sys.stderr, flush=True)

    def _setup_email_list_item(self, _factory, list_item):
        list_item.connect('notify::selected', self._on_email_list_item_selected_changed)

    def _bind_email_list_item(self, _factory, list_item):
        item = list_item.get_item()
        if isinstance(item, DayGroupListItem):
            child = DayGroupRow(item.label)
            list_item.set_selectable(False)
            list_item.set_activatable(False)
        elif isinstance(item, MessageListItem):
            list_item.set_selectable(True)
            list_item.set_activatable(True)
            child = EmailRow(
                item.msg,
                self._on_reply,
                self._on_reply_all,
                self._on_delete,
                accent_class=item.accent_class,
            )
        elif isinstance(item, LoadMoreListItem):
            list_item.set_selectable(False)
            list_item.set_activatable(True)
            child = LoadMoreRow(item.label, self._on_load_more_requested)
        else:
            child = Gtk.Box()
        list_item.set_child(child)
        if hasattr(item, 'bind_widget'):
            item.bind_widget(child)
        if hasattr(child, 'set_selected'):
            # Use our application-level selection state, not GTK's, to avoid
            # losing visual selection when focus moves to the reading pane.
            is_active = (getattr(self, '_active_email_row', None) is item)
            child.set_selected(is_active)

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
        if isinstance(item, DayGroupListItem):
            return
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
                self.set_filter_mode('unified')
                self._load_unified_inbox()
            elif row.folder_id == _UNIFIED_FLAGGED:
                self.set_filter_mode('flagged')
                self._load_unified_inbox()
            elif row.folder_id == _UNIFIED_DRAFTS:
                self.set_filter_mode('unified')
                self._load_unified_folder('Drafts')
            elif row.folder_id == _UNIFIED_SENT:
                self.set_filter_mode('unified')
                self._load_unified_folder('Sent')
            elif row.folder_id == _UNIFIED_ARCHIVE:
                self.set_filter_mode('unified')
                self._load_unified_folder('Archive')
            elif row.folder_id == _UNIFIED_TRASH:
                self.set_filter_mode('unified')
                self._load_unified_folder('Trash')
            elif row.folder_id == _UNIFIED_SPAM:
                self.set_filter_mode('unified')
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
        # Explicit user selection always dismisses the startup overlay —
        # otherwise the reader body would render into a hidden stack child
        # while the "Starting mail" panel keeps covering the viewer.
        if getattr(self, '_startup_status_active', False) and hasattr(self, '_clear_startup_status_view'):
            self._clear_startup_status_view()
        prev = getattr(self, '_active_email_row', None)
        if prev is not None and prev is not row and prev.widget is not None:
            prev.widget.set_selected(False)
        self._active_email_row = row
        if row.widget is not None:
            row.widget.set_selected(True)
        else:
            # Widget may not be bound yet if list view is still laying out;
            # retry on next idle tick.
            def _apply_selected():
                if row.widget is not None:
                    row.widget.set_selected(True)
                return False
            GLib.idle_add(_apply_selected)
        mark_on_open = get_settings().get('mark_read_on_open')
        was_unread = not row.msg.get('is_read', True)
        self._show_mail_view()
        self._body_load_generation += 1
        if hasattr(self, '_info_actions') and self._info_actions:
            self._info_actions.set_visible(True)
        if row.msg.get('thread_count', 1) > 1:
            self._load_thread_view(row.msg, self._body_load_generation)
        else:
            self._load_body(row.msg, self._body_load_generation)
        if mark_on_open:
            row.mark_read()
        if was_unread and mark_on_open:
            self._sync_backend_cached_read_state(row.msg, True)
            if hasattr(self, '_message_filter'):
                self._message_filter.changed(Gtk.FilterChange.DIFFERENT)
            self._update_message_empty_state()
            self._refresh_provider_counts_for_message(
                row.msg,
                row.msg.get('backend_obj') or getattr(self, 'current_backend', None),
            )

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
        self._force_primary_probes()
        self._sync_in_flight = True
        self.set_syncing(True)
        self._offline_refresh_pending = False
        preserve_key = self._selected_message_key()
        if self.current_folder == _UNIFIED:
            self._load_unified_inbox(preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_FLAGGED:
            self._load_unified_inbox(preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_DRAFTS:
            self._load_unified_folder('Drafts', preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_SENT:
            self._load_unified_folder('Sent', preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
        elif self.current_folder == _UNIFIED_ARCHIVE:
            self._load_unified_folder('Archive', preserve_selected_key=preserve_key, sync_complete_callback=self._finish_sync)
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
        win = getattr(self, '_settings_window', None)
        if win is not None and win.get_visible():
            self._close_settings_modal()
            return
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

    def _on_forward(self, msg):
        if self._compose_active():
            return
        backend = msg.get('backend_obj') or self.current_backend
        if backend:
            self._present_compose(
                ComposeView(
                    self,
                    backend,
                    self.backends,
                    forward_from=msg,
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
        self._refresh_message_meta()

    def _show_settings_view(self):
        if hasattr(self, '_has_accounts') and not self._has_accounts():
            if hasattr(self, '_show_welcome_settings_view'):
                self._show_welcome_settings_view()
            return
        if hasattr(self, '_present_settings_modal'):
            self._present_settings_modal()

    def _show_mail_view(self):
        if hasattr(self, '_has_accounts') and not self._has_accounts():
            if hasattr(self, '_show_welcome_mode'):
                self._show_welcome_mode()
            return
        if getattr(self, '_startup_status_active', False):
            self._show_startup_status_view()
        else:
            self._clear_startup_status_view()
            self._viewer_stack.set_visible_child_name('viewer')
        self._settings_btn.set_icon_name('emblem-system-symbolic')
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
                self._refresh_provider_counts_for_message(msg, backend)
                GLib.idle_add(self._show_toast, 'Message deleted')
                if hasattr(self, '_message_filter'):
                    GLib.idle_add(self._message_filter.changed, Gtk.FilterChange.DIFFERENT)
                GLib.idle_add(self._update_message_empty_state)
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
        if hasattr(self, '_message_filter'):
            self._message_filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_message_empty_state()

        def do_mark():
            try:
                backend.mark_as_unread(msg['uid'], msg.get('folder'))
                self._refresh_provider_counts_for_message(msg, backend)
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Failed: {e}')

        threading.Thread(target=do_mark, daemon=True).start()

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().lower()
        self._message_filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_message_empty_state()

    def _email_filter(self, item, *_args):
        if isinstance(item, DayGroupListItem):
            followers = getattr(item, 'followers', None) or []
            if not followers:
                return True
            return any(self._message_passes_filters(follower.msg) for follower in followers)
        if isinstance(item, LoadMoreListItem):
            return True
        if not isinstance(item, MessageListItem):
            return True
        return self._message_passes_filters(item.msg)

    def _message_passes_filters(self, msg):
        mode = getattr(self, '_filter_mode', 'unified')
        if mode == 'unread' and msg.get('is_read', True):
            return False
        if mode == 'flagged' and not msg.get('is_flagged', False):
            return False
        # Legacy toggle preserved for tests / external callers.
        if getattr(self, '_show_unread_only', False) and msg.get('is_read', True):
            return False
        return self._message_matches_search(msg)

    def _message_matches_search(self, msg):
        if not self._search_text:
            return True
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
            if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_k, Gdk.KEY_K):
                self._open_command_palette()
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
        if key == 'f':
            row = self._selected_message_row()
            if row:
                self._on_forward(row.msg)
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
        # Skip over day-group headers so keyboard navigation lands on a message.
        step = 1 if delta >= 0 else -1
        probe = next_index
        while 0 <= probe < count and isinstance(
            self._visible_message_item(probe), DayGroupListItem
        ):
            probe += step
        if 0 <= probe < count:
            next_index = probe
        self._set_selected_visible_index(next_index, grab_focus=True)
        item = self._visible_message_item(next_index)
        if isinstance(item, MessageListItem):
            self._request_commit_email_selection(item)

    def _begin_message_load(self):
        self._message_load_generation += 1
        self._prefetch_generation += 1
        return self._message_load_generation

    def _toggle_unread_only(self, active):
        active = bool(active)
        if getattr(self, '_show_unread_only', False) == active:
            return
        self._show_unread_only = active
        if active:
            self._unread_filter_had_results = False
        # Keep the segmented filter in sync when toggled via the legacy API.
        desired_mode = 'unread' if active else 'unified'
        if getattr(self, '_filter_mode', 'unified') != desired_mode:
            self._filter_mode = desired_mode
            self._sync_filter_segmented_buttons()
        self._sync_unread_toggle_button()
        self._message_filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_message_empty_state()
        self._refresh_message_meta()

    def set_filter_mode(self, mode):
        """Select the Unified / Unread / Flagged filter segment."""
        mode = mode if mode in ('unified', 'unread', 'flagged') else 'unified'
        if getattr(self, '_filter_mode', 'unified') == mode:
            return
        self._filter_mode = mode
        self._show_unread_only = (mode == 'unread')
        if mode == 'unread':
            self._unread_filter_had_results = False
        self._sync_filter_segmented_buttons()
        self._sync_unread_toggle_button()
        self._message_filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_message_empty_state()
        self._refresh_message_meta()

    def _sync_filter_segmented_buttons(self):
        buttons = getattr(self, '_filter_segmented_buttons', None) or {}
        mode = getattr(self, '_filter_mode', 'unified')
        for key, btn in buttons.items():
            is_active = (key == mode)
            if btn.get_active() != is_active:
                btn.set_active(is_active)
            if is_active:
                btn.add_css_class('selected')
            else:
                btn.remove_css_class('selected')

    def _sync_unread_toggle_button(self):
        button = getattr(self, '_unread_toggle_btn', None)
        if button is None:
            return
        button.set_active(bool(getattr(self, '_show_unread_only', False)))
        button.set_tooltip_text('Show all mail' if getattr(self, '_show_unread_only', False) else 'Unread only')
        if getattr(self, '_show_unread_only', False):
            button.add_css_class('active')
        else:
            button.remove_css_class('active')

    def _unread_view_count(self):
        count = 0
        for index in range(self._message_store.get_n_items()):
            item = self._message_store.get_item(index)
            if not isinstance(item, MessageListItem):
                continue
            msg = item.msg
            if msg.get('is_read', True):
                continue
            if not self._message_matches_search(msg):
                continue
            count += 1
        return count

    def _message_view_counts(self):
        total = 0
        unread = 0
        for index in range(self._message_store.get_n_items()):
            item = self._message_store.get_item(index)
            if not isinstance(item, MessageListItem):
                continue
            total += 1
            if not item.msg.get('is_read', True):
                unread += 1
        return total, unread

    def _refresh_message_meta(self):
        eyebrow_lbl = getattr(self, '_message_col_eyebrow', None)
        meta_lbl = getattr(self, '_message_col_meta', None)
        if eyebrow_lbl is None or meta_lbl is None:
            return
        title = (getattr(self, '_content_title', '') or '').strip()
        if getattr(self, '_content_subtitle', ''):
            eyebrow = title or 'MAILBOX'
        else:
            eyebrow = title or 'ALL INBOXES'
        eyebrow_lbl.set_label(eyebrow.upper())
        total, unread = self._message_view_counts()
        if total == 0:
            meta_lbl.set_label('No messages')
        else:
            noun = 'message' if total == 1 else 'messages'
            if unread:
                meta_lbl.set_label(f'{total} {noun} · {unread} unread')
            else:
                meta_lbl.set_label(f'{total} {noun}')

    def _update_message_empty_state(self):
        if not hasattr(self, '_filtered_message_model') or not hasattr(self, '_empty_page') or not hasattr(self, '_list_stack') or not hasattr(self, '_message_store'):
            return
        if getattr(self, '_show_unread_only', False):
            unread_count = self._unread_view_count()
            if unread_count > 0:
                self._unread_filter_had_results = True
                self._list_stack.set_visible_child_name('list')
                return
            elif self._search_text:
                title = 'No matching unread messages'
                desc = 'Try a different search.'
            elif self._unread_filter_had_results:
                title = 'All caught up'
                desc = 'Nice work. You cleared every unread message.'
            else:
                title = 'No unread messages'
                desc = 'You are already caught up.'
        else:
            visible_count = self._filtered_message_model.get_n_items()
            if visible_count > 0:
                self._list_stack.set_visible_child_name('list')
                return
            if self._search_text:
                title = 'No matches'
                desc = 'Try a different search.'
            else:
                title = 'No messages'
                desc = None
        self._empty_page.set_title(title)
        self._empty_page.set_description(desc)
        self._list_stack.set_visible_child_name('empty')

    def _restore_focus_widget(self, widget):
        try:
            if widget is not None and widget.get_root() is self:
                widget.grab_focus()
        except Exception:
            pass
        return False

    def _build_message_items(self, msgs, has_more=False):
        items = []
        sort_order = getattr(self, '_sort_order', 'newest')
        current_group = None
        for msg in msgs:
            accent_class = self._account_class_for(
                (msg.get('account') or (msg.get('backend_obj').identity if msg.get('backend_obj') else ''))
            )
            msg_item = MessageListItem(msg, accent_class=accent_class)
            group_key = _day_group_key(msg.get('date'))
            if group_key is not None and group_key != (current_group.date_key if current_group else None):
                label = _day_group_label(msg.get('date')) or ''
                current_group = DayGroupListItem(label, date_key=group_key)
                items.append(current_group)
            if current_group is not None:
                current_group.followers.append(msg_item)
            items.append(msg_item)
        if has_more and sort_order != 'oldest':
            load_more = LoadMoreListItem()
            items.append(load_more)
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
        if source in {'live', 'provider-cache'} and generation is not None:
            self._message_live_generation = max(self._message_live_generation, generation)
        started = _perf_counter()
        self._set_message_loading(False, generation)
        msgs = [dict(msg) for msg in (msgs or [])]
        sort_order = getattr(self, '_sort_order', 'newest')
        self._displayed_message_list_key = self._message_list_context_key()
        self._message_has_more = bool(has_more)
        self._sync_message_toolbar_controls()
        self._message_store.splice(0, self._message_store.get_n_items(), [])
        if not msgs:
            self._thread_groups = {}
            self._prefetch_generation += 1
            self._message_has_more = False
            self._sync_message_toolbar_controls()
            self._update_message_empty_state()
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
            reverse=(sort_order != 'oldest'),
        )
        self._prefetch_generation += 1
        items = self._build_message_items(ordered_msgs, has_more=has_more)
        self._message_store.splice(0, 0, items)
        self._list_stack.set_visible_child_name('list')
        if source == 'live' and getattr(self, '_startup_status_active', False):
            panel = getattr(self, '_startup_status_panel', None)
            blocking_attention = False
            if panel is not None:
                if hasattr(panel, 'has_blocking_attention'):
                    blocking_attention = panel.has_blocking_attention()
                elif hasattr(panel, 'has_attention'):
                    blocking_attention = panel.has_attention()
            if not blocking_attention:
                self._startup_visible_ready = True
                self._clear_startup_status_view()
                if hasattr(self, '_refresh_all_unread_counts'):
                    self._refresh_all_unread_counts()
                if getattr(self, '_viewer_stack', None) is not None:
                    self._viewer_stack.set_visible_child_name('viewer')
                self._show_mail_view()
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
        self._update_message_empty_state()
        self._refresh_message_meta()
        _log_perf(
            'set messages',
            f'{len(msgs)} msgs -> {len(ordered_msgs)} rows + {1 if has_more else 0} pager, {len(groups)} thread groups [{source}]',
            started=started,
        )
        return False

    def show_sync_badge(self, n):
        if n > 0:
            self._sync_badge.set_label(f'+{n}')
            self._sync_badge.set_visible(True)
            GLib.timeout_add(5000, self._hide_sync_badge)

    def _hide_sync_badge(self):
        self._sync_badge.set_visible(False)
        return False

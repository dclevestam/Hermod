import base64
import collections
import gzip
import hashlib
import json
import html as html_lib
import re
import sys
import threading
import traceback
import time
from datetime import datetime, timezone
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, Adw, GLib, WebKit, Pango, Gdk
try:
    from .backends import network_ready, is_transient_network_error
    from .styles import CSS, build_window_account_css, account_class_for_index
    from .settings import get_settings, get_disk_cache_budget_limit_mb
    from .widgets import EmailRow, ThreadNavRow, UnifiedRow, FolderRow, AccountHeaderRow, MoreFoldersRow
    from .thread_renderer import build_thread_html, thread_reply_msg_for_records
    from .body_cache import load_disk_body, store_disk_body, prune_disk_body_cache
    from .utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR, _SNAPSHOT_CACHE_DIR,
        _format_date, _format_received_date, _thread_day_label,
        _format_size, _pick_icon_name, _log_exception,
        _body_cache_key, _disk_cache_budget_bytes,
        _snapshot_scope, _snapshot_path,
        _attachment_content_id, _attachment_is_inline_image,
        _attachment_cacheable, _inline_image_data_uri, _replace_cid_images,
        _make_count_slot,
        _normalize_thread_subject, _html_to_text, _strip_thread_quotes,
        _thread_message_summary, _thread_day_label,
        _rgb_to_hex, _sender_key, _sender_initials,
        _thread_palette, _thread_color_map, _email_background_hint,
        _backend_for_identity, _backend_for_message,
        _demo_thread_fixture,
    )
except ImportError:
    from backends import network_ready, is_transient_network_error
    from styles import CSS, build_window_account_css, account_class_for_index
    from settings import get_settings, get_disk_cache_budget_limit_mb
    from widgets import EmailRow, ThreadNavRow, UnifiedRow, FolderRow, AccountHeaderRow, MoreFoldersRow
    from thread_renderer import build_thread_html, thread_reply_msg_for_records
    from body_cache import load_disk_body, store_disk_body, prune_disk_body_cache
    from utils import (
        _UNIFIED, _UNIFIED_TRASH, _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR, _SNAPSHOT_CACHE_DIR,
        _format_date, _format_received_date, _thread_day_label,
        _format_size, _pick_icon_name, _log_exception,
        _body_cache_key, _disk_cache_budget_bytes,
        _snapshot_scope, _snapshot_path,
        _attachment_content_id, _attachment_is_inline_image,
        _attachment_cacheable, _inline_image_data_uri, _replace_cid_images,
        _make_count_slot,
        _normalize_thread_subject, _html_to_text, _strip_thread_quotes,
        _thread_message_summary, _thread_day_label,
        _rgb_to_hex, _sender_key, _sender_initials,
        _thread_palette, _thread_color_map, _email_background_hint,
        _backend_for_identity, _backend_for_message,
        _demo_thread_fixture,
    )

_BODY_CACHE_LIMIT = 8
_PREFETCH_WARMUP_LIMIT = 4
_SIDEBAR_MIN_WIDTH = 300
_SIDEBAR_MAX_WIDTH = 300
_MESSAGE_LIST_MIN_WIDTH = 320
_MESSAGE_LIST_MAX_WIDTH = 680


# ── Main window ───────────────────────────────────────────────────────────────

class LarkWindow(Adw.ApplicationWindow):
    def __init__(self, app, backends):
        super().__init__(application=app, title='Lark')
        self.set_default_size(1520, 920)
        self.backends = backends
        self.current_backend = None
        self.current_folder = None
        self._folder_rows = {}
        self._account_state = {}
        self._search_text = ''
        self._unread_counts = collections.defaultdict(lambda: {'inbox': 0, 'trash': 0, 'spam': 0})
        self._all_inboxes_row = None
        self._countdown_seconds = 0
        self._syncing = False
        self._sync_in_flight = False
        self._sync_dots = 0
        self._body_cache = collections.OrderedDict()
        self._cache_lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._diag_ops = {}
        self._diag_watchdog_id = None
        self._thread_groups = {}
        self._current_thread_messages = None
        self._thread_view_active = False
        self._thread_reply_target = None
        self._compose_view = None
        self._active_folder_row = None
        self._active_email_row = None
        self._suppress_folder_selection = False
        self._suppress_email_selection = False
        self._close_after_compose_prompt = False
        self._network_offline = False
        self._offline_refresh_pending = False
        self._offline_body_pending = False
        self._background_refresh_pending = False
        self._prefetch_generation = 0
        self._message_load_generation = 0
        self._body_load_generation = 0
        self._startup_autoselect_pending = True
        self._content_title = 'Lark'
        self._content_subtitle = ''
        self._account_classes = {b.identity: account_class_for_index(i) for i, b in enumerate(backends)}
        self._account_css = self._build_account_css()

        self._apply_css()
        self._build_ui()
        self._populate_sidebar()
        self._setup_shortcuts()
        self.connect('close-request', self._on_close_request)

        if len(self.backends) == 1:
            backend = self.backends[0]
            inbox_row = self._folder_rows.get((backend.identity, backend.FOLDERS[0][0]))
            if inbox_row:
                self.folder_list.select_row(inbox_row)
            elif self._all_inboxes_row:
                self.folder_list.select_row(self._all_inboxes_row)
        else:
            all_row = self._all_inboxes_row
            if all_row:
                self.folder_list.select_row(all_row)

        self._reset_countdown()
        GLib.timeout_add(1000, self._tick_countdown)
        self._diag_watchdog_id = GLib.timeout_add_seconds(5, self._diag_watchdog_tick)

        Adw.StyleManager.get_default().connect(
            'notify::dark', lambda *_: self._update_webview_bg()
        )

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS + self._account_css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_account_css(self):
        return build_window_account_css()

    def _account_class_for(self, identity):
        return self._account_classes.get(identity)

    def _on_close_request(self, *_):
        if self._close_after_compose_prompt:
            self._close_after_compose_prompt = False
            if self._diag_watchdog_id is not None:
                GLib.source_remove(self._diag_watchdog_id)
                self._diag_watchdog_id = None
            return False
        if self._compose_active():
            self._compose_view.request_close(
                lambda proceed: GLib.idle_add(self._finish_window_close_request, bool(proceed))
            )
            return True
        if get_settings().get('close_minimizes'):
            self.hide()
            return True
        if self._diag_watchdog_id is not None:
            GLib.source_remove(self._diag_watchdog_id)
            self._diag_watchdog_id = None
        return False

    def _finish_window_close_request(self, proceed):
        if proceed:
            if get_settings().get('close_minimizes'):
                self.hide()
            else:
                self._close_after_compose_prompt = True
                self.close()
        return False

    def _compose_active(self):
        return self._compose_view is not None and self._viewer_stack.get_visible_child_name() == 'compose'

    def _flash_action_feedback(self, widget):
        if widget is None:
            return
        widget.add_css_class('action-feedback')

        def clear():
            try:
                widget.remove_css_class('action-feedback')
            except Exception:
                pass
            return False

        GLib.timeout_add(120, clear)

    def _selected_message_key(self):
        row = self._active_email_row or self.email_list.get_selected_row()
        if row is None or not isinstance(row, EmailRow):
            return None
        msg = row.msg
        return (
            msg.get('account', ''),
            msg.get('folder', ''),
            msg.get('uid', ''),
        )

    def _thread_key_for_msg(self, msg):
        if not msg:
            return None
        thread_id = (msg.get('thread_id') or '').strip()
        if thread_id:
            return (msg.get('account', ''), msg.get('backend', ''), thread_id)
        return None

    def _thread_group_messages(self, msg):
        key = self._thread_key_for_msg(msg)
        if key is None:
            return [msg] if msg else []
        grouped = self._thread_groups.get(key)
        if grouped:
            return grouped
        return [msg] if msg else []

    def _thread_subject_for_messages(self, msgs):
        for m in reversed(msgs or []):
            subj = (m.get('subject') or '').strip()
            if subj:
                return subj
        return '(no subject)'

    def _thread_date_bounds(self, msgs):
        dates = [m.get('date') for m in (msgs or []) if m.get('date') is not None]
        if not dates:
            return '', ''
        try:
            first = min(dates)
            last = max(dates)
        except Exception:
            return '', ''
        return _format_received_date(first), _format_received_date(last)

    def _thread_participants_summary(self, msgs):
        seen = []
        for m in msgs or []:
            sender_name = (m.get('sender_name') or '').strip()
            sender_email = (m.get('sender_email') or '').strip()
            label = sender_name or sender_email or 'Unknown'
            if sender_email and sender_name and sender_email.lower() not in sender_name.lower():
                label = f'{sender_name}'
            if label not in seen:
                seen.append(label)
        if not seen:
            return 'Unknown sender'
        if len(seen) <= 3:
            return ' • '.join(seen)
        return ' • '.join(seen[:3]) + f' • +{len(seen) - 3} more'

    def _extract_thread_body(self, html, text):
        body = text or _html_to_text(html) or ''
        body = _strip_thread_quotes(body)
        return body.strip()

    def _message_is_self(self, msg):
        sender = (msg.get('sender_email') or '').strip().lower()
        if not sender:
            return False
        for backend in self.backends:
            identity = (backend.identity or '').strip().lower()
            if identity and sender == identity:
                return True
        return False

    def _sender_accent_rgb(self, seed_text):
        return _thread_palette(seed_text)

    def _thread_attachment_summary(self, attachments):
        count = len(attachments or [])
        if count == 0:
            return ''
        if count == 1:
            return '1 attachment'
        return f'{count} attachments'

    def _thread_sender_summary(self, msgs):
        seen = []
        for m in msgs or []:
            sender_name = (m.get('sender_name') or '').strip()
            sender_email = (m.get('sender_email') or '').strip()
            label = sender_name or sender_email or 'Unknown sender'
            if label not in seen:
                seen.append(label)
        if not seen:
            return 'Unknown sender'
        return ' • '.join(seen[:4]) + (f' • +{len(seen) - 4} more' if len(seen) > 4 else '')

    def _thread_sender_markup(self, msgs, sender_colors):
        seen = []
        parts = []
        for m in msgs or []:
            name = (m.get('sender_name') or m.get('sender_email') or 'Unknown sender').strip()
            key = _sender_key(m)
            if key in seen:
                continue
            seen.append(key)
            rgb = sender_colors.get(key)
            color_hex = _rgb_to_hex(rgb) if rgb else '#9aa0a6'
            parts.append(f'<span foreground="{color_hex}" weight="700">{html_lib.escape(name)}</span>')
        if not parts:
            return 'Unknown sender'
        return ' • '.join(parts)

    def _thread_is_open(self):
        return bool(getattr(self, '_thread_sidebar_revealer', None)) and self._thread_sidebar_revealer.get_reveal_child()

    def _set_thread_sidebar_visible(self, visible):
        if getattr(self, '_thread_sidebar_revealer', None) is None:
            return
        self._thread_sidebar_revealer.set_reveal_child(bool(visible))
        if visible:
            self._thread_messages_btn.add_css_class('active')
        else:
            self._thread_messages_btn.remove_css_class('active')

    def _populate_thread_sidebar(self, records):
        if getattr(self, '_thread_sidebar_list', None) is None:
            return
        while (row := self._thread_sidebar_list.get_row_at_index(0)):
            self._thread_sidebar_list.remove(row)
        ordered = sorted(
            list(records or []),
            key=lambda record: record.get('msg', {}).get('date') or datetime.min.replace(tzinfo=timezone.utc),
        )
        for record in ordered:
            row = ThreadNavRow(record, self._scroll_thread_to_message, accent_rgb=record.get('sender_color'))
            self._thread_sidebar_list.append(row)
        if ordered:
            self._thread_sidebar_list.select_row(self._thread_sidebar_list.get_row_at_index(len(ordered) - 1))

    def _on_thread_sidebar_row_activated(self, _listbox, row):
        if not isinstance(row, ThreadNavRow):
            return
        self._scroll_thread_to_message(row.record)

    def _scroll_thread_to_message(self, record):
        msg = (record or {}).get('msg') or {}
        uid = msg.get('uid', '')
        if not uid:
            return
        try:
            script = f"""
                (function() {{
                    const el = document.getElementById({json.dumps(f'msg-{uid}')});
                    if (el) {{
                        document.querySelectorAll('.bubble.selected').forEach((node) => node.classList.remove('selected'));
                        el.classList.add('selected');
                        el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                    }}
                }})();
            """
            self.webview.evaluate_javascript(script, len(script), None, None, None, None, None)
        except Exception:
            pass

    def _format_message_size(self, msg, attachments=None):
        size = msg.get('size')
        if isinstance(size, int) and size > 0:
            return _format_size(size)
        total = 0
        for att in attachments or []:
            try:
                total += int(att.get('size', 0) or 0)
            except Exception:
                continue
        if total > 0:
            return _format_size(total)
        return ''

    def _update_message_info_bar(self, msg, attachments=None):
        if msg is None:
            self._message_info_bar.set_visible(False)
            return
        subject = (msg.get('subject') or '(no subject)').strip()
        sender_name = (msg.get('sender_name') or '').strip()
        sender_email = (msg.get('sender_email') or '').strip()
        if sender_name and sender_email and sender_email.lower() not in sender_name.lower():
            sender = f'{sender_name} <{sender_email}>'
        else:
            sender = sender_name or sender_email or 'Unknown sender'
        size = self._format_message_size(msg, attachments)
        parts = []
        if size:
            parts.append(f'Size {size}')
        if attachments:
            parts.append(f'{len(attachments)} attachment{"s" if len(attachments) != 1 else ""}')
        self._message_info_sender.set_label(sender)
        self._message_info_date.set_label(f'Received: {_format_received_date(msg.get("date"))}')
        self._message_info_subject.set_label(subject)
        self._message_info_meta.set_label(' • '.join(parts))
        self._message_info_meta.set_visible(bool(parts))
        self._message_info_bar.set_visible(True)

    def _close_inline_compose(self, _compose=None):
        while (child := self._compose_holder.get_first_child()):
            self._compose_holder.remove(child)
        self._compose_view = None
        self._show_mail_view()

    def _finish_compose_leave_request(self, proceed, on_leave=None, on_cancel=None):
        if proceed:
            if callable(on_leave):
                on_leave()
        elif callable(on_cancel):
            on_cancel()
        return False

    def _request_leave_compose(self, on_leave, on_cancel=None):
        if not self._compose_active():
            on_leave()
            return
        self._compose_view.request_close(
            lambda proceed: GLib.idle_add(
                self._finish_compose_leave_request,
                bool(proceed),
                on_leave,
                on_cancel,
            )
        )

    def _start_background_op(self, kind, detail, hint):
        if not get_settings().get('debug_logging'):
            return None
        token = object()
        with self._diag_lock:
            self._diag_ops[token] = {
                'kind': kind,
                'detail': detail,
                'hint': hint,
                'started': time.monotonic(),
                'warned': False,
            }
        return token

    def _end_background_op(self, token):
        if token is None:
            return
        with self._diag_lock:
            self._diag_ops.pop(token, None)

    def _diag_watchdog_tick(self):
        if not get_settings().get('debug_logging'):
            return GLib.SOURCE_CONTINUE
        now = time.monotonic()
        stale = []
        with self._diag_lock:
            for op in self._diag_ops.values():
                age = now - op['started']
                if age >= 15 and not op['warned']:
                    op['warned'] = True
                    stale.append((op['kind'], op['detail'], op.get('hint'), age))
        for kind, detail, hint, age in stale:
            print(
                f'Watchdog: {kind} still running after {age:.1f}s ({detail})'
                + (f' | check: {hint}' if hint else ''),
                file=sys.stderr,
            )
        return GLib.SOURCE_CONTINUE

    def _build_ui(self):
        root = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_hexpand(True)
        self.title_widget = Adw.WindowTitle(title='Lark', subtitle='')
        header.set_title_widget(self.title_widget)

        # ── Left side: settings ──
        hamburger = Gtk.Button(icon_name='open-menu-symbolic', tooltip_text='Settings')
        hamburger.connect('clicked', self._on_settings)
        self._settings_btn = hamburger
        header.pack_start(hamburger)

        # Sync control: refresh icon on the left, sync status/timer on the right.
        online_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            homogeneous=False,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        online_box.set_size_request(120, 30)
        online_box.set_hexpand(False)
        online_box.set_vexpand(False)
        left_box = Gtk.CenterBox(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        left_box.add_css_class('sync-left-side')
        left_box.set_size_request(38, 30)
        left_box.set_hexpand(False)
        left_box.set_vexpand(False)
        self._sync_icon = Gtk.Image(icon_name='view-refresh-symbolic')
        self._sync_icon.add_css_class('sync-online-icon')
        left_box.set_center_widget(self._sync_icon)
        online_box.append(left_box)

        sync_divider = Gtk.Box()
        sync_divider.add_css_class('sync-divider')
        sync_divider.set_size_request(1, 30)
        sync_divider.set_hexpand(False)
        sync_divider.set_vexpand(False)
        sync_divider.set_valign(Gtk.Align.FILL)
        online_box.append(sync_divider)

        right_box = Gtk.CenterBox(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        right_box.add_css_class('sync-right-side')
        right_box.set_size_request(81, 30)
        right_box.set_hexpand(False)
        right_box.set_vexpand(False)
        right_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._countdown_hint_lbl = Gtk.Label(label='Auto Sync')
        self._countdown_hint_lbl.add_css_class('sync-auto-label')
        self._countdown_hint_lbl.set_hexpand(True)
        self._countdown_hint_lbl.set_halign(Gtk.Align.CENTER)
        self._countdown_hint_lbl.set_xalign(0.5)
        right_stack.append(self._countdown_hint_lbl)

        self._countdown_lbl = Gtk.Label()
        self._countdown_lbl.add_css_class('sync-auto-value')
        self._countdown_lbl.set_hexpand(True)
        self._countdown_lbl.set_halign(Gtk.Align.CENTER)
        self._countdown_lbl.set_xalign(0.5)
        self._countdown_lbl.set_width_chars(6)
        right_stack.append(self._countdown_lbl)
        right_box.set_center_widget(right_stack)
        online_box.append(right_box)

        offline_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        offline_box.set_size_request(120, 30)
        offline_box.set_hexpand(False)
        offline_box.set_vexpand(False)
        self._sync_offline_lbl = Gtk.Label(label='Offline')
        self._sync_offline_lbl.add_css_class('sync-offline-label')
        offline_box.append(self._sync_offline_lbl)

        self._sync_state_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._sync_state_stack.set_hexpand(False)
        self._sync_state_stack.set_vexpand(False)
        self._sync_state_stack.add_named(online_box, 'online')
        self._sync_state_stack.add_named(offline_box, 'offline')
        self._sync_state_stack.set_visible_child_name('online')

        sync_overlay = Gtk.Overlay()
        sync_overlay.set_size_request(120, 30)
        sync_overlay.set_hexpand(False)
        sync_overlay.set_vexpand(False)
        sync_overlay.set_halign(Gtk.Align.CENTER)
        sync_overlay.set_valign(Gtk.Align.CENTER)
        self._sync_btn = Gtk.Button(child=self._sync_state_stack, tooltip_text='Sync now (F5)')
        self._sync_btn.add_css_class('sync-control')
        self._sync_btn.add_css_class('sidebar-action-btn')
        self._sync_btn.add_css_class('sync-online')
        self._sync_btn.set_size_request(120, 30)
        self._sync_btn.set_hexpand(False)
        self._sync_btn.set_vexpand(False)
        self._sync_btn.set_halign(Gtk.Align.CENTER)
        self._sync_btn.set_valign(Gtk.Align.CENTER)
        self._sync_btn.set_focusable(False)
        self._sync_btn.connect('clicked', self._on_sync)
        sync_overlay.set_child(self._sync_btn)

        self._sync_badge = Gtk.Label()
        self._sync_badge.add_css_class('sync-badge')
        self._sync_badge.set_halign(Gtk.Align.END)
        self._sync_badge.set_valign(Gtk.Align.START)
        self._sync_badge.set_visible(False)
        sync_overlay.add_overlay(self._sync_badge)

        compose_inner = Gtk.CenterBox(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        compose_inner.set_size_request(120, 30)
        compose_stack = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        compose_stack.append(Gtk.Image(icon_name='mail-message-new-symbolic'))
        compose_lbl = Gtk.Label(label='New')
        compose_lbl.add_css_class('sidebar-compose-label')
        compose_stack.append(compose_lbl)
        compose_inner.set_center_widget(compose_stack)
        compose_overlay = Gtk.Overlay()
        compose_overlay.set_size_request(120, 30)
        compose_overlay.set_hexpand(False)
        compose_overlay.set_vexpand(False)
        compose_overlay.set_halign(Gtk.Align.CENTER)
        compose_overlay.set_valign(Gtk.Align.CENTER)
        compose_btn = Gtk.Button(child=compose_inner, tooltip_text='Compose (c)')
        compose_btn.add_css_class('suggested-action')
        compose_btn.add_css_class('sidebar-action-btn')
        compose_btn.set_size_request(120, 30)
        compose_btn.set_hexpand(False)
        compose_btn.set_vexpand(False)
        compose_btn.set_halign(Gtk.Align.CENTER)
        compose_btn.set_valign(Gtk.Align.CENTER)
        compose_btn.set_focusable(False)
        compose_btn.connect('clicked', self._on_compose)
        self._compose_btn = compose_btn
        compose_overlay.set_child(compose_btn)

        root.add_top_bar(header)

        # Body
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True)

        sidebar_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=_SIDEBAR_MIN_WIDTH)
        sidebar_col.set_size_request(_SIDEBAR_MIN_WIDTH, -1)
        sidebar_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            homogeneous=False,
            halign=Gtk.Align.START,
            valign=Gtk.Align.START,
        )
        sidebar_actions.add_css_class('sidebar-actions')
        sidebar_actions.set_size_request(_SIDEBAR_MIN_WIDTH, -1)
        sidebar_actions.set_hexpand(False)
        sidebar_actions.set_vexpand(False)
        sync_overlay.set_hexpand(False)
        sync_overlay.set_halign(Gtk.Align.CENTER)
        compose_overlay.set_hexpand(False)
        compose_overlay.set_halign(Gtk.Align.CENTER)
        sidebar_actions.append(sync_overlay)
        sidebar_actions.append(compose_overlay)
        sidebar_col.append(sidebar_actions)

        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            width_request=_SIDEBAR_MIN_WIDTH,
            vexpand=True,
        )
        self.folder_list = Gtk.ListBox()
        self.folder_list.add_css_class('navigation-sidebar')
        self.folder_list.connect('row-selected', self._on_folder_selected)
        self.folder_list.connect('row-activated', self._on_row_activated)
        sidebar_scroll.set_child(self.folder_list)
        sidebar_col.append(sidebar_scroll)
        body.append(sidebar_col)

        right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        right.add_css_class('content-split')
        right.set_position(380)
        right.set_shrink_start_child(False)
        right.set_shrink_end_child(False)
        right.set_resize_start_child(False)
        right.set_resize_end_child(True)
        self._content_paned = right
        right.connect('notify::position', self._on_content_paned_position_changed)

        list_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=380)
        list_col.add_css_class('message-column')

        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        search_box.add_css_class('search-bar-box')
        search_overlay = Gtk.Overlay(hexpand=True)
        search_overlay.add_css_class('search-entry-shell')
        search_overlay.set_halign(Gtk.Align.FILL)
        self._search_entry = Gtk.Entry(
            placeholder_text='Search sender, subject…',
            hexpand=True,
        )
        self._search_entry.add_css_class('search-entry-tab')
        self._search_entry.connect('changed', self._on_search_changed)
        search_overlay.set_child(self._search_entry)
        search_icon = Gtk.Image(icon_name='system-search-symbolic')
        search_icon.add_css_class('search-entry-icon')
        search_icon.set_halign(Gtk.Align.END)
        search_icon.set_valign(Gtk.Align.CENTER)
        search_icon.set_margin_end(10)
        search_overlay.add_overlay(search_icon)
        search_box.append(search_overlay)
        self._search_bar = search_box
        list_col.append(self._search_bar)

        self._list_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        email_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.email_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.email_list.set_filter_func(self._email_filter)
        self.email_list.connect('row-selected', self._on_email_selected)
        email_scroll.set_child(self.email_list)

        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=60)
        spinner.set_size_request(32, 32)
        loading_box.append(spinner)

        self._empty_page = Adw.StatusPage(icon_name='mail-inbox-symbolic', title='No messages')

        self._list_stack.add_named(email_scroll, 'list')
        self._list_stack.add_named(loading_box, 'loading')
        self._list_stack.add_named(self._empty_page, 'empty')
        self._list_stack.set_visible_child_name('loading')

        list_col.append(self._list_stack)
        right.set_start_child(list_col)

        # Viewer: webview + attachment bar
        viewer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        wk_settings = WebKit.Settings()
        wk_settings.set_enable_javascript(True)
        wk_settings.set_auto_load_images(get_settings().get('load_images'))
        wk_settings.set_enable_write_console_messages_to_stdout(False)
        self._webview_settings = wk_settings
        self._current_body = None

        self._message_info_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._message_info_bar.add_css_class('message-info-bar')
        self._message_info_top = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.CENTER,
        )
        self._message_info_top.add_css_class('message-info-top')
        self._message_info_accent = Gtk.Box()
        self._message_info_accent.set_size_request(4, 18)
        self._message_info_accent.add_css_class('account-accent-strip')
        self._message_info_top.append(self._message_info_accent)
        self._message_info_subject = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_subject.add_css_class('message-info-subject')
        self._message_info_subject.set_wrap(False)
        self._message_info_subject.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_subject.set_hexpand(True)
        self._message_info_top.append(self._message_info_subject)
        self._message_info_bar.append(self._message_info_top)

        self._message_info_sender = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_sender.add_css_class('message-info-sender')
        self._message_info_sender.add_css_class('message-info-sender-line')
        self._message_info_sender.set_wrap(False)
        self._message_info_sender.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_sender.set_hexpand(True)
        self._message_info_bar.append(self._message_info_sender)

        self._message_info_date = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_date.add_css_class('message-info-date')
        self._message_info_date.set_wrap(False)
        self._message_info_date.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_bar.append(self._message_info_date)

        self._message_info_meta = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_meta.add_css_class('message-info-meta')
        self._message_info_meta.set_wrap(False)
        self._message_info_meta.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_bar.append(self._message_info_meta)
        self._message_info_bar.set_visible(False)

        self.webview = WebKit.WebView(vexpand=True, hexpand=True)
        self.webview.set_settings(wk_settings)
        self.webview.connect('load-changed', self._on_webview_load_changed)
        viewer_box.append(self._message_info_bar)

        att_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        att_bar.add_css_class('attachment-bar')
        att_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=6)
        att_header.append(Gtk.Image(icon_name=_pick_icon_name('mail-attachment-symbolic', 'paperclip-symbolic')))
        att_title = Gtk.Label(label='Attachments', halign=Gtk.Align.START)
        att_title.add_css_class('dim-label')
        att_header.append(att_title)
        att_bar.append(att_header)
        att_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self._attachment_flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_bottom=2)
        att_scroll.set_child(self._attachment_flow)
        att_bar.append(att_scroll)
        self._attachment_bar = att_bar
        viewer_box.append(self._attachment_bar)

        self._thread_reply_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            valign=Gtk.Align.FILL,
        )
        self._thread_reply_bar.add_css_class('thread-reply-bar')
        reply_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=54,
            hexpand=True,
            vexpand=False,
        )
        self._thread_reply_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            vexpand=False,
            hexpand=True,
        )
        self._thread_reply_view.add_css_class('thread-reply-editor')
        reply_scroller.set_child(self._thread_reply_view)
        self._thread_reply_send = Gtk.Button(label='Send', hexpand=False)
        self._thread_reply_send.add_css_class('suggested-action')
        self._thread_reply_send.add_css_class('thread-reply-send')
        self._thread_reply_send.connect('clicked', self._on_thread_reply_send)
        self._thread_reply_bar.append(reply_scroller)
        self._thread_reply_bar.append(self._thread_reply_send)
        self._thread_reply_bar.set_visible(False)
        viewer_box.append(self._thread_reply_bar)

        try:
            from .settings import build_settings_content
        except ImportError:
            from settings import build_settings_content
        self._viewer_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
            hexpand=True,
        )
        self._compose_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        self._viewer_stack.add_named(viewer_box, 'viewer')
        self._viewer_stack.add_named(build_settings_content(self), 'settings')
        self._viewer_stack.add_named(self._compose_holder, 'compose')
        self._viewer_stack.set_visible_child_name('viewer')

        self._thread_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._thread_sidebar.add_css_class('thread-sidebar')
        sidebar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_hexpand(True)
        self._thread_sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._thread_sidebar_list.add_css_class('thread-sidebar-list')
        self._thread_sidebar_list.connect('row-activated', self._on_thread_sidebar_row_activated)
        sidebar_scroll.set_child(self._thread_sidebar_list)
        self._thread_sidebar.append(sidebar_scroll)
        self._thread_sidebar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=240,
            halign=Gtk.Align.END,
            valign=Gtk.Align.FILL,
        )
        self._thread_sidebar_revealer.set_child(self._thread_sidebar)
        self._thread_sidebar_revealer.set_reveal_child(False)

        self._thread_webview_overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self._thread_webview_overlay.set_child(self.webview)
        self._thread_messages_btn = Gtk.Button(label='All Messages')
        self._thread_messages_btn.add_css_class('thread-info-button')
        self._thread_messages_btn.add_css_class('thread-tab')
        self._thread_messages_btn.set_visible(False)
        self._thread_messages_btn.set_halign(Gtk.Align.END)
        self._thread_messages_btn.set_valign(Gtk.Align.START)
        self._thread_messages_btn.set_margin_top(10)
        self._thread_messages_btn.set_margin_end(10)
        self._thread_messages_btn.connect(
            'clicked',
            lambda *_: self._set_thread_sidebar_visible(
                not getattr(self, '_thread_sidebar_revealer', None).get_reveal_child()
                if getattr(self, '_thread_sidebar_revealer', None) is not None
                else True
            ),
        )
        self._thread_webview_overlay.add_overlay(self._thread_messages_btn)

        self._thread_body_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            hexpand=True,
            vexpand=True,
        )
        self._thread_body_row.append(self._thread_webview_overlay)
        self._thread_body_row.append(self._thread_sidebar_revealer)
        viewer_box.insert_child_after(self._thread_body_row, self._message_info_bar)

        viewer_shell = Gtk.Frame(vexpand=True, hexpand=True, margin_top=5)
        viewer_shell.add_css_class('reading-pane-shell')
        viewer_shell.set_child(self._viewer_stack)
        right.set_end_child(viewer_shell)
        body.append(right)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(body)
        root.set_content(self._toast_overlay)
        self.set_content(root)

        self._content_paned.set_position(max(_MESSAGE_LIST_MIN_WIDTH, min(_MESSAGE_LIST_MAX_WIDTH, self._content_paned.get_position())))
        self._show_empty_viewer()
        GLib.idle_add(self._prune_disk_body_cache)

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

    # ── Countdown ─────────────────────────────────────────────────────────────

    def _tick_countdown(self):
        if self._network_offline:
            self._countdown_lbl.set_label('N/A')
            return GLib.SOURCE_CONTINUE
        if self._syncing:
            self._sync_dots = (self._sync_dots + 1) % 3
            self._countdown_lbl.set_label('•' * (self._sync_dots + 1))
        else:
            if self._countdown_seconds > 0:
                self._countdown_seconds -= 1
            mins, secs = divmod(self._countdown_seconds, 60)
            self._countdown_lbl.set_label(f'{mins}:{secs:02d}')
        return GLib.SOURCE_CONTINUE

    def _reset_countdown(self):
        try:
            from .settings import get_settings
        except ImportError:
            from settings import get_settings
        self._countdown_seconds = get_settings().get('poll_interval') * 60

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
            self._countdown_hint_lbl.set_label('Auto Sync')
            self._sync_btn.set_tooltip_text('Sync now (F5)')
            self._reset_countdown()
            mins, secs = divmod(self._countdown_seconds, 60)
            self._countdown_lbl.set_label(f'{mins}:{secs:02d}')
            if view_name == 'viewer':
                self.title_widget.set_subtitle(self._content_subtitle)

    def set_syncing(self, syncing):
        if self._network_offline:
            self._syncing = False
            return
        self._syncing = syncing
        if not syncing:
            self._reset_countdown()

    def _finish_sync(self, total_new=0):
        self.set_syncing(False)
        self._sync_in_flight = False
        if total_new > 0:
            self.show_sync_badge(total_new)
            if self._compose_active() or self._viewer_stack.get_visible_child_name() != 'viewer':
                self._background_refresh_pending = True
            else:
                self.refresh_visible_mail(force=True)

    def on_poll_complete(self, total_new):
        self._finish_sync(total_new)

    def _on_content_paned_position_changed(self, paned, _pspec):
        position = paned.get_position()
        clamped = max(_MESSAGE_LIST_MIN_WIDTH, min(_MESSAGE_LIST_MAX_WIDTH, position))
        if clamped != position:
            paned.set_position(clamped)

    # ── Sidebar events ────────────────────────────────────────────────────────

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

    # ── Folder / email selection ──────────────────────────────────────────────

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
        self._active_folder_row = row
        self._active_email_row = None
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

    def _on_email_selected(self, _, row):
        if self._suppress_email_selection:
            return
        if row is None or not isinstance(row, EmailRow):
            return
        if self._compose_active():
            self._request_leave_compose(
                lambda: self._commit_email_selection(row),
                self._restore_email_selection,
            )
            return
        self._commit_email_selection(row)

    def _commit_email_selection(self, row):
        # Any explicit email choice cancels the startup auto-pick path so a
        # later background refresh cannot override the user's selection.
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
            self._adjust_unread_count_for_message(row.msg, -1)

    def _restore_folder_selection(self):
        self._suppress_folder_selection = True
        self.folder_list.select_row(self._active_folder_row)
        self._suppress_folder_selection = False

    def _restore_email_selection(self):
        self._suppress_email_selection = True
        self.email_list.select_row(self._active_email_row)
        self._suppress_email_selection = False

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_sync(self, _=None):
        self._flash_action_feedback(self._sync_btn)
        if self._sync_in_flight or self._syncing:
            return
        if self._network_offline or not network_ready():
            self.set_network_offline(True)
            return
        self._sync_in_flight = True
        self.set_syncing(True)
        self._reset_countdown()
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
        try:
            from .compose import ComposeView
        except ImportError:
            from compose import ComposeView
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
        try:
            from .compose import ComposeView
        except ImportError:
            from compose import ComposeView
        if self._compose_active():
            return
        backend = msg.get('backend_obj') or self.current_backend
        if backend:
            self._present_compose(
                ComposeView(self, backend, self.backends, reply_to=msg, on_close=self._close_inline_compose)
            )

    def _on_reply_all(self, msg):
        try:
            from .compose import ComposeView
        except ImportError:
            from compose import ComposeView
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
        self.email_list.remove(row)
        if self.email_list.get_row_at_index(0) is None:
            self._list_stack.set_visible_child_name('empty')
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
        row = self.email_list.get_selected_row()
        if not row or not isinstance(row, EmailRow):
            return
        msg = row.msg
        backend = msg.get('backend_obj') or self.current_backend
        if not backend:
            return
        if not msg.get('is_read', True):
            return
        row.mark_unread()
        msg['is_read'] = False
        self._adjust_unread_count_for_message(msg, 1)
        def do_mark():
            try:
                backend.mark_as_unread(msg['uid'], msg.get('folder'))
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Failed: {e}')
        threading.Thread(target=do_mark, daemon=True).start()

    def _on_search_changed(self, entry):
        self._search_text = entry.get_text().lower()
        self.email_list.invalidate_filter()

    def _email_filter(self, row):
        if not self._search_text or not isinstance(row, EmailRow):
            return True
        msg = row.msg
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
            row = self.email_list.get_selected_row()
            if row and isinstance(row, EmailRow):
                self._on_reply(row.msg)
            return True
        if key == 'a':
            row = self.email_list.get_selected_row()
            if row and isinstance(row, EmailRow):
                self._on_reply_all(row.msg)
            return True
        if key == 'd':
            row = self.email_list.get_selected_row()
            if row and isinstance(row, EmailRow):
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
        row = self.email_list.get_selected_row()
        if row is None:
            next_row = self.email_list.get_row_at_index(0)
        else:
            next_row = self.email_list.get_row_at_index(row.get_index() + delta)
        if next_row:
            self.email_list.select_row(next_row)
            next_row.grab_focus()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _begin_message_load(self):
        self._message_load_generation += 1
        self._prefetch_generation += 1
        return self._message_load_generation

    def refresh_visible_mail(self, force=False, preserve_selected=True):
        if self._viewer_stack.get_visible_child_name() != 'viewer':
            return False
        if not network_ready():
            return False
        focused = self.get_focus()
        current_keys = self._current_message_keys()
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
                self._load_unified_inbox(preserve_selected_key=preserve_key, current_keys=current_keys)
            elif self.current_folder == _UNIFIED_TRASH:
                self._load_unified_folder('Trash', preserve_selected_key=preserve_key, current_keys=current_keys)
            elif self.current_folder == _UNIFIED_SPAM:
                self._load_unified_folder('Spam', preserve_selected_key=preserve_key, current_keys=current_keys)
            elif self.current_backend:
                self._load_messages(preserve_selected_key=preserve_key, current_keys=current_keys)
        if self._offline_body_pending and self._active_email_row is not None:
            self._offline_body_pending = False
            self._body_load_generation += 1
            self._load_body(self._active_email_row.msg, self._body_load_generation)
        if focused is not None and focused.get_root() is self:
            GLib.idle_add(self._restore_focus_widget, focused)
        return False

    def _restore_focus_widget(self, widget):
        try:
            if widget is not None and widget.get_root() is self:
                widget.grab_focus()
        except Exception:
            pass
        return False

    def _load_messages(self, preserve_selected_key=None, current_keys=None, sync_complete_callback=None):
        generation = self._begin_message_load()
        snapshot_loaded = self._show_message_snapshot(generation)
        if not network_ready():
            self._offline_refresh_pending = True
            if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            return
        self._offline_refresh_pending = False
        if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        backend = self.current_backend
        folder = self.current_folder
        op = self._start_background_op(
            'load messages',
            f'{backend.identity}/{folder}',
            'backend fetch_messages, auth, or IMAP latency',
        )
        def fetch():
            try:
                msgs = backend.fetch_messages(folder)
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
            new_msgs = self._new_messages_from(msgs, current_keys)
            if new_msgs:
                self._prefetch_bodies_for_messages(new_msgs, generation, wait=True)
            GLib.idle_add(self._set_messages, msgs, generation, preserve_selected_key)
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
        threading.Thread(target=fetch, daemon=True).start()

    def _load_unified_inbox(self, preserve_selected_key=None, current_keys=None, sync_complete_callback=None):
        generation = self._begin_message_load()
        snapshot_loaded = self._show_message_snapshot(generation)
        if not network_ready():
            self._offline_refresh_pending = True
            if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            return
        self._offline_refresh_pending = False
        if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        backends = list(self.backends)
        op = self._start_background_op(
            'load unified inbox',
            'all accounts',
            'one backend may be slow or blocked; check auth/network',
        )
        def fetch():
            all_msgs = []
            had_transient_error = False
            for b in backends:
                try:
                    all_msgs.extend(b.fetch_messages(b.FOLDERS[0][0]))
                except Exception as e:
                    if is_transient_network_error(e) or not network_ready():
                        self._offline_refresh_pending = True
                        had_transient_error = True
                        continue
                    _log_exception(f'Unified inbox error ({b.identity})', e)
            if had_transient_error and not all_msgs:
                GLib.idle_add(self._end_background_op, op)
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                return
            all_msgs.sort(
                key=lambda m: m.get('date') or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            new_msgs = self._new_messages_from(all_msgs[:100], current_keys)
            if new_msgs:
                self._prefetch_bodies_for_messages(new_msgs, generation, wait=True)
            GLib.idle_add(self._set_messages, all_msgs[:100], generation, preserve_selected_key)
            GLib.idle_add(self._end_background_op, op)
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
        threading.Thread(target=fetch, daemon=True).start()

    def _load_unified_folder(self, folder_name, preserve_selected_key=None, current_keys=None, sync_complete_callback=None):
        generation = self._begin_message_load()
        snapshot_loaded = self._show_message_snapshot(generation)
        if not network_ready():
            self._offline_refresh_pending = True
            if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
                self._list_stack.set_visible_child_name('loading')
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
            return
        self._offline_refresh_pending = False
        if not snapshot_loaded and self._list_stack.get_visible_child_name() != 'list':
            self._list_stack.set_visible_child_name('loading')
        backends = list(self.backends)
        op = self._start_background_op(
            f'load unified {folder_name.lower()}',
            'all accounts',
            'one backend may be slow or blocked; check auth/network',
        )
        def fetch():
            all_msgs = []
            had_transient_error = False
            for b in backends:
                fid = next((f[0] for f in b.FOLDERS if f[1] == folder_name), None)
                if fid:
                    try:
                        all_msgs.extend(b.fetch_messages(fid))
                    except Exception as e:
                        if is_transient_network_error(e) or not network_ready():
                            self._offline_refresh_pending = True
                            had_transient_error = True
                            continue
                        _log_exception(f'Unified {folder_name} error ({b.identity})', e)
            if had_transient_error and not all_msgs:
                GLib.idle_add(self._end_background_op, op)
                if sync_complete_callback is not None:
                    GLib.idle_add(sync_complete_callback, 0)
                return
            all_msgs.sort(
                key=lambda m: m.get('date') or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            new_msgs = self._new_messages_from(all_msgs[:100], current_keys)
            if new_msgs:
                self._prefetch_bodies_for_messages(new_msgs, generation, wait=True)
            GLib.idle_add(self._set_messages, all_msgs[:100], generation, preserve_selected_key)
            GLib.idle_add(self._end_background_op, op)
            if sync_complete_callback is not None:
                GLib.idle_add(sync_complete_callback, 0)
        threading.Thread(target=fetch, daemon=True).start()

    def _set_messages(self, msgs, generation=None, preserve_selected_key=None):
        if generation is not None and generation != self._message_load_generation:
            return False
        msgs = list(msgs or [])
        while (r := self.email_list.get_row_at_index(0)):
            self.email_list.remove(r)
        if not msgs:
            self._thread_groups = {}
            self._prefetch_generation += 1
            self._empty_page.set_title('No messages')
            self._empty_page.set_description(None)
            self._list_stack.set_visible_child_name('empty')
            return False
        groups = collections.OrderedDict()
        representatives = []
        singletons = []
        for m in msgs:
            key = self._thread_key_for_msg(m)
            if key is None:
                m['thread_count'] = 1
                m['thread_key'] = None
                singletons.append(m)
                continue
            group = groups.setdefault(key, [])
            group.append(m)
        self._thread_groups = groups
        for key, group in groups.items():
            group.sort(key=lambda item: item.get('date') or datetime.min.replace(tzinfo=timezone.utc))
            count = len(group)
            representative = group[-1]
            representative['thread_count'] = count
            representative['thread_key'] = key
            representative['thread_members'] = group
            representatives.append(representative)
        ordered_msgs = sorted(
            representatives + singletons,
            key=lambda item: item.get('date') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        self._prefetch_generation += 1
        for m in ordered_msgs:
            accent_class = self._account_class_for((m.get('account') or (m.get('backend_obj').identity if m.get('backend_obj') else '')))
            self.email_list.append(
                EmailRow(m, self._on_reply, self._on_reply_all, self._on_delete, accent_class=accent_class)
            )
        self._list_stack.set_visible_child_name('list')
        self._store_message_snapshot(ordered_msgs)
        self._prefetch_bodies(ordered_msgs)
        self._active_email_row = None
        if preserve_selected_key:
            row = self.email_list.get_first_child()
            while row is not None:
                if not isinstance(row, EmailRow):
                    row = row.get_next_sibling()
                    continue
                msg = row.msg
                if (
                    msg.get('account', ''),
                    msg.get('folder', ''),
                    msg.get('uid', ''),
                ) == preserve_selected_key:
                    self._suppress_email_selection = True
                    self.email_list.select_row(row)
                    self._suppress_email_selection = False
                    self._active_email_row = row
                    break
                row = row.get_next_sibling()
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
                    representative = next(
                        (m for m in ordered_msgs if m.get('thread_key') == preserved_group),
                        None,
                    )
                    if representative is not None:
                        row = self.email_list.get_first_child()
                        while row is not None:
                            if isinstance(row, EmailRow) and row.msg is representative:
                                self._suppress_email_selection = True
                                self.email_list.select_row(row)
                                self._suppress_email_selection = False
                                self._active_email_row = row
                                break
                            row = row.get_next_sibling()
        elif self._startup_autoselect_pending and self.current_folder in (_UNIFIED, 'INBOX', 'inbox'):
            first_row = self.email_list.get_row_at_index(0)
            if first_row is not None:
                self._startup_autoselect_pending = False
                self.email_list.select_row(first_row)
                self._active_email_row = first_row
                first_row.grab_focus()
        return False

    def _prefetch_bodies(self, msgs):
        """Warm the newest inbox-like bodies in the background."""
        if not msgs or not self._should_seed_recent_cache():
            return
        generation = self._prefetch_generation
        budget_mb = get_settings().get('disk_cache_budget_mb')
        limit = max(1, min(_PREFETCH_WARMUP_LIMIT, budget_mb // 16 or 1))
        ordered = sorted(
            list(msgs),
            key=lambda m: m.get('date') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:limit]
        self._prefetch_bodies_for_messages(ordered, generation)

    def _message_key(self, msg):
        return (
            msg.get('account', ''),
            msg.get('folder', ''),
            msg.get('uid', ''),
        )

    def _current_message_keys(self):
        keys = set()
        row = self.email_list.get_first_child()
        while row is not None:
            if isinstance(row, EmailRow):
                keys.add(self._message_key(row.msg))
            row = row.get_next_sibling()
        return keys

    def _new_messages_from(self, msgs, current_keys=None):
        if not msgs or current_keys is None:
            return []
        return [m for m in msgs if self._message_key(m) not in current_keys]

    def _prefetch_bodies_for_messages(self, msgs, generation=None, wait=False):
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
                        while len(self._body_cache) > _BODY_CACHE_LIMIT:
                            self._body_cache.popitem(last=False)
                    self._store_disk_body(disk_key, html, text, attachments, candidate.get('date'))
                except Exception as e:
                    _log_exception(f'Prefetch failed ({backend.identity}, {folder}, {uid})', e)

        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def _should_seed_recent_cache(self):
        return self.current_folder in (_UNIFIED, 'INBOX', 'inbox')

    def _show_message_snapshot(self, generation=None):
        scope = _snapshot_scope(self.current_backend, self.current_folder)
        if not scope:
            return False
        path = _snapshot_path(scope)
        try:
            if not path.exists():
                return False
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                payload = json.load(f)
            stored_accounts_raw = payload.get('accounts')
            if scope == 'unified-inbox' and not stored_accounts_raw:
                return False
            stored_accounts = sorted(stored_accounts_raw or [])
            current_accounts = sorted(b.identity for b in self.backends)
            if stored_accounts and stored_accounts != current_accounts:
                return False
            msgs = []
            for m in payload.get('messages', []):
                try:
                    date_val = m.get('date')
                    date = datetime.fromisoformat(date_val) if date_val else datetime.now(timezone.utc)
                except Exception:
                    date = datetime.now(timezone.utc)
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
                    'folder': m.get('folder', self.current_folder),
                    'backend': m.get('backend', ''),
                    'account': m.get('account', ''),
                    'thread_id': m.get('thread_id', ''),
                    'thread_source': m.get('thread_source', ''),
                    'backend_obj': (
                        self.current_backend
                        if self.current_backend and m.get('account') == self.current_backend.identity
                    else _backend_for_identity(self.backends, m.get('account'))
                    ),
                })
            self._set_messages(msgs, generation)
            return True
        except Exception as e:
            _log_exception(f'Snapshot load failed ({scope})', e)
            return False

    def _store_message_snapshot(self, msgs):
        scope = _snapshot_scope(self.current_backend, self.current_folder)
        if not scope:
            return
        try:
            _SNAPSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                'scope': scope,
                'saved_at': datetime.now(timezone.utc).isoformat(),
                'accounts': [b.identity for b in self.backends],
                'messages': [
                    {
                        'uid': m.get('uid', ''),
                        'subject': m.get('subject', '(no subject)'),
                        'sender_name': m.get('sender_name', ''),
                        'sender_email': m.get('sender_email', ''),
                        'to_addrs': m.get('to_addrs', []),
                        'cc_addrs': m.get('cc_addrs', []),
                        'date': (m.get('date').isoformat() if m.get('date') else ''),
                        'is_read': m.get('is_read', True),
                        'has_attachments': m.get('has_attachments', False),
                        'snippet': m.get('snippet', ''),
                        'folder': m.get('folder', self.current_folder),
                        'backend': m.get('backend', ''),
                        'account': m.get('account', ''),
                        'thread_id': m.get('thread_id', ''),
                        'thread_source': m.get('thread_source', ''),
                    }
                    for m in (msgs or [])[:100]
                ],
            }
            path = _snapshot_path(scope)
            with gzip.open(path, 'wt', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            _log_exception(f'Snapshot save failed ({scope})', e)

    def _load_disk_body(self, cache_key):
        return load_disk_body(cache_key)

    def _store_disk_body(self, cache_key, html, text, attachments, msg_date=None):
        store_disk_body(cache_key, html, text, attachments, msg_date)

    def _prune_disk_body_cache(self):
        prune_disk_body_cache()

    def _read_message_body_payload(self, msg):
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        if backend is None:
            raise RuntimeError('No backend available for message')
        uid = msg['uid']
        folder = msg.get('folder')
        cache_key = (backend.identity, folder, uid)
        disk_cache_key = _body_cache_key(backend.identity, folder, uid)
        with self._cache_lock:
            cached_body = self._body_cache.get(cache_key)
        if cached_body is not None:
            return cached_body
        disk_body = self._load_disk_body(disk_cache_key)
        if disk_body is not None:
            with self._cache_lock:
                self._body_cache[cache_key] = disk_body
                self._body_cache.move_to_end(cache_key)
                while len(self._body_cache) > _BODY_CACHE_LIMIT:
                    self._body_cache.popitem(last=False)
            return disk_body
        html, text, attachments = backend.fetch_body(uid, folder)
        with self._cache_lock:
            self._body_cache[cache_key] = (html, text, attachments)
            self._body_cache.move_to_end(cache_key)
            while len(self._body_cache) > _BODY_CACHE_LIMIT:
                self._body_cache.popitem(last=False)
        self._store_disk_body(disk_cache_key, html, text, attachments, msg.get('date'))
        return html, text, attachments

    def _set_error(self, msg, generation=None):
        if generation is not None and generation != self._message_load_generation:
            return False
        while (r := self.email_list.get_row_at_index(0)):
            self.email_list.remove(r)
        self._empty_page.set_title('Could not load')
        self._empty_page.set_description(msg)
        self._list_stack.set_visible_child_name('empty')
        self._show_toast(f'Error: {msg}')
        return False

    def _load_body(self, msg, generation=None):
        try:
            from .settings import get_settings
        except ImportError:
            from settings import get_settings
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
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
                if get_settings().get('mark_read_on_open') and not msg.get('is_read'):
                    try:
                        backend.mark_as_read(uid, folder)
                        msg['is_read'] = True
                    except Exception:
                        pass
            except Exception as e:
                if is_transient_network_error(e) or not network_ready():
                    self._offline_body_pending = True
                    if self._current_body is None:
                        GLib.idle_add(self._show_loading_viewer)
                else:
                    _log_exception(f'Load body failed ({backend_identity}, {folder}, {uid})', e)
                    if self._current_body is not None:
                        GLib.idle_add(self._show_toast, f'Failed to load message: {e}')
                    else:
                        GLib.idle_add(self._set_body_error, str(e), generation)
            finally:
                GLib.idle_add(self._end_background_op, op)
        threading.Thread(target=fetch, daemon=True).start()

    def _load_thread_view(self, msg, generation=None):
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        thread_id = (msg.get('thread_id') or '').strip()
        if msg.get('thread_source') == 'demo' and msg.get('thread_members'):
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
                    'selected': thread_msg.get('uid') == selected_uid,
                })
                for att in thread_msg.get('attachments') or []:
                    att_copy = dict(att)
                    att_copy['source_msg'] = thread_msg
                    attachments.append(att_copy)
            GLib.idle_add(self._render_thread_view, msg, records, attachments, generation)
            return
        if not backend or not thread_id:
            self._load_body(msg, generation)
            return
        op = self._start_background_op(
            'load thread',
            f'{(backend.identity if backend else (msg.get("account") or "unknown"))}/{thread_id}',
            'backend thread fetch, body fetches, or mailbox latency',
        )
        if self._current_body is None:
            self._show_loading_viewer()

        def fetch():
            try:
                if not backend or not hasattr(backend, 'fetch_thread_messages'):
                    raise AttributeError('thread fetch unavailable')
                thread_msgs = backend.fetch_thread_messages(thread_id) or []
                if not thread_msgs:
                    GLib.idle_add(self._end_background_op, op)
                    GLib.idle_add(self._load_body, msg, generation)
                    return
                records = []
                attachments = []
                selected_uid = msg.get('uid')
                total = len(thread_msgs)
                for thread_msg in thread_msgs:
                    try:
                        html, text, fetched_attachments = self._read_message_body_payload(thread_msg)
                    except Exception as e:
                        _log_exception(
                            f'Thread body failed ({backend.identity}, {thread_msg.get("folder")}, {thread_msg.get("uid")})',
                            e,
                        )
                        html, text, fetched_attachments = None, '', []
                    thread_msg = dict(thread_msg)
                    thread_msg['thread_count'] = total
                    thread_msg['thread_key'] = self._thread_key_for_msg(thread_msg)
                    records.append({
                        'msg': thread_msg,
                        'html': html,
                        'text': text,
                        'attachments': fetched_attachments or [],
                        'body_text': self._extract_thread_body(html, text),
                        'selected': thread_msg.get('uid') == selected_uid,
                    })
                    for att in fetched_attachments or []:
                        att_copy = dict(att)
                        att_copy['source_msg'] = thread_msg
                        attachments.append(att_copy)
                GLib.idle_add(self._render_thread_view, msg, records, attachments, generation)
                if get_settings().get('mark_read_on_open') and not msg.get('is_read'):
                    try:
                        backend.mark_as_read(msg['uid'], msg.get('folder'))
                        msg['is_read'] = True
                    except Exception:
                        pass
            except Exception as e:
                if is_transient_network_error(e) or not network_ready():
                    self._offline_body_pending = True
                    if self._current_body is None:
                        GLib.idle_add(self._show_loading_viewer)
                else:
                    _log_exception(f'Load thread failed ({backend.identity if backend else (msg.get("account") or "unknown")}, {thread_id})', e)
                    GLib.idle_add(self._set_body_error, str(e), generation)
            finally:
                GLib.idle_add(self._end_background_op, op)

        threading.Thread(target=fetch, daemon=True).start()

    def _render_thread_view(self, selected_msg, records, attachments, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        ordered_records = sorted(
            list(records or []),
            key=lambda record: record.get('msg', {}).get('date') or datetime.min.replace(tzinfo=timezone.utc),
        )
        thread_msgs = [record['msg'] for record in ordered_records]
        subject = self._thread_subject_for_messages(thread_msgs)
        thread_seed = str(
            selected_msg.get('thread_id')
            or selected_msg.get('thread_key')
            or selected_msg.get('account')
            or selected_msg.get('sender_email')
            or selected_msg.get('sender_name')
            or subject
            or ''
        )
        thread_account_seed = (
            selected_msg.get('account')
            or (selected_msg.get('backend_obj').identity if selected_msg.get('backend_obj') else '')
            or selected_msg.get('sender_email')
            or selected_msg.get('sender_name')
            or ''
        )
        self_color = self._sender_accent_rgb(thread_account_seed)
        sender_order = []
        for msg in thread_msgs:
            key = _sender_key(msg)
            if key not in sender_order:
                sender_order.append(key)
        self_keys = {_sender_key(msg) for msg in thread_msgs if self._message_is_self(msg)}
        non_self_keys = [key for key in sender_order if key not in self_keys]
        sender_colors = _thread_color_map(thread_seed, non_self_keys)
        for key in self_keys:
            sender_colors[key] = self_color
        sender_lanes = {key: idx for idx, key in enumerate(non_self_keys)}
        render_records = []
        for record in ordered_records:
            msg = record.get('msg') or {}
            key = _sender_key(msg)
            record = dict(record)
            record['sender_color'] = sender_colors.get(key, self_color)
            record['sender_lane'] = sender_lanes.get(key, 0)
            record['is_self'] = self._message_is_self(msg)
            render_records.append(record)
        participants = self._thread_sender_markup(thread_msgs, sender_colors)
        first_date, last_date = self._thread_date_bounds(thread_msgs)
        parts = []
        if thread_msgs:
            parts.append(f'{len(thread_msgs)} messages')
        attachment_summary = self._thread_attachment_summary(attachments)
        if attachment_summary:
            parts.append(attachment_summary)
        self._thread_view_active = True
        self._current_body = None
        self._current_thread_messages = ordered_records
        accent_r, accent_g, accent_b = self_color
        if Adw.StyleManager.get_default().get_dark():
            self._webview_bg_color = f'rgba({accent_r}, {accent_g}, {accent_b}, 0.30)'
        else:
            self._webview_bg_color = f'rgba({accent_r}, {accent_g}, {accent_b}, 0.22)'
        accent_name = f'message-info-accent-{selected_msg.get("uid") or id(selected_msg)}'
        try:
            self._message_info_accent.set_name(accent_name)
        except Exception:
            pass
        accent_provider = Gtk.CssProvider()
        accent_provider.load_from_string(
            f'#{accent_name} {{ background-color: rgb({accent_r}, {accent_g}, {accent_b}); }}'
        )
        self._message_info_accent.get_style_context().add_provider(
            accent_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._message_info_accent_provider = accent_provider
        self._update_message_info_bar(
            {
                'subject': subject,
                'sender_name': participants,
                'sender_email': '',
                'date': thread_msgs[-1].get('date') if thread_msgs else None,
            },
            attachments,
        )
        self._message_info_subject.set_label(subject)
        self._message_info_sender.set_markup(participants)
        if first_date or last_date:
            self._message_info_date.set_label(f'First: {first_date} • Last: {last_date}')
        else:
            self._message_info_date.set_label('')
        self._message_info_meta.set_label(' • '.join(parts))
        self._message_info_meta.set_visible(bool(parts))
        self._message_info_bar.set_visible(True)
        self._show_attachments(attachments, selected_msg)
        self._thread_reply_target = self._thread_reply_msg_for_records(render_records)
        self._thread_reply_bar.set_visible(len(thread_msgs) > 1)
        self._thread_messages_btn.set_visible(len(thread_msgs) > 1)
        self._populate_thread_sidebar(render_records)
        self._set_thread_sidebar_visible(False)
        if self._active_email_row is not None and self._active_email_row.msg.get('uid') == selected_msg.get('uid'):
            self._active_email_row.set_thread_count(len(thread_msgs))
        self._update_webview_bg()
        self.webview.load_html(
            self._build_thread_html(selected_msg, subject, first_date, last_date, render_records, attachments),
            'about:blank',
        )
        GLib.idle_add(self._scroll_thread_to_bottom)
        return False

    def _build_thread_html(self, selected_msg, subject, first_date, last_date, records, attachments):
        return build_thread_html(
            selected_msg, subject, first_date, last_date, records, attachments,
            is_self_fn=self._message_is_self,
        )

    def _thread_reply_msg_for_records(self, records):
        return thread_reply_msg_for_records(records, is_self_fn=self._message_is_self)

    def _scroll_thread_to_bottom(self):
        if not self._thread_view_active:
            return False
        try:
            script = "window.scrollTo(0, document.body.scrollHeight);"
            self.webview.evaluate_javascript(script, len(script), None, None, None, None, None)
        except Exception:
            pass
        return False

    def _on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED and self._thread_view_active:
            GLib.idle_add(self._scroll_thread_to_bottom)

    def _reply_editor_text(self):
        buffer = self._thread_reply_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, True).strip()

    def _clear_reply_editor(self):
        buffer = self._thread_reply_view.get_buffer()
        buffer.set_text('')

    def _on_thread_reply_send(self, _button=None):
        if not self._thread_view_active or not self._current_thread_messages:
            return
        text = self._reply_editor_text()
        if not text:
            self._show_toast('Write a reply first')
            return
        target = self._thread_reply_target or self._current_thread_messages[-1].get('msg')
        if not target:
            return
        backend = target.get('backend_obj') or self.current_backend
        if not backend:
            self._show_toast('Cannot send reply: no backend')
            return
        own_email = (backend.identity or '').strip()
        sender = (target.get('sender_email') or '').strip()
        if not sender:
            self._show_toast('Cannot send reply: missing sender')
            return
        to = sender
        cc = []
        for m in [record.get('msg') for record in self._current_thread_messages]:
            for addr in (m.get('to_addrs') or []) + (m.get('cc_addrs') or []):
                email = (addr.get('email') or '').strip()
                if email and email.lower() not in {own_email.lower(), sender.lower()} and email not in cc:
                    cc.append(email)
        subject = self._thread_subject_for_messages([record.get('msg') for record in self._current_thread_messages])
        if not subject.lower().startswith('re:'):
            subject = f'Re: {subject}'
        thread_records = list(self._current_thread_messages)
        reply_target = {
            'message_id': target.get('message_id', ''),
            'subject': target.get('subject', subject),
        }
        def send():
            try:
                backend.send_message(to, subject, text, cc=cc, reply_to_msg=reply_target)
                def _append_local_reply():
                    sent_msg = {
                        'uid': f'local-{int(time.time() * 1000)}',
                        'subject': subject,
                        'sender_name': backend.identity,
                        'sender_email': backend.identity,
                        'to_addrs': [{'name': sender, 'email': sender}],
                        'cc_addrs': [{'name': c, 'email': c} for c in cc],
                        'date': datetime.now(timezone.utc),
                        'is_read': True,
                        'has_attachments': False,
                        'snippet': '',
                        'folder': target.get('folder', self.current_folder),
                        'backend': target.get('backend', ''),
                        'account': backend.identity,
                        'backend_obj': backend,
                        'thread_id': target.get('thread_id') or target.get('thread_key') or '',
                        'thread_source': target.get('thread_source', ''),
                        'message_id': '',
                        'thread_count': len(thread_records) + 1,
                        'thread_key': target.get('thread_key'),
                    }
                    records = thread_records + [{
                        'msg': sent_msg,
                        'html': None,
                        'text': text,
                        'attachments': [],
                        'body_text': text,
                        'selected': True,
                    }]
                    attachments = []
                    self._clear_reply_editor()
                    self._show_toast('Reply sent')
                    self._render_thread_view(sent_msg, records, attachments, self._body_load_generation)
                    if self._active_email_row is not None:
                        self._active_email_row.set_thread_count(len(records))
                GLib.idle_add(_append_local_reply)
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Reply failed: {e}')
        threading.Thread(target=send, daemon=True).start()

    def _apply_load_images(self, enabled):
        if getattr(self, '_webview_settings', None) is not None:
            self._webview_settings.set_auto_load_images(bool(enabled))
        current = getattr(self, '_current_body', None)
        if current is not None:
            self._render_body(*current, cache=False)

    def _render_body(self, msg, html, text, attachments, cache=True, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        self._thread_view_active = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._thread_reply_bar.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        sender_seed = (msg.get('account') or (msg.get('backend_obj').identity if msg.get('backend_obj') else '') or msg.get('sender_email') or msg.get('sender_name') or '')
        accent_r, accent_g, accent_b = self._sender_accent_rgb(sender_seed)
        if Adw.StyleManager.get_default().get_dark():
            self._webview_bg_color = f'rgba({accent_r}, {accent_g}, {accent_b}, 0.30)'
        else:
            self._webview_bg_color = f'rgba({accent_r}, {accent_g}, {accent_b}, 0.22)'
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        backend_identity = backend.identity if backend is not None else (msg.get('account') or 'unknown')
        cache_key = (backend_identity, msg.get('folder'), msg['uid'])
        inline_attachments = [att for att in (attachments or []) if _attachment_is_inline_image(att)]
        self._update_message_info_bar(msg, attachments)
        bg_rgb = _email_background_hint(
            html,
            text,
            self._sender_accent_rgb(
                msg.get('account')
                or (msg.get('backend_obj').identity if msg.get('backend_obj') else '')
                or msg.get('sender_email')
                or msg.get('sender_name')
                or ''
            ),
        )
        self._webview_bg_color = f'rgba({bg_rgb[0]}, {bg_rgb[1]}, {bg_rgb[2]}, 1.0)'
        if cache:
            with self._cache_lock:
                self._body_cache[cache_key] = (html, text, attachments)
                self._body_cache.move_to_end(cache_key)
                while len(self._body_cache) > _BODY_CACHE_LIMIT:
                    self._body_cache.popitem(last=False)
            self._store_disk_body(
                _body_cache_key(backend_identity, msg.get('folder'), msg['uid']),
                html,
                text,
                attachments,
                msg.get('date'),
            )
            self._current_body = (msg, html, text, attachments)
        self._update_webview_bg()
        css = self._get_email_css()
        if html:
            content = _inject_styles(_replace_cid_images(html, inline_attachments), css)
        elif text:
            esc = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            content = f'<html><head>{css}</head><body><pre style="white-space:pre-wrap">{esc}</pre></body></html>'
        else:
            content = f'<html><head>{css}</head><body><p style="text-align:center;padding:40px">No content</p></body></html>'
        self.webview.load_html(content, 'about:blank')
        self._show_attachments(attachments, msg)
        return False

    def _set_body(self, msg, html, text, attachments, generation=None):
        return self._render_body(msg, html, text, attachments, cache=True, generation=generation)

    def _set_body_error(self, msg, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        if get_settings().get('debug_logging'):
            print(f'Body error: {msg}', file=sys.stderr)
        self._current_body = None
        self._thread_view_active = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._thread_reply_bar.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        self._webview_bg_color = None
        if self._message_info_bar is not None:
            self._message_info_bar.set_visible(False)
        self.webview.load_html(
            f'<html><body style="padding:20px"><p style="color:red">{msg}</p></body></html>', None
        )
        self._show_toast(f'Failed to load message: {msg}')
        return False

    def _show_empty_viewer(self):
        self._attachment_bar.set_visible(False)
        self._message_info_bar.set_visible(False)
        self._thread_reply_bar.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        self._webview_bg_color = None
        self._current_body = None
        self._thread_view_active = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._update_webview_bg()
        self.webview.load_html('<html><body style="background:transparent"></body></html>', None)

    def _show_loading_viewer(self):
        if self._current_body is not None:
            return
        self._thread_reply_bar.set_visible(False)
        self._show_empty_viewer()

    def _update_webview_bg(self):
        is_dark = Adw.StyleManager.get_default().get_dark()
        rgba = Gdk.RGBA()
        color = getattr(self, '_webview_bg_color', None)
        if color:
            rgba.parse(color)
        else:
            rgba.parse('#1e1e1e' if is_dark else '#f2f1ef')
        self.webview.set_background_color(rgba)

    def _get_email_css(self):
        # Keep the HTML content transparent so the pane tint can match the email's outer edge.
        # Avoid forcing text/background overrides on the message itself; only the shell is styled.
        link = '#3584e4'
        return """<style>
html { background-color: transparent; }
body {
    font-family: -apple-system, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    color: #222222;
    background-color: transparent;
    max-width: 860px;
    width: min(860px, calc(100vw - 32px));
    margin: 24px auto;
    padding: 0 16px;
    box-sizing: border-box;
}
img { max-width: 100%; height: auto; }
a { color: """ + link + """; }
blockquote { border-left: 3px solid #aaa; margin-left: 0; padding-left: 12px; color: #666; }
pre { background: rgba(255,255,255,0.82); padding: 12px; border-radius: 4px; overflow-x: auto; }
</style>"""

    # ── Attachment bar ────────────────────────────────────────────────────────

    def _show_attachments(self, attachments, msg=None):
        while (child := self._attachment_flow.get_first_child()):
            self._attachment_flow.remove(child)
        if not attachments:
            self._attachment_bar.set_visible(False)
            return
        self._attachment_bar.set_visible(True)
        for att in attachments:
            self._attachment_flow.append(self._make_attachment_chip(att, msg))

    def _make_attachment_chip(self, att, msg=None):
        source_msg = att.get('source_msg') or msg
        btn = Gtk.Button()
        btn.add_css_class('attachment-chip')
        btn.add_css_class('flat')
        tooltip = f"{att.get('name', 'attachment')} — {_format_size(att.get('size', 0))}"
        if source_msg is not None:
            sender = source_msg.get('sender_name') or source_msg.get('sender_email') or 'Unknown sender'
            when = _format_received_date(source_msg.get('date')) or _format_date(source_msg.get('date')) or ''
            tooltip = f'{tooltip}\n{sender} {when}'.strip()
        btn.set_tooltip_text(tooltip)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        ct = att.get('content_type', '')
        name = (att.get('name') or '').lower()
        icon_name = (
            _pick_icon_name('image-x-generic-symbolic', 'image-symbolic', 'mail-attachment-symbolic') if 'image' in ct else
            _pick_icon_name('application-pdf-symbolic', 'x-office-document-symbolic', 'document-pdf-symbolic', 'mail-attachment-symbolic') if ('pdf' in ct or name.endswith('.pdf')) else
            _pick_icon_name('package-x-generic-symbolic', 'package-symbolic', 'archive-manager-symbolic', 'mail-attachment-symbolic') if any(x in ct for x in ('zip','archive','compressed')) else
            _pick_icon_name('text-x-generic-symbolic', 'x-office-document-symbolic', 'mail-attachment-symbolic') if 'text' in ct else
            _pick_icon_name('mail-attachment-symbolic', 'paperclip-symbolic')
        )
        box.append(Gtk.Image(icon_name=icon_name, icon_size=Gtk.IconSize.NORMAL))

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name_lbl = Gtk.Label(
            label=att.get('name', 'attachment'),
            halign=Gtk.Align.START,
            max_width_chars=22,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
        )
        size_lbl = Gtk.Label(label=_format_size(att.get('size', 0)), halign=Gtk.Align.START)
        size_lbl.add_css_class('caption')
        size_lbl.add_css_class('dim-label')
        info.append(name_lbl)
        info.append(size_lbl)
        box.append(info)
        save_icon = Gtk.Image(icon_name='document-save-symbolic')
        save_icon.add_css_class('dim-label')
        box.append(save_icon)

        btn.set_child(box)
        btn.connect('clicked', lambda _, a=att, m=source_msg: self._save_attachment(a, m))
        return btn

    def _save_attachment(self, att, msg=None):
        downloads = Path.home() / 'Downloads'
        downloads.mkdir(exist_ok=True)
        name = att.get('name', 'attachment')
        stem, suffix = Path(name).stem, Path(name).suffix
        dest = downloads / name
        counter = 1
        while dest.exists():
            dest = downloads / f'{stem} ({counter}){suffix}'
            counter += 1
        data = att.get('data') or b''
        if data:
            try:
                dest.write_bytes(data)
                self._show_toast(f'Saved to Downloads/{dest.name}')
            except Exception as e:
                self._show_toast(f'Save failed: {e}')
            return
        backend = (_backend_for_message(self.backends, msg) or self.current_backend) if msg else None
        if backend is None and msg:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        if not backend:
            self._show_toast('Cannot fetch attachment: no backend')
            return
        final_dest = dest
        def fetch_and_save():
            try:
                _, _, attachments = backend.fetch_body(msg['uid'], msg.get('folder'))
                fetched_data = b''
                for fetched in attachments or []:
                    if (
                        fetched.get('name') == att.get('name')
                        and fetched.get('content_type') == att.get('content_type')
                        and _attachment_content_id(fetched) == _attachment_content_id(att)
                    ):
                        fetched_data = fetched.get('data', b'')
                        break
                if fetched_data:
                    final_dest.write_bytes(fetched_data)
                    GLib.idle_add(self._show_toast, f'Saved to Downloads/{final_dest.name}')
                else:
                    GLib.idle_add(self._show_toast, 'Attachment data not found')
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Save failed: {e}')
        threading.Thread(target=fetch_and_save, daemon=True).start()

    def _show_toast(self, message):
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    # ── Public: counts + sync badge ───────────────────────────────────────────

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


# ── Email rendering ───────────────────────────────────────────────────────────

def _inject_styles(html, css):
    lower = html.lower()
    if '<head>' in lower:
        idx = lower.index('<head>') + 6
        return html[:idx] + css + html[idx:]
    if '<html>' in lower:
        idx = lower.index('<html>') + 6
        return html[:idx] + f'<head>{css}</head>' + html[idx:]
    return f'<html><head>{css}</head><body>{html}</body></html>'

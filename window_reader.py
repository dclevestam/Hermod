"""Reader, thread view, and attachment behavior for LarkWindow."""

import html as html_lib
import json
import re
import threading
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
    from .body_cache import load_disk_body, prune_disk_body_cache, store_disk_body
    from .settings import get_settings
    from .thread_renderer import build_thread_html, thread_reply_msg_for_records
    from .utils import (
        _DISK_BODY_CACHE_DIR,
        _format_date, _format_received_date, _format_size, _pick_icon_name,
        _log_exception, _body_cache_key,
        _attachment_content_id, _attachment_is_inline_image,
        _replace_cid_images, _thread_inline_image_records,
        _html_to_text, _strip_thread_quotes,
        _rgb_to_hex, _sender_key,
        _thread_palette, _thread_color_map, _email_background_hint,
        _backend_for_identity, _backend_for_message,
        _perf_counter, _log_perf,
    )
    from .window_constants import BODY_CACHE_LIMIT
except ImportError:
    from backends import network_ready, is_transient_network_error
    from body_cache import load_disk_body, prune_disk_body_cache, store_disk_body
    from settings import get_settings
    from thread_renderer import build_thread_html, thread_reply_msg_for_records
    from utils import (
        _DISK_BODY_CACHE_DIR,
        _format_date, _format_received_date, _format_size, _pick_icon_name,
        _log_exception, _body_cache_key,
        _attachment_content_id, _attachment_is_inline_image,
        _replace_cid_images, _thread_inline_image_records,
        _html_to_text, _strip_thread_quotes,
        _rgb_to_hex, _sender_key,
        _thread_palette, _thread_color_map, _email_background_hint,
        _backend_for_identity, _backend_for_message,
        _perf_counter, _log_perf,
    )
    from window_constants import BODY_CACHE_LIMIT


def _inject_styles(html, css):
    lower = html.lower()
    if '<head>' in lower:
        idx = lower.index('<head>') + 6
        return html[:idx] + css + html[idx:]
    if '<html>' in lower:
        idx = lower.index('<html>') + 6
        return html[:idx] + f'<head>{css}</head>' + html[idx:]
    return f'<html><head>{css}</head><body>{html}</body></html>'


def _wrap_email_html_frame(html):
    if not html:
        return html
    body_open = re.search(r'(?is)<body\b[^>]*>', html)
    if body_open:
        body_close = re.search(r'(?is)</body\s*>', html)
        start = body_open.end()
        end = body_close.start() if body_close else len(html)
        inner = html[start:end]
        wrapped = (
            '<div class="lark-message-shell">'
            '<div class="lark-message-frame">'
            f'{inner}'
            '</div>'
            '</div>'
        )
        return html[:start] + wrapped + html[end:]
    return (
        '<div class="lark-message-shell">'
        '<div class="lark-message-frame">'
        f'{html}'
        '</div>'
        '</div>'
    )


class ReaderMixin:
    def _thread_key_for_msg(self, msg):
        if not msg:
            return None
        thread_id = (msg.get('thread_id') or '').strip()
        if thread_id:
            return (msg.get('account', ''), msg.get('backend', ''), thread_id)
        return None

    def _thread_subject_for_messages(self, msgs):
        for m in msgs or []:
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
        self._thread_sidebar_open = bool(visible)
        self._thread_sidebar_revealer.set_reveal_child(self._thread_sidebar_open)
        if self._thread_sidebar_open:
            self._thread_messages_btn.add_css_class('active')
        else:
            self._thread_messages_btn.remove_css_class('active')

    def _sync_backend_cached_read_state(self, msg, is_read):
        if not msg:
            return False
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        if backend is None or not hasattr(backend, 'update_cached_message_read_state'):
            return False
        uid = msg.get('uid')
        if not uid:
            return False
        try:
            return bool(backend.update_cached_message_read_state(msg.get('folder'), uid, is_read))
        except Exception:
            return False

    def _remove_backend_cached_message(self, msg):
        if not msg:
            return False
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get('account'))
        if backend is None or not hasattr(backend, 'remove_cached_message'):
            return False
        uid = msg.get('uid')
        if not uid:
            return False
        try:
            return bool(backend.remove_cached_message(msg.get('folder'), uid))
        except Exception:
            return False

    def _restore_pending_list_scroll(self):
        if self._pending_list_scroll_value is None or getattr(self, '_email_scroll', None) is None:
            return False
        adj = self._email_scroll.get_vadjustment()
        if adj is None:
            return False
        lower = adj.get_lower()
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        target = max(lower, min(self._pending_list_scroll_value, max(lower, upper - page_size)))
        adj.set_value(target)
        self._pending_list_scroll_value = None
        return False

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
            row = self._thread_sidebar_row_cls(record, self._scroll_thread_to_message, accent_rgb=record.get('sender_color'))
            self._thread_sidebar_list.append(row)
        if ordered:
            self._thread_sidebar_list.select_row(self._thread_sidebar_list.get_row_at_index(len(ordered) - 1))

    def _on_thread_sidebar_row_activated(self, _listbox, row):
        if not isinstance(row, self._thread_sidebar_row_cls):
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
        self._message_info_sender.set_use_markup(False)
        self._message_info_sender.set_label(sender)
        self._message_info_date.set_label(f'Received: {_format_received_date(msg.get("date"))}')
        self._message_info_subject.set_label(subject)
        self._message_info_meta.set_label(' • '.join(parts))
        self._message_info_meta.set_visible(bool(parts))
        self._message_info_bar.set_visible(True)

    def _set_original_message_source(self, subject, html, text):
        self._original_message_source = {
            'subject': (subject or '(no subject)').strip() or '(no subject)',
            'html': html or '',
            'text': text or '',
        } if html or text else None
        if getattr(self, '_message_info_original_btn', None) is not None:
            self._message_info_original_btn.set_visible(self._original_message_source is not None and bool(getattr(self, '_thread_view_active', False)))

    def _show_original_message_dialog(self, _button=None):
        source = self._original_message_source
        if not source:
            return
        dialog = Gtk.Dialog(transient_for=self, modal=True)
        dialog.set_title(f'Original: {source.get("subject") or "(no subject)"}')
        dialog.set_default_size(920, 680)
        dialog.add_button('Close', Gtk.ResponseType.CLOSE)
        dialog.connect('response', lambda dlg, *_: dlg.close())
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        html = source.get('html') or ''
        text = source.get('text') or ''
        if html:
            preview = WebKit.WebView(hexpand=True, vexpand=True)
            preview.set_settings(self._webview_settings)
            preview.load_html(html, 'about:blank')
            scroller.set_child(preview)
        else:
            viewer = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            viewer.get_buffer().set_text(text)
            scroller.set_child(viewer)
        content.append(scroller)
        dialog.present()

    def _load_body(self, msg, generation=None):
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
                        self._sync_backend_cached_read_state(msg, True)
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
                    'inline_images': [],
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
                        'inline_images': _thread_inline_image_records(html, fetched_attachments),
                        'selected': thread_msg.get('uid') == selected_uid,
                    })
                    for att in fetched_attachments or []:
                        att_copy = dict(att)
                        att_copy['source_msg'] = thread_msg
                        attachments.append(att_copy)
                GLib.idle_add(self._render_thread_view, msg, records, attachments, generation)
                if get_settings().get('mark_read_on_open'):
                    for unread_msg in [m for m in thread_msgs if not m.get('is_read')]:
                        try:
                            backend.mark_as_read(unread_msg['uid'], unread_msg.get('folder'))
                            unread_msg['is_read'] = True
                            self._sync_backend_cached_read_state(unread_msg, True)
                        except Exception:
                            continue
                    if not msg.get('is_read'):
                        msg['is_read'] = True
                        self._sync_backend_cached_read_state(msg, True)
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
        self._thread_original_sources = {}
        for record in ordered_records:
            uid = (record.get('msg') or {}).get('uid')
            if not uid:
                continue
            self._thread_original_sources[uid] = {
                'subject': (record.get('msg') or {}).get('subject') or subject,
                'html': record.get('html'),
                'text': record.get('text'),
            }
        participants = self._thread_sender_markup(thread_msgs, sender_colors)
        first_date, last_date = self._thread_date_bounds(thread_msgs)
        current_thread_id = (
            selected_msg.get('thread_id')
            or selected_msg.get('thread_key')
            or ''
        )
        if current_thread_id != self._active_thread_id:
            self._active_thread_id = current_thread_id
            self._thread_sidebar_open = False
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
        self._message_info_sender.set_use_markup(True)
        self._message_info_sender.set_markup(participants)
        if first_date or last_date:
            self._message_info_date.set_label(f'First: {first_date} • Last: {last_date}')
        else:
            self._message_info_date.set_label('')
        self._message_info_meta.set_label(' • '.join(parts))
        self._message_info_meta.set_visible(bool(parts))
        self._message_info_bar.set_visible(True)
        selected_record = next(
            (record for record in ordered_records if (record.get('msg') or {}).get('uid') == selected_msg.get('uid')),
            ordered_records[-1] if ordered_records else None,
        )
        self._set_original_message_source(
            selected_msg.get('subject') or subject,
            (selected_record or {}).get('html'),
            (selected_record or {}).get('text'),
        )
        self._show_attachments(attachments, selected_msg)
        self._thread_reply_target = self._thread_reply_msg_for_records(render_records)
        self._thread_reply_bar.set_visible(len(thread_msgs) > 1)
        self._thread_messages_btn.set_visible(len(thread_msgs) > 1)
        self._populate_thread_sidebar(render_records)
        self._set_thread_sidebar_visible(len(thread_msgs) > 1 and self._thread_sidebar_open)
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

    def _on_webview_script_message(self, _manager, message):
        try:
            value = message.get_js_value().to_string()
            payload = json.loads(value)
        except Exception:
            return
        if payload.get('action') != 'original':
            return
        uid = payload.get('uid')
        if not uid:
            return
        source = getattr(self, '_thread_original_sources', {}).get(uid)
        if not source:
            return
        if not (source.get('html') or source.get('text')):
            return
        self._set_original_message_source(source.get('subject'), source.get('html'), source.get('text'))
        self._show_original_message_dialog()

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
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._thread_original_sources = {}
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
        self._set_original_message_source(msg.get('subject'), html, text)
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
                while len(self._body_cache) > BODY_CACHE_LIMIT:
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
            content = _inject_styles(_wrap_email_html_frame(_replace_cid_images(html, inline_attachments)), css)
        elif text:
            esc = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            content = (
                f'<html><head>{css}</head><body>'
                f'<div class="lark-message-shell"><div class="lark-message-frame">'
                f'<pre style="white-space:pre-wrap">{esc}</pre>'
                f'</div></div></body></html>'
            )
        else:
            content = (
                f'<html><head>{css}</head><body>'
                f'<div class="lark-message-shell"><div class="lark-message-frame">'
                f'<p style="text-align:center;padding:40px">No content</p>'
                f'</div></div></body></html>'
            )
        self.webview.load_html(content, 'about:blank')
        self._show_attachments(attachments, msg)
        return False

    def _set_body(self, msg, html, text, attachments, generation=None):
        return self._render_body(msg, html, text, attachments, cache=True, generation=generation)

    def _set_body_error(self, msg, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        if get_settings().get('debug_logging'):
            import sys
            print(f'Body error: {msg}', file=sys.stderr)
        self._current_body = None
        self._thread_view_active = False
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._set_original_message_source('', None, None)
        self._thread_original_sources = {}
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
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._set_original_message_source('', None, None)
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
        link = '#3584e4'
        return """<style>
html { background-color: transparent; }
body {
    font-family: -apple-system, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    color: #222222;
    background-color: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box;
}
.lark-message-shell {
    box-sizing: border-box;
    width: 100%;
    padding: 24px 18px 30px;
}
.lark-message-frame {
    max-width: 1200px;
    width: min(1200px, 100%);
    margin: 0 auto;
}
.lark-message-frame img { max-width: 100%; height: auto; }
.lark-message-frame table { max-width: 100%; }
.lark-message-frame pre { max-width: 100%; }
a { color: """ + link + """; }
blockquote { border-left: 3px solid #aaa; margin-left: 0; padding-left: 12px; color: #666; }
pre { background: rgba(255,255,255,0.82); padding: 12px; border-radius: 4px; overflow-x: auto; }
</style>"""

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
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        ct = att.get('content_type', '')
        name = (att.get('name') or '').lower()
        icon_name = (
            _pick_icon_name('image-x-generic-symbolic', 'image-symbolic', 'mail-attachment-symbolic') if 'image' in ct else
            _pick_icon_name('application-pdf-symbolic', 'x-office-document-symbolic', 'document-pdf-symbolic', 'mail-attachment-symbolic') if ('pdf' in ct or name.endswith('.pdf')) else
            _pick_icon_name('package-x-generic-symbolic', 'package-symbolic', 'archive-manager-symbolic', 'mail-attachment-symbolic') if any(x in ct for x in ('zip', 'archive', 'compressed')) else
            _pick_icon_name('text-x-generic-symbolic', 'x-office-document-symbolic', 'mail-attachment-symbolic') if 'text' in ct else
            _pick_icon_name('mail-attachment-symbolic', 'paperclip-symbolic')
        )
        box.append(Gtk.Image(icon_name=icon_name, icon_size=Gtk.IconSize.NORMAL))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name_lbl = Gtk.Label(label=att.get('name', 'attachment'), halign=Gtk.Align.START, max_width_chars=22, ellipsize=Pango.EllipsizeMode.MIDDLE)
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
                fetched_data = b''
                if hasattr(backend, 'fetch_attachment_data') and att.get('attachment_id'):
                    fetched_data = backend.fetch_attachment_data(msg['uid'], att, msg.get('folder')) or b''
                if not fetched_data:
                    _, _, attachments = backend.fetch_body(msg['uid'], msg.get('folder'))
                    for fetched in attachments or []:
                        if fetched.get('attachment_id') and att.get('attachment_id'):
                            if fetched.get('attachment_id') == att.get('attachment_id'):
                                fetched_data = fetched.get('data', b'')
                                break
                        elif (
                            fetched.get('name') == att.get('name')
                            and fetched.get('content_type') == att.get('content_type')
                            and _attachment_content_id(fetched) == _attachment_content_id(att)
                        ):
                            fetched_data = fetched.get('data', b'')
                            break
                if fetched_data:
                    att['data'] = fetched_data
                    final_dest.write_bytes(fetched_data)
                    GLib.idle_add(self._show_toast, f'Saved to Downloads/{final_dest.name}')
                else:
                    GLib.idle_add(self._show_toast, 'Attachment data not found')
            except Exception as e:
                GLib.idle_add(self._show_toast, f'Save failed: {e}')

        threading.Thread(target=fetch_and_save, daemon=True).start()

    def _show_toast(self, message):
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def _load_disk_body(self, cache_key):
        return load_disk_body(cache_key)

    def _store_disk_body(self, cache_key, html, text, attachments, msg_date=None):
        store_disk_body(cache_key, html, text, attachments, msg_date)

    def _prune_disk_body_cache(self):
        prune_disk_body_cache()

    def _read_message_body_payload(self, msg):
        started = _perf_counter()
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

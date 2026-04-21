"""Reader, thread view, and attachment behavior for HermodWindow."""

import json
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, GLib, WebKit, Pango, Gdk, Gio

try:
    from .styles import apply_accent_css_class
    from .settings import get_settings
    from .thread_renderer import build_clean_body_html, build_thread_html, thread_reply_msg_for_records
    from .utils import (
        _format_date,
        _format_received_date,
        _format_size,
        _pick_icon_name,
        _body_cache_key,
        _attachment_content_id,
        _attachment_is_inline_image,
        _replace_cid_images,
        _thread_inline_image_records,
        _html_to_text,
        _strip_thread_quotes,
        _rgb_to_hex,
        _sender_key,
        _thread_palette,
        _thread_color_map,
        _email_background_hint,
        _email_surface_hint,
        _backend_for_identity,
        _backend_for_message,
    )
    from .window_constants import BODY_CACHE_LIMIT
except ImportError:
    from styles import apply_accent_css_class
    from settings import get_settings
    from thread_renderer import build_clean_body_html, build_thread_html, thread_reply_msg_for_records
    from utils import (
        _format_date,
        _format_received_date,
        _format_size,
        _pick_icon_name,
        _body_cache_key,
        _attachment_content_id,
        _attachment_is_inline_image,
        _replace_cid_images,
        _thread_inline_image_records,
        _html_to_text,
        _strip_thread_quotes,
        _rgb_to_hex,
        _sender_key,
        _thread_palette,
        _thread_color_map,
        _email_background_hint,
        _email_surface_hint,
        _backend_for_identity,
        _backend_for_message,
    )
    from window_constants import BODY_CACHE_LIMIT


def _inject_styles(html, css):
    lower = html.lower()
    if "<head>" in lower:
        idx = lower.index("<head>") + 6
        return html[:idx] + css + html[idx:]
    if "<html>" in lower:
        idx = lower.index("<html>") + 6
        return html[:idx] + f"<head>{css}</head>" + html[idx:]
    return f"<html><head>{css}</head><body>{html}</body></html>"


def _wrap_email_html_frame(html):
    if not html:
        return html
    body_open = re.search(r"(?is)<body\b[^>]*>", html)
    if body_open:
        body_close = re.search(r"(?is)</body\s*>", html)
        start = body_open.end()
        end = body_close.start() if body_close else len(html)
        inner = html[start:end]
        wrapped = (
            '<div class="hermod-message-shell">'
            '<div class="hermod-message-frame">'
            f"{inner}"
            "</div>"
            "</div>"
        )
        return html[:start] + wrapped + html[end:]
    return (
        '<div class="hermod-message-shell">'
        '<div class="hermod-message-frame">'
        f"{html}"
        "</div>"
        "</div>"
    )


class ReaderMixin:
    def _thread_key_for_msg(self, msg):
        if not msg:
            return None
        thread_id = (msg.get("thread_id") or "").strip()
        if thread_id:
            return (msg.get("account", ""), msg.get("backend", ""), thread_id)
        return None

    def _thread_subject_for_messages(self, msgs):
        for m in msgs or []:
            subj = (m.get("subject") or "").strip()
            if subj:
                return subj
        return "(no subject)"

    def _thread_date_bounds(self, msgs):
        dates = [m.get("date") for m in (msgs or []) if m.get("date") is not None]
        if not dates:
            return "", ""
        try:
            first = min(dates)
            last = max(dates)
        except Exception:
            return "", ""
        return _format_received_date(first), _format_received_date(last)

    def _thread_participants_summary(self, msgs):
        seen = []
        for m in msgs or []:
            sender_name = (m.get("sender_name") or "").strip()
            sender_email = (m.get("sender_email") or "").strip()
            label = sender_name or sender_email or "Unknown"
            if (
                sender_email
                and sender_name
                and sender_email.lower() not in sender_name.lower()
            ):
                label = f"{sender_name}"
            if label not in seen:
                seen.append(label)
        if not seen:
            return "Unknown sender"
        if len(seen) <= 3:
            return " • ".join(seen)
        return " • ".join(seen[:3]) + f" • +{len(seen) - 3} more"

    def _extract_thread_body(self, html, text):
        body = text or _html_to_text(html) or ""
        body = _strip_thread_quotes(body)
        return body.strip()

    def _message_is_self(self, msg):
        sender = (msg.get("sender_email") or "").strip().lower()
        if not sender:
            return False
        for backend in self.backends:
            identity = (backend.identity or "").strip().lower()
            if identity and sender == identity:
                return True
        return False

    def _sender_accent_rgb(self, seed_text):
        return _thread_palette(seed_text)

    def _thread_attachment_summary(self, attachments):
        count = len(attachments or [])
        if count == 0:
            return ""
        if count == 1:
            return "1 attachment"
        return f"{count} attachments"

    def _thread_sender_summary(self, msgs):
        seen = []
        for m in msgs or []:
            sender_name = (m.get("sender_name") or "").strip()
            sender_email = (m.get("sender_email") or "").strip()
            label = sender_name or sender_email or "Unknown sender"
            if label not in seen:
                seen.append(label)
        if not seen:
            return "Unknown sender"
        return " • ".join(seen[:4]) + (
            f" • +{len(seen) - 4} more" if len(seen) > 4 else ""
        )

    def _thread_record_for_message(
        self, thread_msg, total, html=None, text="", attachments=None, selected=False
    ):
        thread_msg = dict(thread_msg or {})
        thread_msg["thread_count"] = total
        thread_msg["thread_key"] = self._thread_key_for_msg(thread_msg)
        body_text = self._extract_thread_body(html, text)
        if not body_text:
            body_text = (
                thread_msg.get("snippet") or "Loading..."
            ).strip() or "Loading..."
        attachments = attachments or []
        return {
            "msg": thread_msg,
            "html": html,
            "text": text,
            "attachments": attachments,
            "body_text": body_text,
            "inline_images": _thread_inline_image_records(html, attachments),
            "selected": selected,
        }

    def _thread_is_open(self):
        return (
            bool(getattr(self, "_thread_sidebar_revealer", None))
            and self._thread_sidebar_revealer.get_reveal_child()
        )

    def _set_thread_sidebar_visible(self, visible):
        if getattr(self, "_thread_sidebar_revealer", None) is None:
            return
        self._thread_sidebar_open = bool(visible)
        self._thread_sidebar_revealer.set_reveal_child(self._thread_sidebar_open)
        icon = getattr(self, "_thread_messages_icon", None)
        if self._thread_sidebar_open:
            self._thread_messages_btn.add_css_class("active")
            if icon is not None:
                icon.set_from_icon_name("pan-end-symbolic")
        else:
            self._thread_messages_btn.remove_css_class("active")
            if icon is not None:
                icon.set_from_icon_name("view-list-symbolic")

    def _sync_backend_cached_read_state(self, msg, is_read):
        if not msg:
            return False
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get("account"))
        if backend is None or not hasattr(backend, "update_cached_message_read_state"):
            return False
        uid = msg.get("uid")
        if not uid:
            return False
        try:
            return bool(
                backend.update_cached_message_read_state(
                    msg.get("folder"), uid, is_read
                )
            )
        except Exception:
            return False

    def _remove_backend_cached_message(self, msg):
        if not msg:
            return False
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get("account"))
        if backend is None or not hasattr(backend, "remove_cached_message"):
            return False
        uid = msg.get("uid")
        if not uid:
            return False
        try:
            return bool(backend.remove_cached_message(msg.get("folder"), uid))
        except Exception:
            return False

    def _restore_pending_list_scroll(self):
        target_value = self._pending_list_scroll_value
        if target_value is None or getattr(self, "_email_scroll", None) is None:
            self._pending_list_scroll_value = None
            self._pending_list_scroll_attempts = 0
            self._detach_pending_list_scroll_watcher()
            return False
        adj = self._email_scroll.get_vadjustment()
        if adj is None:
            self._pending_list_scroll_value = None
            self._pending_list_scroll_attempts = 0
            self._detach_pending_list_scroll_watcher()
            return False
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        if upper <= page_size and target_value > 0:
            attempts = getattr(self, "_pending_list_scroll_attempts", 0) + 1
            self._pending_list_scroll_attempts = attempts
            # Hook onto the adjustment so we restore the moment GTK measures
            # the list and `upper` grows past the page size.
            if getattr(self, "_pending_list_scroll_watcher", None) is None:
                try:
                    handler = adj.connect(
                        "notify::upper", self._on_pending_list_scroll_upper_changed
                    )
                except Exception:
                    handler = None
                if handler is not None:
                    self._pending_list_scroll_watcher = (adj, handler)
            if attempts <= 40:
                return True
            self._pending_list_scroll_value = None
            self._pending_list_scroll_attempts = 0
            self._detach_pending_list_scroll_watcher()
            return False
        lower = adj.get_lower()
        target = max(
            lower, min(target_value, max(lower, upper - page_size))
        )
        adj.set_value(target)
        self._pending_list_scroll_value = None
        self._pending_list_scroll_attempts = 0
        self._detach_pending_list_scroll_watcher()
        return False

    def _on_pending_list_scroll_upper_changed(self, adj, _pspec):
        if self._pending_list_scroll_value is None:
            self._detach_pending_list_scroll_watcher()
            return
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        if upper <= page_size and self._pending_list_scroll_value > 0:
            return
        lower = adj.get_lower()
        target = max(
            lower,
            min(self._pending_list_scroll_value, max(lower, upper - page_size)),
        )
        adj.set_value(target)
        self._pending_list_scroll_value = None
        self._pending_list_scroll_attempts = 0
        self._detach_pending_list_scroll_watcher()

    def _detach_pending_list_scroll_watcher(self):
        watcher = getattr(self, "_pending_list_scroll_watcher", None)
        if watcher is None:
            return
        adj, handler = watcher
        try:
            adj.disconnect(handler)
        except Exception:
            pass
        self._pending_list_scroll_watcher = None

    def _populate_thread_sidebar(self, records):
        if getattr(self, "_thread_sidebar_list", None) is None:
            return
        while row := self._thread_sidebar_list.get_row_at_index(0):
            self._thread_sidebar_list.remove(row)
        ordered = sorted(
            list(records or []),
            key=lambda record: (
                record.get("msg", {}).get("date")
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
        )
        for record in ordered:
            row = self._thread_sidebar_row_cls(
                record,
                self._scroll_thread_to_message,
                accent_rgb=record.get("sender_color"),
            )
            self._thread_sidebar_list.append(row)
        if ordered:
            self._thread_sidebar_list.select_row(
                self._thread_sidebar_list.get_row_at_index(len(ordered) - 1)
            )

    def _on_thread_sidebar_row_activated(self, _listbox, row):
        if not isinstance(row, self._thread_sidebar_row_cls):
            return
        self._scroll_thread_to_message(row.record)

    def _scroll_thread_to_message(self, record):
        msg = (record or {}).get("msg") or {}
        uid = msg.get("uid", "")
        if not uid:
            return
        try:
            script = f"""
                (function() {{
                    const el = document.getElementById({json.dumps(f"msg-{uid}")});
                    if (el) {{
                        document.querySelectorAll('.bubble.selected').forEach((node) => node.classList.remove('selected'));
                        el.classList.add('selected');
                        el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                    }}
                }})();
            """
            self.webview.evaluate_javascript(
                script, len(script), None, None, None, None, None
            )
        except Exception:
            pass

    def _on_webview_decide_policy(self, _webview, decision, decision_type):
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        try:
            nav = decision.get_navigation_action()
            request = nav.get_request() if nav is not None else None
            uri = request.get_uri() if request is not None else ""
            nav_type = nav.get_navigation_type() if nav is not None else None
        except Exception:
            return False
        if uri.startswith("hermod://original"):
            try:
                parsed = urllib.parse.urlparse(uri)
                query = urllib.parse.parse_qs(parsed.query)
                uid = (query.get("uid") or [""])[0]
            except Exception:
                uid = ""
            if uid:
                source = getattr(self, "_thread_original_sources", {}).get(uid)
                if source and (source.get("html") or source.get("text")):
                    self._set_original_message_source(
                        source.get("subject"), source.get("html"), source.get("text")
                    )
                    self._show_original_message_dialog()
            decision.ignore()
            return True
        if nav_type != WebKit.NavigationType.LINK_CLICKED:
            return False
        if uri and uri != "about:blank":
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except Exception:
                pass
            decision.ignore()
            return True
        return False

    def _format_message_size(self, msg, attachments=None):
        size = msg.get("size")
        if isinstance(size, int) and size > 0:
            return _format_size(size)
        total = 0
        for att in attachments or []:
            try:
                total += int(att.get("size", 0) or 0)
            except Exception:
                continue
        if total > 0:
            return _format_size(total)
        return ""

    def _update_message_info_bar(self, msg, attachments=None):
        if msg is None:
            self._message_info_bar.set_visible(False)
            return
        subject = (msg.get("subject") or "(no subject)").strip()
        sender_name = (msg.get("sender_name") or "").strip()
        sender_email = (msg.get("sender_email") or "").strip()
        if (
            sender_name
            and sender_email
            and sender_email.lower() not in sender_name.lower()
        ):
            sender = f"{sender_name} <{sender_email}>"
        else:
            sender = sender_name or sender_email or "Unknown sender"
        # Keep legacy labels in sync (they are hidden but may be read by tests).
        self._message_info_sender.set_use_markup(False)
        self._message_info_sender.set_label(sender)
        self._message_info_date.set_label(
            f"Received: {_format_received_date(msg.get('date'))}"
        )
        size = self._format_message_size(msg, attachments)
        legacy_parts = []
        if size:
            legacy_parts.append(f"Size {size}")
        if attachments:
            legacy_parts.append(
                f"{len(attachments)} attachment{'s' if len(attachments) != 1 else ''}"
            )
        self._message_info_meta.set_label(" • ".join(legacy_parts))
        self._message_info_subject.set_label(subject)
        # New reader-meta subtitle: `sender · received-date` for a single message.
        received = _format_received_date(msg.get("date"))
        reader_parts = [p for p in (sender, received) if p]
        reader_meta = getattr(self, "_reader_meta_lbl", None)
        if reader_meta is not None:
            reader_meta.set_label(" · ".join(reader_parts))
            reader_meta.set_visible(bool(reader_parts))
        self._message_info_bar.set_visible(True)

    def _set_original_message_source(self, subject, html, text, uid=None):
        self._original_message_source = (
            {
                "subject": (subject or "(no subject)").strip() or "(no subject)",
                "html": html or "",
                "text": text or "",
            }
            if html or text
            else None
        )
        if getattr(self, "_message_info_original_btn", None) is not None:
            # Per-bubble Original buttons handle thread view; header button is never shown.
            self._message_info_original_btn.set_visible(False)

    def _show_original_message_dialog(self, _button=None):
        source = self._original_message_source
        if not source:
            return
        dialog = Gtk.Dialog(transient_for=self, modal=True)
        dialog.set_title(f"Original: {source.get('subject') or '(no subject)'}")
        dialog.set_default_size(920, 680)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda dlg, *_: dlg.close())
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        html = source.get("html") or ""
        text = source.get("text") or ""
        if html:
            preview = WebKit.WebView(hexpand=True, vexpand=True)
            preview.set_settings(self._webview_settings)
            preview.load_html(html, "about:blank")
            scroller.set_child(preview)
        else:
            viewer = Gtk.TextView(
                editable=False,
                cursor_visible=False,
                monospace=True,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
            )
            viewer.get_buffer().set_text(text)
            scroller.set_child(viewer)
        content.append(scroller)
        dialog.present()

    def _render_thread_view(self, selected_msg, records, attachments, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        # Thread view is always "clean" (bubble layout); the single-message
        # toggle is meaningless here, hide it.
        if getattr(self, "_reader_mode_btn", None) is not None:
            self._reader_mode_btn.set_visible(False)
        if getattr(self, "_reader_mode_menu_btn", None) is not None:
            self._reader_mode_menu_btn.set_visible(False)
        ordered_records = sorted(
            list(records or []),
            key=lambda record: (
                record.get("msg", {}).get("date")
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
        )
        thread_msgs = [record["msg"] for record in ordered_records]
        subject = self._thread_subject_for_messages(thread_msgs)
        thread_seed = str(
            selected_msg.get("thread_id")
            or selected_msg.get("thread_key")
            or selected_msg.get("account")
            or selected_msg.get("sender_email")
            or selected_msg.get("sender_name")
            or subject
            or ""
        )
        thread_account_seed = (
            selected_msg.get("account")
            or (
                selected_msg.get("backend_obj").identity
                if selected_msg.get("backend_obj")
                else ""
            )
            or selected_msg.get("sender_email")
            or selected_msg.get("sender_name")
            or ""
        )
        self_color = self._sender_accent_rgb(thread_account_seed)
        sender_order = []
        for msg in thread_msgs:
            key = _sender_key(msg)
            if key not in sender_order:
                sender_order.append(key)
        self_keys = {
            _sender_key(msg) for msg in thread_msgs if self._message_is_self(msg)
        }
        non_self_keys = [key for key in sender_order if key not in self_keys]
        sender_colors = _thread_color_map(thread_seed, non_self_keys)
        for key in self_keys:
            sender_colors[key] = self_color
        sender_lanes = {key: idx for idx, key in enumerate(non_self_keys)}
        render_records = []
        for record in ordered_records:
            msg = record.get("msg") or {}
            key = _sender_key(msg)
            record = dict(record)
            record["sender_color"] = sender_colors.get(key, self_color)
            record["sender_lane"] = sender_lanes.get(key, 0)
            record["is_self"] = self._message_is_self(msg)
            render_records.append(record)
        self._thread_original_sources = {}
        for record in ordered_records:
            uid = (record.get("msg") or {}).get("uid")
            if not uid:
                continue
            self._thread_original_sources[uid] = {
                "subject": (record.get("msg") or {}).get("subject") or subject,
                "html": record.get("html"),
                "text": record.get("text"),
            }
        participants = self._thread_sender_summary(thread_msgs)
        first_date, last_date = self._thread_date_bounds(thread_msgs)
        current_thread_id = (
            selected_msg.get("thread_id") or selected_msg.get("thread_key") or ""
        )
        if current_thread_id != self._active_thread_id:
            self._active_thread_id = current_thread_id
            self._thread_sidebar_open = False
        parts = []
        attachment_summary = self._thread_attachment_summary(attachments)
        if attachment_summary:
            parts.append(attachment_summary)
        self._thread_view_active = True
        self._current_body = None
        self._current_thread_messages = ordered_records
        accent_r, accent_g, accent_b = self_color
        self._webview_bg_color = f"rgba({accent_r}, {accent_g}, {accent_b}, 0.24)"
        accent_hex = _rgb_to_hex(self_color)
        apply_accent_css_class(self._message_info_accent, accent_hex)
        apply_accent_css_class(self._message_info_sender, accent_hex)
        self._update_message_info_bar(
            {
                "subject": subject,
                "sender_name": participants,
                "sender_email": "",
                "date": thread_msgs[-1].get("date") if thread_msgs else None,
            },
            attachments,
        )
        self._message_info_subject.set_label(subject)
        self._message_info_sender.set_use_markup(False)
        self._message_info_sender.set_label(participants)
        if first_date or last_date:
            self._message_info_date.set_label(
                f"First: {first_date} • Last: {last_date}"
            )
        else:
            self._message_info_date.set_label("")
        self._message_info_meta.set_label(" • ".join(parts))
        # New reader-meta subtitle: `N messages · participants` for threads.
        reader_meta = getattr(self, "_reader_meta_lbl", None)
        if reader_meta is not None:
            thread_count = len(thread_msgs)
            noun = "message" if thread_count == 1 else "messages"
            meta_parts = [f"{thread_count} {noun}"] if thread_count else []
            if participants and participants != "Unknown sender":
                meta_parts.append(participants)
            elif thread_count == 1:
                meta_parts.append(participants or "Unknown sender")
            reader_meta.set_label(" · ".join(meta_parts))
            reader_meta.set_visible(bool(meta_parts))
        self._message_info_bar.set_visible(True)
        selected_record = next(
            (
                record
                for record in ordered_records
                if (record.get("msg") or {}).get("uid") == selected_msg.get("uid")
            ),
            ordered_records[-1] if ordered_records else None,
        )
        self._set_original_message_source(
            selected_msg.get("subject") or subject,
            (selected_record or {}).get("html"),
            (selected_record or {}).get("text"),
            uid=self._active_email_row.msg.get("uid")
            if self._active_email_row
            else selected_msg.get("uid"),
        )
        self._show_attachments(attachments, selected_msg)
        self._thread_reply_target = self._thread_reply_msg_for_records(render_records)
        self._thread_reply_bar.set_visible(True)
        if getattr(self, "_smart_reply_bar", None) is not None:
            self._smart_reply_bar.set_visible(True)
        if getattr(self, "_thread_summary_banner", None) is not None:
            self._thread_summary_banner.set_visible(False)
        if len(thread_msgs) > 1:
            if getattr(self, "_thread_messages_count_lbl", None) is not None:
                self._thread_messages_count_lbl.set_label(str(len(thread_msgs)))
            self._thread_messages_btn.set_visible(True)
        else:
            self._thread_messages_btn.set_visible(False)
        self._populate_thread_sidebar(render_records)
        self._set_thread_sidebar_visible(
            len(thread_msgs) > 1 and self._thread_sidebar_open
        )
        if self._active_email_row is not None and self._active_email_row.msg.get(
            "uid"
        ) == selected_msg.get("uid"):
            self._active_email_row.set_thread_count(len(thread_msgs))
        self._update_webview_bg()
        thread_html = self._build_thread_html(
            selected_msg,
            subject,
            first_date,
            last_date,
            render_records,
            attachments,
        )
        self.webview.load_html(thread_html, "about:blank")
        GLib.idle_add(self._scroll_thread_to_bottom)
        return False

    def _build_thread_html(
        self, selected_msg, subject, first_date, last_date, records, attachments
    ):
        theme = (get_settings().get("theme_mode") or "night").lower()
        return build_thread_html(
            selected_msg,
            subject,
            first_date,
            last_date,
            records,
            attachments,
            is_self_fn=self._message_is_self,
            theme=theme,
        )

    def _thread_reply_msg_for_records(self, records):
        return thread_reply_msg_for_records(records, is_self_fn=self._message_is_self)

    def _scroll_thread_to_bottom(self):
        if not self._thread_view_active:
            return False
        try:
            script = "window.scrollTo(0, document.body.scrollHeight);"
            self.webview.evaluate_javascript(
                script, len(script), None, None, None, None, None
            )
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
        buffer.set_text("")

    def _prefill_reply_with(self, text):
        buffer = self._thread_reply_view.get_buffer()
        buffer.set_text(text or "")
        self._thread_reply_view.grab_focus()

    def _on_thread_reply_send(self, _button=None):
        if not self._thread_view_active or not self._current_thread_messages:
            return
        text = self._reply_editor_text()
        if not text:
            self._show_toast("Write a reply first")
            return
        target = self._thread_reply_target or self._current_thread_messages[-1].get(
            "msg"
        )
        if not target:
            return
        backend = target.get("backend_obj") or self.current_backend
        if not backend:
            self._show_toast("Cannot send reply: no backend")
            return
        own_email = (backend.identity or "").strip()
        sender = (target.get("sender_email") or "").strip()
        if not sender:
            self._show_toast("Cannot send reply: missing sender")
            return
        to = sender
        cc = []
        for m in [record.get("msg") for record in self._current_thread_messages]:
            for addr in (m.get("to_addrs") or []) + (m.get("cc_addrs") or []):
                email = (addr.get("email") or "").strip()
                if (
                    email
                    and email.lower() not in {own_email.lower(), sender.lower()}
                    and email not in cc
                ):
                    cc.append(email)
        subject = self._thread_subject_for_messages(
            [record.get("msg") for record in self._current_thread_messages]
        )
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        thread_records = list(self._current_thread_messages)
        reply_target = {
            "message_id": target.get("message_id", ""),
            "subject": target.get("subject", subject),
        }

        def send():
            try:
                backend.send_message(
                    to, subject, text, cc=cc, reply_to_msg=reply_target
                )

                def _append_local_reply():
                    sent_msg = {
                        "uid": f"local-{int(time.time() * 1000)}",
                        "subject": subject,
                        "sender_name": backend.identity,
                        "sender_email": backend.identity,
                        "to_addrs": [{"name": sender, "email": sender}],
                        "cc_addrs": [{"name": c, "email": c} for c in cc],
                        "date": datetime.now(timezone.utc),
                        "is_read": True,
                        "has_attachments": False,
                        "snippet": "",
                        "folder": target.get("folder", self.current_folder),
                        "backend": target.get("backend", ""),
                        "account": backend.identity,
                        "backend_obj": backend,
                        "thread_id": target.get("thread_id")
                        or target.get("thread_key")
                        or "",
                        "thread_source": target.get("thread_source", ""),
                        "message_id": "",
                        "thread_count": len(thread_records) + 1,
                        "thread_key": target.get("thread_key"),
                    }
                    records = thread_records + [
                        {
                            "msg": sent_msg,
                            "html": None,
                            "text": text,
                            "attachments": [],
                            "body_text": text,
                            "selected": True,
                        }
                    ]
                    attachments = []
                    self._clear_reply_editor()
                    self._show_toast("Reply sent")
                    self._render_thread_view(
                        sent_msg, records, attachments, self._body_load_generation
                    )
                    if self._active_email_row is not None:
                        self._active_email_row.set_thread_count(len(records))

                GLib.idle_add(_append_local_reply)
            except Exception as e:
                GLib.idle_add(self._show_toast, f"Reply failed: {e}")

        threading.Thread(target=send, daemon=True).start()

    def _apply_load_images(self, enabled):
        if getattr(self, "_webview_settings", None) is not None:
            self._webview_settings.set_auto_load_images(bool(enabled))
        current = getattr(self, "_current_body", None)
        if current is not None:
            self._render_body(*current, cache=False)

    def _resolve_reader_mode_for_msg(self, msg, html="", text="", clean_body=""):
        """Pick the initial reader mode for a single-message open.

        Precedence (strongest first):
          1. Explicit per-sender opt-out — user said "always original"
             for this sender, always wins.
          2. Shape heuristic — score the message: image-heavy,
             structured tables, transactional subject, or extraction
             ratio that suggests the HTML layout *is* the content.
          3. Default to clean.
        """
        sender = (msg.get("sender_email") or "").strip().lower()
        if sender:
            prefs = get_settings().get("senders_prefer_original") or []
            try:
                if sender in {str(s).strip().lower() for s in prefs}:
                    return "original"
            except Exception:
                pass
        if self._heuristic_prefers_original(msg, html, text, clean_body):
            return "original"
        return "clean"

    def _heuristic_prefers_original(self, msg, html, text, clean_body):
        """Return True when the message's shape suggests the original
        HTML will read better than the quoted-stripped text.

        Signals (any one fires):
          a) Design-heavy: many <img> and very little usable text after
             extraction. Marketing newsletters fall here (Womier-style).
          b) Structured receipt/invoice: <table> plus URL clutter in the
             extracted body. Payment receipts (Anthropic/Stripe) fall
             here — the original renders line items cleanly, the
             extracted text interleaves "(https://…)" everywhere.
          c) Tiny extraction ratio: < 5% of the original HTML survives
             extraction AND the HTML is sizeable. Means the content
             IS the markup (hero images, CTA buttons, etc.).
          d) Transactional subject keywords + multiple images.
          e) URL density in the extracted text exceeds ~1 URL per
             400 chars — reads like "[Link](https://…)" soup in clean
             mode.
        """
        if not html:
            return False
        try:
            lower_html = html.lower()
            img_count = lower_html.count("<img ")
            has_table = "<table" in lower_html
            clean_stripped = (clean_body or "").strip()
            clean_len = len(clean_stripped)
            html_len = len(html)
            url_count = clean_stripped.count("(http")

            # a) Design-heavy
            if img_count >= 3 and clean_len < 240:
                return True

            # b) Structured table with URL clutter in the extraction
            if has_table and url_count >= 3:
                return True

            # c) Content is the markup
            if html_len > 5000 and clean_len > 0 and (clean_len / html_len) < 0.05:
                return True

            # d) Transactional subject + imagery
            subject_lower = str(msg.get("subject") or "").lower()
            transactional_kw = (
                "receipt",
                "invoice",
                "your order",
                "order #",
                "order confirmation",
                "confirmation",
                "shipment",
                "shipping",
                "delivery",
                "kvitto",
            )
            if img_count >= 2 and any(kw in subject_lower for kw in transactional_kw):
                return True

            # e) URL density in the extracted text
            if clean_len > 400 and url_count * 400 > clean_len:
                return True
        except Exception:
            return False
        return False

    def _on_reader_mode_toggle(self):
        """Header button click: flip between clean and original."""
        current = getattr(self, "_reader_view_mode", "clean")
        self._set_reader_mode("original" if current == "clean" else "clean")

    def _on_reader_mode_sender_pref_toggled(self, check_button):
        """Popover checkbox: persist `always show original from sender`."""
        if self._current_body is None:
            return
        msg = self._current_body[0]
        prefer_original = bool(check_button.get_active())
        if not self._toggle_sender_prefer_original(msg, prefer_original):
            return
        # If the user just opted this sender out of clean mode, flip
        # the current view to original immediately so the change is
        # visible. If they un-opted-out, don't auto-flip — let them
        # click the main toggle.
        if prefer_original and self._reader_view_mode != "original":
            self._set_reader_mode("original")

    def _sync_reader_mode_popover(self, msg):
        """Keep the popover checkbox state in sync with persisted settings
        and with whether the active message has a usable sender."""
        check = getattr(self, "_reader_mode_sender_check", None)
        menu_btn = getattr(self, "_reader_mode_menu_btn", None)
        if check is None or menu_btn is None:
            return
        sender = (msg or {}).get("sender_email") if msg else ""
        sender = (sender or "").strip()
        if not sender:
            menu_btn.set_sensitive(False)
            check.set_label("Always show original from this sender")
            return
        menu_btn.set_sensitive(True)
        check.set_label(f"Always show original from {sender}")
        # set_active triggers the 'toggled' signal; block while syncing.
        try:
            check.handler_block_by_func(self._on_reader_mode_sender_pref_toggled)
        except (TypeError, AttributeError):
            pass
        check.set_active(self._sender_prefers_original(msg))
        try:
            check.handler_unblock_by_func(self._on_reader_mode_sender_pref_toggled)
        except (TypeError, AttributeError):
            pass

    def _set_reader_mode(self, mode):
        """Flip the current single-message view mode and re-render."""
        mode = "original" if mode == "original" else "clean"
        if self._current_body is None:
            self._reader_view_mode = mode
            self._sync_reader_mode_toggle()
            return
        self._reader_view_mode = mode
        msg, html, text, attachments = self._current_body
        # `cache=False` so we don't re-persist identical bytes; mode is
        # a view-only preference.
        self._render_body(
            msg, html, text, attachments, cache=False, mode=mode
        )

    def _sync_reader_mode_toggle(self):
        """Mirror _reader_view_mode onto the header toggle button so its
        icon, tooltip, and active state reflect what's on screen."""
        btn = getattr(self, "_reader_mode_btn", None)
        if btn is None:
            return
        mode = getattr(self, "_reader_view_mode", "clean")
        can_toggle = bool(getattr(self, "_reader_mode_clean_available", False))
        btn.set_sensitive(can_toggle)
        if mode == "original":
            btn.set_tooltip_text("Switch to clean view")
            btn.remove_css_class("reader-mode-clean")
            btn.add_css_class("reader-mode-original")
        else:
            btn.set_tooltip_text("Switch to original HTML view")
            btn.remove_css_class("reader-mode-original")
            btn.add_css_class("reader-mode-clean")

    def _toggle_sender_prefer_original(self, msg, prefer_original):
        """Add or remove the message's sender from the persistent list
        of senders who should always open in original view."""
        sender = (msg.get("sender_email") or "").strip().lower()
        if not sender:
            return False
        settings = get_settings()
        current = settings.get("senders_prefer_original") or []
        as_set = {str(s).strip().lower() for s in current if str(s).strip()}
        if prefer_original:
            as_set.add(sender)
        else:
            as_set.discard(sender)
        settings.set("senders_prefer_original", sorted(as_set))
        return True

    def _sender_prefers_original(self, msg):
        sender = (msg.get("sender_email") or "").strip().lower()
        if not sender:
            return False
        prefs = get_settings().get("senders_prefer_original") or []
        return sender in {str(s).strip().lower() for s in prefs}

    def _render_body(self, msg, html, text, attachments, cache=True, generation=None, mode=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        self._thread_view_active = False
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._thread_original_sources = {}
        self._thread_reply_bar.set_visible(True)
        if getattr(self, "_smart_reply_bar", None) is not None:
            self._smart_reply_bar.set_visible(True)
        if getattr(self, "_thread_summary_banner", None) is not None:
            self._thread_summary_banner.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        sender_seed = (
            msg.get("account")
            or (msg.get("backend_obj").identity if msg.get("backend_obj") else "")
            or msg.get("sender_email")
            or msg.get("sender_name")
            or ""
        )
        accent_r, accent_g, accent_b = self._sender_accent_rgb(sender_seed)
        self._webview_bg_color = f"rgba({accent_r}, {accent_g}, {accent_b}, 0.24)"
        backend = _backend_for_message(self.backends, msg) or self.current_backend
        if backend is None:
            backend = _backend_for_identity(self.backends, msg.get("account"))
        backend_identity = (
            backend.identity
            if backend is not None
            else (msg.get("account") or "unknown")
        )
        cache_key = (backend_identity, msg.get("folder"), msg["uid"])
        inline_attachments = [
            att for att in (attachments or []) if _attachment_is_inline_image(att)
        ]
        self._update_message_info_bar(msg, attachments)
        self._set_original_message_source(msg.get("subject"), html, text)
        surface_hint = _email_surface_hint(html, text)
        if surface_hint is not None:
            bg_rgb = surface_hint["background_rgb"]
            fg_rgb = surface_hint["foreground_rgb"]
            self._webview_bg_color = f"rgba({bg_rgb[0]}, {bg_rgb[1]}, {bg_rgb[2]}, 1.0)"
            self._email_text_color = f"#{fg_rgb[0]:02x}{fg_rgb[1]:02x}{fg_rgb[2]:02x}"
        else:
            # Emails are authored for a light background (Gmail / Outlook-style),
            # so the reader body always uses a light surface with dark text,
            # regardless of the app's Night/Day theme — same as the design
            # prototype's Night-inbox screenshot.
            self._webview_bg_color = "rgba(255, 255, 255, 1.0)"
            self._email_text_color = "#1b2024"
        if cache:
            with self._cache_lock:
                self._body_cache[cache_key] = (html, text, attachments)
                self._body_cache.move_to_end(cache_key)
                while len(self._body_cache) > BODY_CACHE_LIMIT:
                    self._body_cache.popitem(last=False)
            self._store_disk_body(
                _body_cache_key(backend_identity, msg.get("folder"), msg["uid"]),
                html,
                text,
                attachments,
                msg.get("date"),
            )
            self._current_body = (msg, html, text, attachments)
        # Pre-compute clean body so the mode resolver has the real
        # extraction in hand (the shape heuristic scores it directly).
        clean_body = self._extract_thread_body(html, text)
        clean_available = bool(clean_body.strip())
        self._reader_mode_clean_available = clean_available
        # Resolve the view mode once per render: explicit `mode=` wins
        # (the header toggle uses that path); else per-sender opt-out
        # or shape heuristic; else default to clean.
        if mode is None:
            mode = self._resolve_reader_mode_for_msg(msg, html, text, clean_body)
        if mode == "clean" and not clean_available:
            mode = "original"
        self._reader_view_mode = mode
        self._sync_reader_mode_toggle()
        self._sync_reader_mode_popover(msg)
        # Show the mode toggle + sender-pref overflow only in single-message
        # view; threads are always clean by design and the toggle would
        # have no meaning there.
        if getattr(self, "_reader_mode_btn", None) is not None:
            self._reader_mode_btn.set_visible(True)
        if getattr(self, "_reader_mode_menu_btn", None) is not None:
            self._reader_mode_menu_btn.set_visible(True)
        self._update_webview_bg()
        if mode == "clean":
            # Clean view sits on the dark reader surface (same as thread
            # bubbles); skip the light email frame entirely so switching
            # back to original visibly changes the background.
            self._webview_bg_color = "rgba(11, 15, 18, 1.0)"
            self._update_webview_bg()
            content = build_clean_body_html(clean_body)
            self.webview.load_html(content, "about:blank")
            self._show_attachments(attachments, msg)
            return False
        css = self._get_email_css(self._email_text_color)
        if html:
            content = _inject_styles(
                _wrap_email_html_frame(_replace_cid_images(html, inline_attachments)),
                css,
            )
        elif text:
            esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            content = (
                f"<html><head>{css}</head><body>"
                f'<div class="hermod-message-shell"><div class="hermod-message-frame">'
                f'<pre style="white-space:pre-wrap">{esc}</pre>'
                f"</div></div></body></html>"
            )
        else:
            content = (
                f"<html><head>{css}</head><body>"
                f'<div class="hermod-message-shell"><div class="hermod-message-frame">'
                f'<p style="text-align:center;padding:40px">No content</p>'
                f"</div></div></body></html>"
            )
        self.webview.load_html(content, "about:blank")
        self._show_attachments(attachments, msg)
        return False

    def _set_body(self, msg, html, text, attachments, generation=None):
        return self._render_body(
            msg, html, text, attachments, cache=True, generation=generation
        )

    def _set_body_error(self, msg, generation=None):
        if generation is not None and generation != self._body_load_generation:
            return False
        if get_settings().get("debug_logging"):
            import sys

            print(f"Body error: {msg}", file=sys.stderr)
        self._current_body = None
        self._thread_view_active = False
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._set_original_message_source("", None, None)
        self._thread_original_sources = {}
        self._thread_reply_bar.set_visible(False)
        if getattr(self, "_smart_reply_bar", None) is not None:
            self._smart_reply_bar.set_visible(False)
        if getattr(self, "_thread_summary_banner", None) is not None:
            self._thread_summary_banner.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        if getattr(self, "_reader_mode_btn", None) is not None:
            self._reader_mode_btn.set_visible(False)
        if getattr(self, "_reader_mode_menu_btn", None) is not None:
            self._reader_mode_menu_btn.set_visible(False)
        self._webview_bg_color = None
        if self._message_info_bar is not None:
            self._message_info_bar.set_visible(False)
        self.webview.load_html(
            f'<html><body style="padding:20px"><p style="color:red">{msg}</p></body></html>',
            None,
        )
        self._show_toast(f"Failed to load message: {msg}")
        return False

    def _show_empty_viewer(self):
        if getattr(self, "_startup_status_active", False):
            self._show_startup_status_view()
            return
        self._attachment_bar.set_visible(False)
        self._message_info_bar.set_visible(False)
        self._thread_reply_bar.set_visible(False)
        if getattr(self, "_smart_reply_bar", None) is not None:
            self._smart_reply_bar.set_visible(False)
        if getattr(self, "_thread_summary_banner", None) is not None:
            self._thread_summary_banner.set_visible(False)
        self._thread_messages_btn.set_visible(False)
        self._set_thread_sidebar_visible(False)
        self._webview_bg_color = None
        self._current_body = None
        self._thread_view_active = False
        self._active_thread_id = None
        self._thread_sidebar_open = False
        self._current_thread_messages = None
        self._thread_reply_target = None
        self._set_original_message_source("", None, None)
        self._update_webview_bg()
        css = self._get_email_css(
            self._email_text_color if hasattr(self, "_email_text_color") else "#666666"
        )
        self.webview.load_html(
            f"<html><head>{css}</head><body>"
            f'<div class="hermod-message-shell"><div class="hermod-message-frame">'
            f'<p style="text-align:center;padding:40px">Select a message</p>'
            f"</div></div></body></html>",
            "about:blank",
        )

    def _show_loading_viewer(self):
        if self._current_body is not None:
            return
        self._thread_reply_bar.set_visible(False)
        if getattr(self, "_startup_status_active", False):
            self._show_startup_status_view()
        else:
            css = self._get_email_css(
                self._email_text_color
                if hasattr(self, "_email_text_color")
                else "#666666"
            )
            self._update_webview_bg()
            self.webview.load_html(
                f"<html><head>{css}</head><body>"
                f'<div class="hermod-message-shell"><div class="hermod-message-frame">'
                f'<p style="text-align:center;padding:40px">Loading message...</p>'
                f"</div></div></body></html>",
                "about:blank",
            )

    def _update_webview_bg(self):
        rgba = Gdk.RGBA()
        color = getattr(self, "_webview_bg_color", None)
        if color:
            rgba.parse(color)
        else:
            rgba.parse("#101312")
        self.webview.set_background_color(rgba)

    def _get_email_css(self, text_color):
        link = "#2E6A70"
        return (
            """<style>
html { background-color: transparent; }
body {
    font-family: "DejaVu Sans", -apple-system, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    color: """
            + text_color
            + """;
    background-color: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box;
}
.hermod-message-shell {
    box-sizing: border-box;
    width: 100%;
    padding: 20px 18px 26px;
}
.hermod-message-frame {
    width: 100%;
    max-width: 1160px;
    margin: 0;
    padding: 0;
}
.hermod-message-frame img { max-width: 100%; height: auto; }
.hermod-message-frame table { max-width: 100%; }
.hermod-message-frame pre { max-width: 100%; }
a { color: """
            + link
            + """; }
blockquote { border-left: 3px solid rgba(223,228,222,0.24); margin-left: 0; padding-left: 12px; color: rgba(183,190,184,0.90); }
pre { background: rgba(18,23,21,0.92); padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid rgba(223,228,222,0.08); }
</style>"""
        )

    def _show_attachments(self, attachments, msg=None):
        while child := self._attachment_flow.get_first_child():
            self._attachment_flow.remove(child)
        if not attachments:
            self._attachment_bar.set_visible(False)
            return
        self._attachment_bar.set_visible(True)
        for att in attachments:
            self._attachment_flow.append(self._make_attachment_chip(att, msg))

    def _make_attachment_chip(self, att, msg=None):
        source_msg = att.get("source_msg") or msg
        btn = Gtk.Button()
        btn.add_css_class("attachment-chip")
        btn.add_css_class("flat")
        tooltip = (
            f"{att.get('name', 'attachment')} — {_format_size(att.get('size', 0))}"
        )
        if source_msg is not None:
            sender = (
                source_msg.get("sender_name")
                or source_msg.get("sender_email")
                or "Unknown sender"
            )
            when = (
                _format_received_date(source_msg.get("date"))
                or _format_date(source_msg.get("date"))
                or ""
            )
            tooltip = f"{tooltip}\n{sender} {when}".strip()
        btn.set_tooltip_text(tooltip)
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=4,
            margin_bottom=4,
            margin_start=4,
            margin_end=4,
        )
        ct = att.get("content_type", "")
        name = (att.get("name") or "").lower()
        icon_name = (
            _pick_icon_name(
                "image-x-generic-symbolic", "image-symbolic", "mail-attachment-symbolic"
            )
            if "image" in ct
            else _pick_icon_name(
                "application-pdf-symbolic",
                "x-office-document-symbolic",
                "document-pdf-symbolic",
                "mail-attachment-symbolic",
            )
            if ("pdf" in ct or name.endswith(".pdf"))
            else _pick_icon_name(
                "package-x-generic-symbolic",
                "package-symbolic",
                "archive-manager-symbolic",
                "mail-attachment-symbolic",
            )
            if any(x in ct for x in ("zip", "archive", "compressed"))
            else _pick_icon_name(
                "text-x-generic-symbolic",
                "x-office-document-symbolic",
                "mail-attachment-symbolic",
            )
            if "text" in ct
            else _pick_icon_name("mail-attachment-symbolic", "paperclip-symbolic")
        )
        box.append(Gtk.Image(icon_name=icon_name, icon_size=Gtk.IconSize.NORMAL))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name_lbl = Gtk.Label(
            label=att.get("name", "attachment"),
            halign=Gtk.Align.START,
            max_width_chars=22,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
        )
        size_lbl = Gtk.Label(
            label=_format_size(att.get("size", 0)), halign=Gtk.Align.START
        )
        size_lbl.add_css_class("caption")
        size_lbl.add_css_class("dim-label")
        info.append(name_lbl)
        info.append(size_lbl)
        box.append(info)
        save_icon = Gtk.Image(icon_name="document-save-symbolic")
        save_icon.add_css_class("dim-label")
        box.append(save_icon)
        btn.set_child(box)
        btn.connect(
            "clicked", lambda _, a=att, m=source_msg: self._save_attachment(a, m)
        )
        return btn

    def _save_attachment(self, att, msg=None):
        downloads = Path.home() / "Downloads"
        downloads.mkdir(exist_ok=True)
        name = att.get("name", "attachment")
        stem, suffix = Path(name).stem, Path(name).suffix
        dest = downloads / name
        counter = 1
        while dest.exists():
            dest = downloads / f"{stem} ({counter}){suffix}"
            counter += 1
        data = att.get("data") or b""
        if data:
            try:
                dest.write_bytes(data)
                self._show_toast(f"Saved to Downloads/{dest.name}")
            except Exception as e:
                self._show_toast(f"Save failed: {e}")
            return
        backend = (
            (_backend_for_message(self.backends, msg) or self.current_backend)
            if msg
            else None
        )
        if backend is None and msg:
            backend = _backend_for_identity(self.backends, msg.get("account"))
        if not backend:
            self._show_toast("Cannot fetch attachment: no backend")
            return
        final_dest = dest

        def fetch_and_save():
            try:
                fetched_data = b""
                if hasattr(backend, "fetch_attachment_data") and att.get(
                    "attachment_id"
                ):
                    fetched_data = (
                        backend.fetch_attachment_data(
                            msg["uid"], att, msg.get("folder")
                        )
                        or b""
                    )
                if not fetched_data:
                    _, _, attachments = backend.fetch_body(
                        msg["uid"], msg.get("folder")
                    )
                    for fetched in attachments or []:
                        if fetched.get("attachment_id") and att.get("attachment_id"):
                            if fetched.get("attachment_id") == att.get("attachment_id"):
                                fetched_data = fetched.get("data", b"")
                                break
                        elif (
                            fetched.get("name") == att.get("name")
                            and fetched.get("content_type") == att.get("content_type")
                            and _attachment_content_id(fetched)
                            == _attachment_content_id(att)
                        ):
                            fetched_data = fetched.get("data", b"")
                            break
                if fetched_data:
                    att["data"] = fetched_data
                    final_dest.write_bytes(fetched_data)
                    GLib.idle_add(
                        self._show_toast, f"Saved to Downloads/{final_dest.name}"
                    )
                else:
                    GLib.idle_add(self._show_toast, "Attachment data not found")
            except Exception as e:
                GLib.idle_add(self._show_toast, f"Save failed: {e}")

        threading.Thread(target=fetch_and_save, daemon=True).start()

    def _show_toast(self, message):
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

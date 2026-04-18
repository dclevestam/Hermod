"""GTK row widget classes for the Hermod inbox and sidebar."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango, GObject

try:
    from .styles import apply_accent_css_class
    from .utils import (
        _format_date,
        _format_received_date,
        _pick_icon_name,
        _make_count_slot,
        _sender_initials,
        _thread_palette,
        _thread_message_summary,
        _rgb_to_hex,
    )
except ImportError:
    from styles import apply_accent_css_class
    from utils import (
        _format_date,
        _format_received_date,
        _pick_icon_name,
        _make_count_slot,
        _sender_initials,
        _thread_palette,
        _thread_message_summary,
        _rgb_to_hex,
    )


# ── Email list items / rows ──────────────────────────────────────────────────


class MailListItem(GObject.Object):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.widget = None

    def bind_widget(self, widget):
        self.widget = widget

    def unbind_widget(self, widget):
        if self.widget is widget:
            self.widget = None

    def set_selected(self, selected):
        if self.widget is not None:
            self.widget.set_selected(selected)

    def grab_focus(self):
        if self.widget is not None:
            self.widget.grab_focus()


class MessageListItem(MailListItem):
    def __init__(self, msg, accent_class=None):
        super().__init__("message")
        self.msg = msg
        self.accent_class = accent_class

    def set_thread_count(self, count):
        self.msg["thread_count"] = count
        if self.widget is not None:
            self.widget.set_thread_count(count)

    def mark_read(self):
        self.msg["is_read"] = True
        if self.widget is not None:
            self.widget.mark_read()

    def mark_unread(self):
        self.msg["is_read"] = False
        if self.widget is not None:
            self.widget.mark_unread()


class LoadMoreListItem(MailListItem):
    def __init__(self, label="Load more"):
        super().__init__("load_more")
        self.label = label


class EmailRow(Gtk.Box):
    def __init__(self, msg, on_reply, on_reply_all, on_delete, accent_class=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.msg = msg
        self._hovering = False
        self._selected = False
        self.add_css_class("email-row")
        if accent_class:
            self.add_css_class(accent_class)

        overlay = Gtk.Overlay()

        outer = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=8,
            margin_bottom=8,
            margin_start=10,
            margin_end=10,
            spacing=8,
        )

        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.set_size_request(8, 8)
        dot.add_css_class("unread-dot")
        self._dot = dot
        if msg.get("is_read"):
            dot.add_css_class("hidden")
        outer.append(dot)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sender = Gtk.Label(
            label=msg.get("sender_name", ""),
            halign=Gtk.Align.START,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=28,
        )
        self._sender_label = sender
        row1.append(sender)

        if msg.get("thread_count", 1) > 1:
            thread_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=3,
                valign=Gtk.Align.CENTER,
            )
            thread_box.add_css_class("thread-indicator")
            thread_icon = Gtk.Image(
                icon_name=_pick_icon_name(
                    "chat-bubbles-symbolic",
                    "mail-message-new-symbolic",
                    "chat-symbolic",
                    "mail-reply-sender-symbolic",
                ),
                pixel_size=13,
            )
            thread_box.append(thread_icon)
            thread_lbl = Gtk.Label(label=str(msg.get("thread_count", 1)))
            thread_lbl.add_css_class("thread-badge")
            thread_lbl.add_css_class("thread-badge-threaded")
            thread_lbl.set_visible(msg.get("thread_count", 1) > 1)
            thread_box.append(thread_lbl)
            self._thread_box = thread_box
            self._thread_label = thread_lbl
            row1.append(thread_box)
        else:
            self._thread_box = None
            self._thread_label = None

        if msg.get("has_attachments"):
            clip = Gtk.Image(
                icon_name=_pick_icon_name(
                    "mail-attachment-symbolic", "paperclip-symbolic"
                ),
                pixel_size=13,
            )
            clip.add_css_class("dim-label")
            clip.set_margin_end(4)
            row1.append(clip)

        date_lbl = Gtk.Label(
            label=_format_date(msg.get("date")),
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
        )
        date_lbl.add_css_class("caption")
        date_lbl.add_css_class("dim-label")
        self._date_label = date_lbl
        row1.append(date_lbl)
        col.append(row1)

        subj = Gtk.Label(
            label=msg.get("subject", ""),
            halign=Gtk.Align.START,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=50,
        )
        subj.add_css_class("caption")
        self._subject_label = subj
        self._apply_unread_style()
        col.append(subj)

        outer.append(col)
        overlay.set_child(outer)

        sender_email = (msg.get("sender_email") or "").strip()
        if sender_email:
            self.set_tooltip_text(sender_email)

        action_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            spacing=2,
        )
        action_box.add_css_class("email-actions")
        action_box.set_visible(False)

        for icon, tip, cb in [
            ("user-trash-symbolic", "Delete (d)", lambda _: on_delete(self, msg)),
        ]:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, has_frame=False)
            btn.add_css_class("flat")
            btn.connect("clicked", cb)
            action_box.append(btn)

        overlay.add_overlay(action_box)
        self._action_box = action_box

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_hover_enter)
        motion.connect("leave", self._on_hover_leave)
        self.add_controller(motion)

        self.append(overlay)
        self._sync_action_visibility()

    def set_selected(self, selected):
        self._selected = bool(selected)
        if self._selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")
        self._sync_action_visibility()

    def set_thread_count(self, count):
        self.msg["thread_count"] = count
        if getattr(self, "_thread_label", None) is not None:
            self._thread_label.set_label(str(count))
            self._thread_label.set_visible(count > 1)
            self._thread_box.set_visible(bool(self.msg.get("thread_id")) or count > 1)

    def _on_hover_enter(self, *_):
        self._hovering = True
        self._sync_action_visibility()

    def _on_hover_leave(self, *_):
        self._hovering = False
        self._sync_action_visibility()

    def _sync_action_visibility(self):
        self._action_box.set_visible(self._hovering or self._selected)

    def mark_read(self):
        self.msg["is_read"] = True
        self._dot.add_css_class("hidden")
        self._apply_unread_style()

    def mark_unread(self):
        self.msg["is_read"] = False
        self._dot.remove_css_class("hidden")
        self._apply_unread_style()

    def _apply_unread_style(self):
        if self.msg.get("is_read", True):
            self._sender_label.remove_css_class("heading")
            self._subject_label.remove_css_class("heading")
        else:
            self._sender_label.remove_css_class("heading")
            self._subject_label.add_css_class("heading")


class LoadMoreRow(Gtk.Box):
    def __init__(self, label, on_activate):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self._selected = False
        self.add_css_class("load-more-row")
        self.set_margin_top(6)
        self.set_margin_bottom(10)
        self.set_margin_start(10)
        self.set_margin_end(10)

        button = Gtk.Button(label=label)
        button.add_css_class("flat")
        button.set_halign(Gtk.Align.CENTER)
        button.set_hexpand(True)
        button.connect("clicked", lambda *_: on_activate())
        self._button = button
        self.append(button)

    def set_selected(self, selected):
        self._selected = bool(selected)
        if self._selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


# ── Thread sidebar row ────────────────────────────────────────────────────────


class ThreadNavRow(Gtk.ListBoxRow):
    def __init__(self, record, on_activate, accent_rgb=None):
        super().__init__()
        self.record = record
        self.msg = record.get("msg") or {}
        self.uid = self.msg.get("uid", "")
        self.add_css_class("thread-sidebar-row")

        msg = self.msg
        sender_name = (
            msg.get("sender_name") or msg.get("sender_email") or "Unknown sender"
        ).strip()
        sender_email = (msg.get("sender_email") or "").strip()
        body = record.get("body_text") or ""
        sender_seed = sender_email or sender_name
        r, g, b = (
            accent_rgb or record.get("sender_color") or _thread_palette(sender_seed)
        )
        initials = _sender_initials(sender_name, sender_email)
        has_avatar = bool((msg.get("sender_name") or "").strip() or sender_email)
        row_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=0,
            margin_bottom=0,
            margin_start=0,
            margin_end=0,
        )

        strip = Gtk.Box(valign=Gtk.Align.FILL)
        strip.set_size_request(4, 30)
        strip.add_css_class("thread-sidebar-strip")
        row_box.append(strip)

        avatar = Gtk.Label(
            label=initials, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER
        )
        avatar.add_css_class("thread-sidebar-avatar")
        if not has_avatar:
            avatar.add_css_class("generic")
        avatar.set_size_request(28, 28)
        avatar.set_halign(Gtk.Align.CENTER)
        avatar.set_valign(Gtk.Align.CENTER)
        row_box.append(avatar)

        text_col = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True
        )
        sender_lbl = Gtk.Label(label=sender_name, halign=Gtk.Align.START, hexpand=True)
        sender_lbl.set_xalign(0.0)
        sender_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sender_lbl.add_css_class("thread-sidebar-sender")
        text_col.append(sender_lbl)

        snippet = _thread_message_summary(body or (msg.get("snippet") or "").strip())
        snippet_lbl = Gtk.Label(
            label=snippet or "(no content)", halign=Gtk.Align.START, hexpand=True
        )
        snippet_lbl.set_xalign(0.0)
        snippet_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        snippet_lbl.add_css_class("thread-sidebar-snippet")
        text_col.append(snippet_lbl)
        row_box.append(text_col)

        meta_col = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.END
        )
        time_lbl = Gtk.Label(
            label=_format_received_date(msg.get("date"))
            or _format_date(msg.get("date"))
            or "",
            halign=Gtk.Align.END,
        )
        time_lbl.set_xalign(1.0)
        time_lbl.add_css_class("thread-sidebar-time")
        meta_col.append(time_lbl)
        if msg.get("has_attachments"):
            att_lbl = Gtk.Label(label="Attachment", halign=Gtk.Align.END)
            att_lbl.set_xalign(1.0)
            att_lbl.add_css_class("thread-sidebar-time")
            meta_col.append(att_lbl)
        row_box.append(meta_col)

        self.set_child(row_box)
        self._sender_name = sender_name
        self._avatar = avatar
        self._sender_lbl = sender_lbl
        self._strip = strip
        self._on_activate = on_activate
        apply_accent_css_class(self, _rgb_to_hex((r, g, b)))
        self.connect("activate", self._activated)

    def _activated(self, *_):
        if callable(self._on_activate):
            self._on_activate(self.record)


# ── Sidebar rows ──────────────────────────────────────────────────────────────


class UnifiedRow(Gtk.ListBoxRow):
    """Selectable top-level row (All Inboxes, All Trash, All Spam)."""

    def __init__(self, folder_id, name, icon, accent_class=None):
        super().__init__(activatable=False, selectable=True)
        self.folder_id = folder_id
        self.folder_name = name
        self.backend = None
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=8,
            margin_bottom=2,
            margin_start=8,
            margin_end=8,
            spacing=6,
        )
        strip = Gtk.Box(valign=Gtk.Align.CENTER)
        strip.set_size_request(4, 18)
        strip.add_css_class("account-accent-strip")
        box.append(strip)
        box.append(
            Gtk.Image(
                icon_name=_pick_icon_name(
                    icon, "mail-inbox-symbolic", "folder-symbolic"
                ),
                icon_size=Gtk.IconSize.NORMAL,
            )
        )
        if folder_id == "_UNIFIED_":
            self.add_css_class("all-inboxes-row")
        lbl = Gtk.Label(label=name, halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class("account-accent-label")
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class("folder-count")
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_count(self, n, dim=False):
        self.count_label.remove_css_class("folder-count-dim")
        if dim:
            self.count_label.add_css_class("folder-count-dim")
        if n > 0:
            self.count_label.set_label(str(n))
            self.count_label.set_visible(True)
        else:
            self.count_label.set_visible(False)


class FolderRow(Gtk.ListBoxRow):
    def __init__(
        self, folder_id, name, icon, indent=False, accent_class=None, is_last=False
    ):
        super().__init__()
        self.folder_id = folder_id
        self.folder_name = name
        self.count_dim = False
        self.add_css_class("folder-row")
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=0,
            margin_bottom=0,
            margin_start=14 if indent else 14,
            margin_end=12,
            spacing=5,
        )
        box.set_valign(Gtk.Align.FILL)
        if indent:
            connector = Gtk.Box()
            connector.set_size_request(14, -1)
            connector.add_css_class(
                "folder-connector-last" if is_last else "folder-connector"
            )
            box.append(connector)
        name_l = (name or "").lower()
        fallback = "folder-symbolic"
        if "inbox" in name_l or "inbox" in (folder_id or "").lower():
            fallback = "mail-inbox-symbolic"
        elif "sent" in name_l:
            fallback = "mail-send-symbolic"
        elif "draft" in name_l:
            fallback = "document-edit-symbolic"
        elif "trash" in name_l:
            fallback = "user-trash-symbolic"
        elif "spam" in name_l or "junk" in name_l:
            fallback = "mail-mark-junk-symbolic"
        box.append(
            Gtk.Image(
                icon_name=_pick_icon_name(icon, fallback, "folder-symbolic"),
                icon_size=Gtk.IconSize.NORMAL,
            )
        )
        lbl = Gtk.Label(label=name, halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class("account-accent-label")
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self._label = lbl

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class("folder-count")
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_count(self, n, dim=False):
        self.count_dim = dim
        self.count_label.remove_css_class("folder-count-dim")
        if dim:
            self.count_label.add_css_class("folder-count-dim")
        if n > 0:
            self.count_label.set_label(str(n))
            self.count_label.set_visible(True)
        else:
            self.count_label.set_visible(False)


class AccountHeaderRow(Gtk.ListBoxRow):
    def __init__(self, identity, accent_class=None):
        super().__init__(activatable=True, selectable=False)
        self.identity = identity
        self.expanded = False
        self.add_css_class("account-section-header")
        self.add_css_class("account-header-row")
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=0,
            margin_bottom=0,
            margin_start=8,
            margin_end=8,
            spacing=5,
        )
        box.set_valign(Gtk.Align.FILL)
        self.chevron = Gtk.Image(icon_name="pan-end-symbolic")
        box.append(self.chevron)

        lbl = Gtk.Label(
            label=identity,
            halign=Gtk.Align.START,
            hexpand=True,
        )
        lbl.add_css_class("account-header")
        lbl.add_css_class("account-accent-label")
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self._label = lbl

        self._health_icon = Gtk.Image(
            icon_name="dialog-warning-symbolic", pixel_size=12
        )
        self._health_icon.add_css_class("account-health-icon")
        self._health_icon.set_visible(False)
        box.append(self._health_icon)

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class("folder-count")
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_label(self, text):
        self._label.set_label(text or "")

    def set_count(self, n, dim=False):
        self.count_label.remove_css_class("folder-count-dim")
        if dim:
            self.count_label.add_css_class("folder-count-dim")
        if n > 0:
            self.count_label.set_label(str(n))
            self.count_label.set_visible(True)
        else:
            self.count_label.set_visible(False)

    def set_health(self, state=None, detail="", tooltip=""):
        state = str(state or "").strip().lower()
        self._health_icon.remove_css_class("state-warning")
        self._health_icon.remove_css_class("state-error")
        if state not in {"warning", "error"}:
            self._health_icon.set_visible(False)
            self._health_icon.set_tooltip_text("")
            return
        icon_name = (
            "dialog-error-symbolic" if state == "error" else "dialog-warning-symbolic"
        )
        self._health_icon.add_css_class(f"state-{state}")
        self._health_icon.set_from_icon_name(icon_name)
        self._health_icon.set_visible(True)
        self._health_icon.set_tooltip_text(str(tooltip or detail or ""))


class MoreFoldersRow(Gtk.ListBoxRow):
    def __init__(self, accent_class=None):
        super().__init__(activatable=True, selectable=False)
        self.loaded = False
        self.expanded = False
        self.add_css_class("folder-row")
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=0,
            margin_bottom=0,
            margin_start=16,
            margin_end=12,
            spacing=5,
        )
        box.set_valign(Gtk.Align.FILL)
        connector = Gtk.Box()
        connector.set_size_request(14, -1)
        connector.add_css_class("folder-connector-last")
        box.append(connector)
        self._connector = connector
        self.chevron = Gtk.Image(icon_name="pan-end-symbolic")
        box.append(self.chevron)
        lbl = Gtk.Label(label="More folders", halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class("account-accent-label")
        lbl.add_css_class("more-folders-label")
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self.spinner = Gtk.Spinner()
        box.append(self.spinner)
        self.set_child(box)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self._connector.remove_css_class("folder-connector")
        self._connector.remove_css_class("folder-connector-last")
        self._connector.add_css_class(
            "folder-connector" if self.expanded else "folder-connector-last"
        )
        self.chevron.set_from_icon_name(
            "pan-down-symbolic" if self.expanded else "pan-end-symbolic"
        )


class StartupStatusRow(Gtk.ListBoxRow):
    def __init__(self, identity, accent_class=None):
        super().__init__()
        self.identity = identity
        self.state = "pending"
        self.add_css_class("startup-status-row")
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        box.set_valign(Gtk.Align.CENTER)

        strip = Gtk.Box(valign=Gtk.Align.FILL)
        strip.set_size_request(4, 38)
        strip.add_css_class("startup-status-strip")
        box.append(strip)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )
        title = Gtk.Label(label=identity, halign=Gtk.Align.START, hexpand=True)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.add_css_class("startup-status-title")
        text_box.append(title)

        detail = Gtk.Label(label="Waiting", halign=Gtk.Align.START, hexpand=True)
        detail.set_xalign(0.0)
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.add_css_class("startup-status-detail")
        text_box.append(detail)

        box.append(text_box)

        indicator = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER
        )
        indicator.add_css_class("startup-status-indicator")
        icon = Gtk.Image(icon_name="emblem-ok-symbolic")
        icon.set_visible(False)
        indicator.append(icon)
        box.append(indicator)

        self._title = title
        self._detail = detail
        self._icon = icon
        self._strip = strip
        self.set_child(box)
        self.set_state("pending")

    def set_state(self, state, detail=""):
        state = (state or "pending").strip().lower()
        self.state = state
        self.remove_css_class("state-pending")
        self.remove_css_class("state-checking")
        self.remove_css_class("state-ready")
        self.remove_css_class("state-warning")
        self.remove_css_class("state-error")
        self.add_css_class(f"state-{state}")
        detail_text = detail or {
            "pending": "Preparing",
            "checking": "Checking mail",
            "ready": "Ready",
            "warning": "Using fallback",
            "error": "Sync issue",
        }.get(state, "Ready")
        self._detail.set_label(detail_text)
        if state in {"pending", "checking"}:
            self._icon.set_visible(False)
        elif state == "warning":
            self._icon.set_from_icon_name("dialog-warning-symbolic")
            self._icon.set_visible(True)
        elif state == "error":
            self._icon.set_from_icon_name("dialog-warning-symbolic")
            self._icon.set_visible(True)
        else:
            self._icon.set_from_icon_name("emblem-ok-symbolic")
            self._icon.set_visible(True)


class StartupIssueRow(Gtk.Box):
    def __init__(self, identity, detail, state="warning", accent_class=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.identity = identity
        self.state = state
        self.add_css_class("startup-status-issue-row")
        self.add_css_class(f"state-{(state or 'warning').strip().lower()}")
        if accent_class:
            self.add_css_class(accent_class)

        strip = Gtk.Box(valign=Gtk.Align.FILL)
        strip.set_size_request(4, 24)
        strip.add_css_class("startup-status-strip")
        strip.add_css_class(f"state-{(state or 'warning').strip().lower()}")
        self.append(strip)

        icon_name = (
            "dialog-warning-symbolic" if state == "warning" else "dialog-error-symbolic"
        )
        icon = Gtk.Image(icon_name=icon_name, pixel_size=16)
        icon.add_css_class("startup-status-issue-icon")
        self.append(icon)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True
        )
        title = Gtk.Label(label=identity, halign=Gtk.Align.START, hexpand=True)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.add_css_class("startup-status-issue-title")
        detail_lbl = Gtk.Label(label=detail, halign=Gtk.Align.START, hexpand=True)
        detail_lbl.set_xalign(0.0)
        detail_lbl.set_wrap(True)
        detail_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail_lbl.add_css_class("startup-status-issue-detail")
        text_box.append(title)
        text_box.append(detail_lbl)
        self.append(text_box)
        self._title = title
        self._detail = detail_lbl
        self._icon = icon

    def set_detail(self, detail):
        self._detail.set_label(detail or "")


class StartupStatusPanel(Gtk.Box):
    def __init__(self, backends=None, accent_for_identity=None, on_close=None):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True
        )
        self.add_css_class("startup-status-panel")
        self._accent_for_identity = accent_for_identity or (lambda identity: None)
        self._on_close = on_close
        self._rows = {}
        self._issues = {}
        self._issue_rows = {}
        self._backend_order = []
        self._total = 0
        shell = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            hexpand=True,
            vexpand=True,
            margin_top=24,
            margin_bottom=24,
            margin_start=26,
            margin_end=26,
        )
        shell.set_valign(Gtk.Align.CENTER)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        card.add_css_class("startup-status-card")
        shell.append(card)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18, hexpand=True)
        hero.add_css_class("startup-status-hero")

        orb = Gtk.Overlay()
        orb.set_size_request(78, 78)
        orb_shell = Gtk.Box()
        orb_shell.add_css_class("startup-status-orb")
        orb_shell.set_hexpand(True)
        orb_shell.set_vexpand(True)
        orb_icon = Gtk.Image(icon_name="mail-send-receive-symbolic", pixel_size=28)
        orb.set_child(orb_shell)
        orb.add_overlay(orb_icon)
        orb_icon.set_halign(Gtk.Align.CENTER)
        orb_icon.set_valign(Gtk.Align.CENTER)
        hero.append(orb)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True
        )
        title = Gtk.Label(
            label="Hermod is waking your mail", halign=Gtk.Align.START, hexpand=True
        )
        title.set_xalign(0.0)
        title.add_css_class("startup-status-heading")
        title.set_wrap(False)
        self._title = title

        subtitle = Gtk.Label(
            label="Loading mail, refreshing counts, and restoring the first view.",
            halign=Gtk.Align.START,
            hexpand=True,
        )
        subtitle.set_xalign(0.0)
        subtitle.set_wrap(True)
        subtitle.add_css_class("startup-status-subtitle")
        self._subtitle = subtitle

        mood = Gtk.Label(
            label="Just a moment while accounts come online.",
            halign=Gtk.Align.START,
            hexpand=True,
        )
        mood.set_xalign(0.0)
        mood.add_css_class("startup-status-mood")
        self._mood = mood

        text_box.append(title)
        text_box.append(subtitle)
        text_box.append(mood)
        hero.append(text_box)

        summary = Gtk.Label(label="", halign=Gtk.Align.END, hexpand=False)
        summary.add_css_class("startup-status-summary")
        self._summary = summary
        hero.append(summary)

        card.append(hero)

        progress = Gtk.ProgressBar()
        progress.set_show_text(False)
        progress.add_css_class("startup-status-progress")
        self._progress = progress
        card.append(progress)

        self._list = Gtk.ListBox()
        self._list.add_css_class("startup-status-list")
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        card.append(self._list)

        issue_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        issue_wrap.set_visible(False)
        issue_wrap.add_css_class("startup-status-issues")
        issue_title = Gtk.Label(label="Issues", halign=Gtk.Align.START)
        issue_title.set_xalign(0.0)
        issue_title.add_css_class("startup-status-issues-title")
        issue_wrap.append(issue_title)
        issue_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        issue_wrap.append(issue_list)
        self._issue_wrap = issue_wrap
        self._issue_list = issue_list
        card.append(issue_wrap)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        action_row.set_halign(Gtk.Align.END)
        close_button = Gtk.Button(label="Close")
        close_button.add_css_class("startup-status-close")
        close_button.set_visible(False)
        close_button.connect("clicked", self._on_close_clicked)
        action_row.append(close_button)
        self._close_button = close_button
        card.append(action_row)

        self.append(shell)
        self.set_backends(backends or [])

    def _on_close_clicked(self, *_args):
        if callable(self._on_close):
            self._on_close()

    def set_backends(self, backends):
        while row := self._list.get_row_at_index(0):
            self._list.remove(row)
        self._rows.clear()
        self._issues.clear()
        self._backend_order = []
        for backend in backends or []:
            identity = (
                getattr(backend, "presentation_name", "")
                or getattr(backend, "identity", "")
                or "Account"
            )
            row = StartupStatusRow(
                identity,
                accent_class=self._accent_for_identity(
                    getattr(backend, "identity", "")
                ),
            )
            backend_identity = getattr(backend, "identity", identity)
            self._rows[backend_identity] = row
            self._backend_order.append(backend_identity)
            self._list.append(row)
        self._total = len(self._rows)
        self._render_issues()
        self._update_summary()

    def set_title(self, title, subtitle=None):
        self._title.set_label(title or "Hermod is waking your mail")
        if subtitle is not None:
            self._subtitle.set_label(subtitle)

    def set_account_state(self, identity, state, detail=""):
        row = self._rows.get(identity)
        if row is None:
            return
        row.set_state(state, detail)
        state = str(state or "pending").strip().lower()
        detail = str(detail or "").strip()
        if state in {"warning", "error"}:
            self._issues[identity] = {
                "state": state,
                "detail": detail
                or ("Sync issue" if state == "error" else "Using fallback"),
                "title": row.identity,
            }
        else:
            self._issues.pop(identity, None)
        self._render_issues()
        self._update_summary()

    def has_attention(self):
        return bool(self._issues)

    def has_blocking_attention(self):
        return any(issue.get("state") == "error" for issue in self._issues.values())

    def set_all_pending(
        self, detail="Preparing accounts and restoring the mailbox snapshot."
    ):
        for row in self._rows.values():
            row.set_state("pending")
        self._subtitle.set_label(detail)
        self._update_summary()

    def _update_summary(self):
        total = max(1, self._total)
        ready = sum(1 for row in self._rows.values() if row.state == "ready")
        checking = sum(1 for row in self._rows.values() if row.state == "checking")
        warnings = sum(1 for row in self._rows.values() if row.state == "warning")
        errors = sum(1 for row in self._rows.values() if row.state == "error")
        pending = sum(
            1 for row in self._rows.values() if row.state in {"pending", "checking"}
        )
        completed = ready + warnings + errors
        self._progress.set_fraction(completed / total if self._total else 0.0)
        if self._mood is not None:
            if errors:
                mood = "One account needs help."
            elif warnings:
                mood = "One account is using fallback."
            elif checking:
                mood = f"Checking {checking} account{'s' if checking != 1 else ''}."
            elif completed == self._total and self._total:
                mood = "All accounts are ready."
            elif ready:
                mood = "Finishing startup."
            else:
                mood = "Starting up."
            self._mood.set_label(mood)
        if self._total:
            self._close_button.set_visible(self.has_attention())
            if completed == self._total and not self.has_blocking_attention():
                summary = f"{ready}/{self._total} ready"
            else:
                summary = f"{completed}/{self._total} checked"
            if checking:
                summary += f"  {checking} checking"
            if warnings:
                summary += f"  {warnings} fallback"
            if errors:
                summary += f"  {errors} attention"
            if pending:
                summary += f"  {pending} pending"
            self._summary.set_label(summary)
        else:
            self._close_button.set_visible(False)
            self._summary.set_label("Preparing accounts")

    def _render_issues(self):
        while row := self._issue_list.get_first_child():
            self._issue_list.remove(row)
        ordered = [
            identity for identity in self._backend_order if identity in self._issues
        ]
        for identity in ordered:
            issue = self._issues[identity]
            row = StartupIssueRow(
                issue.get("title") or identity,
                issue.get("detail") or "",
                state=issue.get("state", "warning"),
                accent_class=self._accent_for_identity(identity),
            )
            self._issue_list.append(row)
        self._issue_wrap.set_visible(bool(ordered))

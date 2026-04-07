"""GTK row widget classes for the Lark inbox and sidebar."""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Pango, GObject

try:
    from .utils import (
        _format_date, _format_received_date,
        _pick_icon_name, _make_count_slot,
        _sender_initials, _thread_palette, _thread_message_summary,
    )
except ImportError:
    from utils import (
        _format_date, _format_received_date,
        _pick_icon_name, _make_count_slot,
        _sender_initials, _thread_palette, _thread_message_summary,
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
        super().__init__('message')
        self.msg = msg
        self.accent_class = accent_class

    def set_thread_count(self, count):
        self.msg['thread_count'] = count
        if self.widget is not None:
            self.widget.set_thread_count(count)

    def mark_read(self):
        self.msg['is_read'] = True
        if self.widget is not None:
            self.widget.mark_read()

    def mark_unread(self):
        self.msg['is_read'] = False
        if self.widget is not None:
            self.widget.mark_unread()

class LoadMoreListItem(MailListItem):
    def __init__(self, label='Load more'):
        super().__init__('load_more')
        self.label = label


class EmailRow(Gtk.Box):
    def __init__(self, msg, on_reply, on_reply_all, on_delete, accent_class=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.msg = msg
        self._hovering = False
        self._selected = False
        self.add_css_class('email-row')
        if accent_class:
            self.add_css_class(accent_class)

        overlay = Gtk.Overlay()

        outer = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=10, margin_bottom=10,
            margin_start=12, margin_end=12,
            spacing=10,
        )

        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.set_size_request(8, 8)
        dot.add_css_class('unread-dot')
        self._dot = dot
        if msg.get('is_read'):
            dot.set_opacity(0)
        outer.append(dot)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sender = Gtk.Label(
            label=msg.get('sender_name', ''),
            halign=Gtk.Align.START,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=28,
        )
        self._sender_label = sender
        row1.append(sender)

        if msg.get('thread_count', 1) > 1:
            thread_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.CENTER)
            thread_box.add_css_class('thread-indicator')
            thread_icon = Gtk.Image(
                icon_name=_pick_icon_name(
                    'chat-bubbles-symbolic',
                    'mail-message-new-symbolic',
                    'chat-symbolic',
                    'mail-reply-sender-symbolic',
                ),
                pixel_size=13,
            )
            thread_box.append(thread_icon)
            thread_lbl = Gtk.Label(label=str(msg.get('thread_count', 1)))
            thread_lbl.add_css_class('thread-badge')
            thread_lbl.add_css_class('thread-badge-threaded')
            thread_lbl.set_visible(msg.get('thread_count', 1) > 1)
            thread_box.append(thread_lbl)
            self._thread_box = thread_box
            self._thread_label = thread_lbl
            row1.append(thread_box)
        else:
            self._thread_box = None
            self._thread_label = None

        if msg.get('has_attachments'):
            clip = Gtk.Image(icon_name=_pick_icon_name('mail-attachment-symbolic', 'paperclip-symbolic'), pixel_size=14)
            clip.add_css_class('dim-label')
            clip.set_margin_end(4)
            row1.append(clip)

        date_lbl = Gtk.Label(
            label=_format_date(msg.get('date')),
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
        )
        date_lbl.add_css_class('caption')
        date_lbl.add_css_class('dim-label')
        self._date_label = date_lbl
        row1.append(date_lbl)
        col.append(row1)

        subj = Gtk.Label(
            label=msg.get('subject', ''),
            halign=Gtk.Align.START,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=50,
        )
        subj.add_css_class('caption')
        self._subject_label = subj
        self._apply_unread_style()
        col.append(subj)

        outer.append(col)
        overlay.set_child(outer)

        sender_email = (msg.get('sender_email') or '').strip()
        if sender_email:
            self.set_tooltip_text(sender_email)

        action_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            spacing=2,
        )
        action_box.add_css_class('email-actions')
        action_box.set_visible(False)

        for icon, tip, cb in [
            ('mail-reply-sender-symbolic',  'Reply (r)',     lambda _: on_reply(msg)),
            ('mail-reply-all-symbolic',     'Reply All (a)', lambda _: on_reply_all(msg)),
            ('user-trash-symbolic',         'Delete (d)',    lambda _: on_delete(self, msg)),
        ]:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, has_frame=False)
            btn.add_css_class('flat')
            btn.connect('clicked', cb)
            action_box.append(btn)

        overlay.add_overlay(action_box)
        self._action_box = action_box

        motion = Gtk.EventControllerMotion()
        motion.connect('enter', self._on_hover_enter)
        motion.connect('leave', self._on_hover_leave)
        self.add_controller(motion)

        self.append(overlay)
        self._sync_action_visibility()

    def set_selected(self, selected):
        self._selected = bool(selected)
        if self._selected:
            self.add_css_class('selected')
        else:
            self.remove_css_class('selected')
        self._sync_action_visibility()

    def set_thread_count(self, count):
        self.msg['thread_count'] = count
        if getattr(self, '_thread_label', None) is not None:
            self._thread_label.set_label(str(count))
            self._thread_label.set_visible(count > 1)
            self._thread_box.set_visible(bool(self.msg.get('thread_id')) or count > 1)

    def _on_hover_enter(self, *_):
        self._hovering = True
        self._sync_action_visibility()

    def _on_hover_leave(self, *_):
        self._hovering = False
        self._sync_action_visibility()

    def _sync_action_visibility(self):
        self._action_box.set_visible(self._hovering or self._selected)

    def mark_read(self):
        self.msg['is_read'] = True
        self._dot.set_opacity(0)
        self._apply_unread_style()

    def mark_unread(self):
        self.msg['is_read'] = False
        self._dot.set_opacity(1)
        self._apply_unread_style()

    def _apply_unread_style(self):
        if self.msg.get('is_read', True):
            self._sender_label.remove_css_class('heading')
            self._subject_label.remove_css_class('heading')
        else:
            self._sender_label.remove_css_class('heading')
            self._subject_label.add_css_class('heading')


class LoadMoreRow(Gtk.Box):
    def __init__(self, label, on_activate):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self._selected = False
        self.add_css_class('load-more-row')
        self.set_margin_top(8)
        self.set_margin_bottom(14)
        self.set_margin_start(12)
        self.set_margin_end(12)

        button = Gtk.Button(label=label)
        button.add_css_class('flat')
        button.connect('clicked', lambda *_: on_activate())
        self._button = button
        self.append(button)

    def set_selected(self, selected):
        self._selected = bool(selected)
        if self._selected:
            self.add_css_class('selected')
        else:
            self.remove_css_class('selected')


# ── Thread sidebar row ────────────────────────────────────────────────────────

class ThreadNavRow(Gtk.ListBoxRow):
    def __init__(self, record, on_activate, accent_rgb=None):
        super().__init__()
        self.record = record
        self.msg = record.get('msg') or {}
        self.uid = self.msg.get('uid', '')
        self.add_css_class('thread-sidebar-row')

        msg = self.msg
        sender_name = (msg.get('sender_name') or msg.get('sender_email') or 'Unknown sender').strip()
        sender_email = (msg.get('sender_email') or '').strip()
        body = record.get('body_text') or ''
        sender_seed = sender_email or sender_name
        r, g, b = accent_rgb or record.get('sender_color') or _thread_palette(sender_seed)
        initials = _sender_initials(sender_name, sender_email)
        has_avatar = bool((msg.get('sender_name') or '').strip() or sender_email)
        row_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=0,
            margin_bottom=0,
            margin_start=0,
            margin_end=0,
        )

        strip = Gtk.Box(valign=Gtk.Align.FILL)
        strip.set_size_request(4, 34)
        strip.add_css_class('thread-sidebar-strip')
        row_box.append(strip)

        avatar = Gtk.Label(label=initials, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        avatar.add_css_class('thread-sidebar-avatar')
        if not has_avatar:
            avatar.add_css_class('generic')
        avatar.set_size_request(30, 30)
        avatar.set_halign(Gtk.Align.CENTER)
        avatar.set_valign(Gtk.Align.CENTER)
        row_box.append(avatar)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        sender_lbl = Gtk.Label(label=sender_name, halign=Gtk.Align.START, hexpand=True)
        sender_lbl.set_xalign(0.0)
        sender_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sender_lbl.add_css_class('thread-sidebar-sender')
        text_col.append(sender_lbl)

        snippet = _thread_message_summary(body or (msg.get('snippet') or '').strip())
        snippet_lbl = Gtk.Label(label=snippet or '(no content)', halign=Gtk.Align.START, hexpand=True)
        snippet_lbl.set_xalign(0.0)
        snippet_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        snippet_lbl.add_css_class('thread-sidebar-snippet')
        text_col.append(snippet_lbl)
        row_box.append(text_col)

        meta_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.END)
        time_lbl = Gtk.Label(label=_format_received_date(msg.get('date')) or _format_date(msg.get('date')) or '', halign=Gtk.Align.END)
        time_lbl.set_xalign(1.0)
        time_lbl.add_css_class('thread-sidebar-time')
        meta_col.append(time_lbl)
        if msg.get('has_attachments'):
            att_lbl = Gtk.Label(label='Attachment', halign=Gtk.Align.END)
            att_lbl.set_xalign(1.0)
            att_lbl.add_css_class('thread-sidebar-time')
            meta_col.append(att_lbl)
        row_box.append(meta_col)

        self.set_child(row_box)
        self._sender_name = sender_name
        self._avatar = avatar
        self._sender_lbl = sender_lbl
        self._strip = strip
        self._on_activate = on_activate
        self._set_accent_color(r, g, b)
        self.connect('activate', self._activated)

    def _set_accent_color(self, r, g, b):
        avatar_name = f'thread-sidebar-avatar-{self.uid or id(self)}'
        strip_name = f'thread-sidebar-strip-{self.uid or id(self)}'
        sender_name = f'thread-sidebar-sender-{self.uid or id(self)}'
        try:
            self._avatar.set_name(avatar_name)
        except Exception:
            pass
        try:
            self._strip.set_name(strip_name)
        except Exception:
            pass
        try:
            self._sender_lbl.set_name(sender_name)
        except Exception:
            pass
        avatar_provider = Gtk.CssProvider()
        avatar_provider.load_from_string(f'#{avatar_name} {{ background-color: rgb({r}, {g}, {b}); }}')
        strip_provider = Gtk.CssProvider()
        strip_provider.load_from_string(f'#{strip_name} {{ background-color: rgb({r}, {g}, {b}); }}')
        sender_provider = Gtk.CssProvider()
        sender_provider.load_from_string(f'#{sender_name} {{ color: rgb({r}, {g}, {b}); }}')
        self._avatar.get_style_context().add_provider(avatar_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._strip.get_style_context().add_provider(strip_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._sender_lbl.get_style_context().add_provider(sender_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._avatar_provider = avatar_provider
        self._strip_provider = strip_provider
        self._sender_provider = sender_provider

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
            margin_top=10, margin_bottom=4,
            margin_start=8, margin_end=8,
            spacing=8,
        )
        strip = Gtk.Box(valign=Gtk.Align.CENTER)
        strip.set_size_request(4, 18)
        strip.add_css_class('account-accent-strip')
        box.append(strip)
        box.append(Gtk.Image(icon_name=_pick_icon_name(icon, 'mail-inbox-symbolic', 'folder-symbolic'), icon_size=Gtk.IconSize.NORMAL))
        lbl = Gtk.Label(label=name, halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class('account-accent-label')
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class('folder-count')
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_count(self, n, dim=False):
        self.count_label.remove_css_class('folder-count-dim')
        if dim:
            self.count_label.add_css_class('folder-count-dim')
        if n > 0:
            self.count_label.set_label(str(n))
            self.count_label.set_visible(True)
        else:
            self.count_label.set_visible(False)


class FolderRow(Gtk.ListBoxRow):
    def __init__(self, folder_id, name, icon, indent=False, accent_class=None):
        super().__init__()
        self.folder_id = folder_id
        self.folder_name = name
        self.count_dim = False
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=5, margin_bottom=5,
            margin_start=32 if indent else 16,
            margin_end=12,
            spacing=10,
        )
        strip = Gtk.Box(valign=Gtk.Align.CENTER)
        strip.set_size_request(3, 16)
        strip.add_css_class('account-accent-strip')
        box.append(strip)
        name_l = (name or '').lower()
        fallback = 'folder-symbolic'
        if 'inbox' in name_l or 'inbox' in (folder_id or '').lower():
            fallback = 'mail-inbox-symbolic'
        elif 'sent' in name_l:
            fallback = 'mail-send-symbolic'
        elif 'draft' in name_l:
            fallback = 'document-edit-symbolic'
        elif 'trash' in name_l:
            fallback = 'user-trash-symbolic'
        elif 'spam' in name_l or 'junk' in name_l:
            fallback = 'mail-mark-junk-symbolic'
        box.append(Gtk.Image(icon_name=_pick_icon_name(icon, fallback, 'folder-symbolic'), icon_size=Gtk.IconSize.NORMAL))
        lbl = Gtk.Label(label=name, halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class('account-accent-label')
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self._label = lbl

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class('folder-count')
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_count(self, n, dim=False):
        self.count_dim = dim
        self.count_label.remove_css_class('folder-count-dim')
        if dim:
            self.count_label.add_css_class('folder-count-dim')
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
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=10, margin_bottom=4,
            margin_start=8, margin_end=8,
            spacing=6,
        )
        strip = Gtk.Box(valign=Gtk.Align.CENTER)
        strip.set_size_request(4, 18)
        strip.add_css_class('account-accent-strip')
        box.append(strip)
        self.chevron = Gtk.Image(icon_name='pan-end-symbolic')
        box.append(self.chevron)

        lbl = Gtk.Label(
            label=identity,
            halign=Gtk.Align.START,
            hexpand=True,
        )
        lbl.add_css_class('account-header')
        lbl.add_css_class('account-accent-label')
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self._label = lbl

        count_slot = _make_count_slot()
        self.count_label = Gtk.Label()
        self.count_label.add_css_class('folder-count')
        self.count_label.set_visible(False)
        count_slot.append(self.count_label)
        box.append(count_slot)
        self.set_child(box)

    def set_count(self, n, dim=False):
        self.count_label.remove_css_class('folder-count-dim')
        if dim:
            self.count_label.add_css_class('folder-count-dim')
        if n > 0:
            self.count_label.set_label(str(n))
            self.count_label.set_visible(True)
        else:
            self.count_label.set_visible(False)


class MoreFoldersRow(Gtk.ListBoxRow):
    def __init__(self, accent_class=None):
        super().__init__(activatable=True, selectable=False)
        self.loaded = False
        self.expanded = False
        if accent_class:
            self.add_css_class(accent_class)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=4, margin_bottom=4,
            margin_start=32, margin_end=12,
            spacing=8,
        )
        strip = Gtk.Box(valign=Gtk.Align.CENTER)
        strip.set_size_request(3, 16)
        strip.add_css_class('account-accent-strip')
        box.append(strip)
        self.chevron = Gtk.Image(icon_name='pan-end-symbolic')
        box.append(self.chevron)
        lbl = Gtk.Label(label='More folders', halign=Gtk.Align.START, hexpand=True)
        lbl.add_css_class('account-accent-label')
        lbl.add_css_class('more-folders-label')
        lbl.set_xalign(0.0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        self.spinner = Gtk.Spinner()
        box.append(self.spinner)
        self.set_child(box)

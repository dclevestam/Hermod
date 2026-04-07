import threading
from pathlib import Path
from datetime import datetime
from html import escape as _html_escape
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk
try:
    from .styles import build_compose_account_css, account_class_for_index
except ImportError:
    from styles import build_compose_account_css, account_class_for_index


_CONFIG_DIR = Path.home() / '.config' / 'lark'
_DRAFTS_DIR = _CONFIG_DIR / 'drafts'

DEFAULT_FONT_SIZE_POINTS = 14
FONT_SIZE_OPTIONS = [12, 14, 16, 18, 24]

COMPOSE_CSS = """
.compose-shell {
    background-color: alpha(@window_fg_color, 0.02);
    border-radius: 14px 14px 14px 14px;
}
.compose-header-meta {
    padding: 8px 16px 7px;
    border-bottom: 1px solid alpha(@borders, 0.22);
    background: alpha(@window_bg_color, 0.55);
}
.compose-header-summary {
    font-size: 0.84em;
    line-height: 1.0;
    color: alpha(@window_fg_color, 0.84);
    min-height: 18px;
    padding-top: 0px;
    padding-bottom: 0px;
}
.compose-fields {
    padding: 12px 16px 8px;
}
.compose-field {
    margin-bottom: 6px;
}
.compose-field-label {
    font-size: 0.70em;
    letter-spacing: 0.045em;
    color: alpha(@window_fg_color, 0.54);
    margin-bottom: 3px;
}
.compose-field-line {
    min-height: 30px;
}
.compose-row-entry {
    padding: 5px 9px;
    min-height: 30px;
}
.compose-from-surface {
    background: transparent;
    border: none;
    padding: 0;
}
.compose-from-inline-label {
    min-width: 84px;
    font-size: 0.82em;
    letter-spacing: 0.04em;
    color: alpha(@window_fg_color, 0.56);
}
.compose-from-value {
    font-size: 0.9em;
    color: alpha(@window_fg_color, 0.92);
    font-weight: 400;
}
.compose-operator-bar {
    padding: 5px 10px 6px;
    background: alpha(@window_bg_color, 0.98);
    border-top: 1px solid alpha(@borders, 0.18);
}
.compose-style-dropdown {
    min-width: 86px;
}
.compose-color-btn {
    min-width: 30px;
}
.compose-tool-fallback {
    font-size: 1.05em;
    font-weight: 600;
}
.compose-send-btn {
    min-height: 30px;
    min-width: 94px;
    font-weight: 700;
    padding: 0px 12px;
}
.compose-body-shell {
    background-color: alpha(@window_bg_color, 0.92);
}
.compose-bcc-toggle {
    min-height: 24px;
}
.compose-from-button {
    min-height: 30px;
    padding: 0;
    min-width: 254px;
}
.compose-from-button > box {
    min-height: 30px;
}
.compose-operator-action {
    min-height: 30px;
    min-width: 30px;
    border-radius: 7px;
    padding: 0;
    background: alpha(@window_fg_color, 0.04);
    color: alpha(@window_fg_color, 0.88);
}
.compose-operator-action:hover {
    background: alpha(@window_fg_color, 0.08);
}
.compose-discard {
    background: rgba(229, 57, 53, 0.10);
    color: rgba(244, 132, 126, 0.98);
}
.compose-discard:hover {
    background: rgba(229, 57, 53, 0.18);
}
.compose-draft {
    background: rgba(241, 196, 15, 0.10);
    color: rgba(246, 224, 110, 0.98);
}
.compose-draft:hover {
    background: rgba(241, 196, 15, 0.18);
}
.compose-attach-strip {
    padding: 4px 12px;
    min-height: 32px;
}
.compose-attach-chip {
    border-radius: 6px;
    padding: 2px 6px;
    background: alpha(@window_fg_color, 0.06);
    font-size: 0.85em;
}
.compose-attach-chip-remove {
    min-width: 18px;
    min-height: 18px;
    padding: 0;
    border-radius: 4px;
    background: none;
    border: none;
}
"""

def _rgba_to_hex(rgba):
    if rgba is None:
        return '#000000'
    return '#{0:02x}{1:02x}{2:02x}'.format(
        round(max(0.0, min(1.0, rgba.red)) * 255),
        round(max(0.0, min(1.0, rgba.green)) * 255),
        round(max(0.0, min(1.0, rgba.blue)) * 255),
    )


class ComposeView(Gtk.Box):
    def __init__(self, parent, backend, backends=None, reply_to=None, reply_all=False, on_close=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        self._parent = parent
        self._on_close = on_close
        self._backends = backends or [backend]
        self._selected_backend_index = next((i for i, b in enumerate(self._backends) if b is backend), 0)
        self._selected_backend_class = account_class_for_index(self._selected_backend_index)
        self._contact_timer = None
        self._contact_fetch_gen = 0
        self._attachments = []
        self._buffer = None
        self._tag_bold = None
        self._tag_italic = None
        self._tag_quote = None
        self._color_tags = {}
        self._size_tags = {}
        self._bold_active = False
        self._italic_active = False
        self._active_color = None
        self._active_font_size = None
        self._inserting_rich_text = False
        self._last_selection = None
        self._dirty = False
        self._snapshot_cache = None
        self._dirty_check_id = None
        self._reply_to_msg = reply_to
        self._apply_css()

        is_reply = reply_to is not None
        self._title = 'Reply' if is_reply else 'New Message'

        toolbar = Adw.ToolbarView()
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        shell.add_css_class('compose-shell')

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        meta.add_css_class('compose-header-meta')
        self._summary_label = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._summary_label.set_wrap(False)
        self._summary_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._summary_label.add_css_class('compose-header-summary')
        meta.append(self._summary_label)

        fields = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        fields.add_css_class('compose-fields')

        self.to_entry = Gtk.Entry(hexpand=True)
        self.to_entry.add_css_class('compose-row-entry')
        self.to_entry.connect('changed', self._on_to_changed)
        fields.append(self._make_entry_field('To:', self.to_entry))

        self.subject_entry = Gtk.Entry(hexpand=True)
        self.subject_entry.add_css_class('compose-row-entry')
        fields.append(self._make_entry_field('Subject:', self.subject_entry))
        self.cc_entry = None

        # Contacts popover
        self._contact_list_box = Gtk.ListBox()
        self._contact_list_box.add_css_class('boxed-list')
        self._contact_list_box.connect('row-activated', self._on_contact_selected)
        pop_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            max_content_height=200,
            propagate_natural_height=True,
            min_content_width=300,
        )
        pop_scroll.set_child(self._contact_list_box)
        self._contact_popover = Gtk.Popover()
        self._contact_popover.set_child(pop_scroll)
        self._contact_popover.set_parent(self.to_entry)
        self._contact_popover.set_position(Gtk.PositionType.BOTTOM)
        self._contact_popover.set_has_arrow(False)
        self._contact_popover.set_autohide(True)

        self._format_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for icon_names, tip, cb, fallback in [
            (['format-text-bold-symbolic'], 'Bold', self._on_bold_clicked, 'B'),
            (['format-text-italic-symbolic'], 'Italic', self._on_italic_clicked, 'I'),
            (['format-quote-symbolic', 'insert-text-symbolic'], 'Quote', self._on_quote_clicked, '❝'),
            (['format-list-bulleted-symbolic', 'view-list-symbolic'], 'List', self._on_list_clicked, '•'),
        ]:
            if tip in ('Bold', 'Italic'):
                self._format_box.append(self._make_toggle_tool_button(icon_names, tip, cb, fallback))
            else:
                self._format_box.append(self._make_tool_button(icon_names, tip, cb, fallback))
        self._format_box.append(self._build_style_controls())

        self.body = Gtk.TextView(
            vexpand=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=16, right_margin=16,
            top_margin=12, bottom_margin=12,
        )
        self.body.add_css_class('body')
        self._buffer = self.body.get_buffer()
        self._setup_editor_tags()
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.body)
        scroll.add_css_class('compose-body-shell')

        self._operator_bar = self._build_operator_bar(backend)

        self._attach_strip = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            visible=False,
        )
        self._attach_strip.add_css_class('compose-attach-strip')

        shell.append(meta)
        shell.append(fields)
        shell.append(self._format_box)
        shell.append(scroll)
        shell.append(self._attach_strip)
        shell.append(self._operator_bar)
        toolbar.set_content(shell)
        self.append(toolbar)

        # Pre-fill for reply / reply-all
        if is_reply:
            own_email = self._get_selected_backend().identity
            sender = reply_to.get('sender_email', '')

            if reply_all:
                to_set = {sender}
                to_set.update(
                    a['email'] for a in reply_to.get('to_addrs', [])
                    if a['email'].lower() != own_email.lower()
                )
                self.to_entry.set_text(', '.join(to_set))
                cc_emails = ', '.join(
                    a['email'] for a in reply_to.get('cc_addrs', [])
                    if a['email'].lower() != own_email.lower()
                )
                if cc_emails:
                    self.cc_entry = Gtk.Entry(hexpand=True)
                    self.cc_entry.add_css_class('compose-row-entry')
                    self.cc_entry.set_text(cc_emails)
                    fields.append(self._make_entry_field('Cc:', self.cc_entry))
                    self.cc_entry.connect('changed', self._on_compose_content_changed)

            subj = reply_to.get('subject', '')
            if not subj.lower().startswith('re:'):
                subj = 'Re: ' + subj
            self.subject_entry.set_text(subj)
            self.body.grab_focus()
        else:
            self.to_entry.grab_focus()

        self.to_entry.connect('changed', self._on_compose_content_changed)
        self.subject_entry.connect('changed', self._on_compose_content_changed)
        self._buffer.connect('changed', self._on_compose_content_changed)
        self._initial_snapshot = self._snapshot_state()
        self._snapshot_cache = self._initial_snapshot
        self._refresh_compose_summary()

    def get_title(self):
        return self._title

    def _build_style_controls(self):
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        size_values = [str(size) for size in FONT_SIZE_OPTIONS]
        self._font_size_dropdown = Gtk.DropDown.new_from_strings(size_values)
        self._font_size_dropdown.add_css_class('compose-style-dropdown')
        self._font_size_dropdown.set_tooltip_text('Font size')
        self._font_size_dropdown.set_selected(size_values.index(str(DEFAULT_FONT_SIZE_POINTS)))
        self._font_size_dropdown.connect('notify::selected', self._on_font_size_changed)
        controls.append(self._font_size_dropdown)

        self._color_dialog = Gtk.ColorDialog()
        self._color_button = Gtk.ColorDialogButton.new(self._color_dialog)
        self._color_button.add_css_class('compose-color-btn')
        self._color_button.set_tooltip_text('Text color')
        self._color_button.set_rgba(Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0))
        self._color_button.connect('notify::rgba', self._on_color_changed)
        controls.append(self._color_button)
        return controls

    def _snapshot_state(self):
        return {
            'backend_index': self._selected_backend_index,
            'to': self.to_entry.get_text(),
            'subject': self.subject_entry.get_text(),
            'cc': self.cc_entry.get_text() if self.cc_entry else '',
            'bcc_me': self._bcc_switch.get_active(),
            'body': self._buffer_to_plain_text(),
            'html': self._buffer_to_html(),
            'attachments': [a['name'] for a in self._attachments],
        }

    def _invalidate_snapshot_cache(self):
        self._snapshot_cache = None

    def _current_snapshot(self):
        if self._snapshot_cache is None:
            self._snapshot_cache = self._snapshot_state()
        return self._snapshot_cache

    def _cancel_dirty_check(self):
        if self._dirty_check_id is not None:
            GLib.source_remove(self._dirty_check_id)
            self._dirty_check_id = None

    def _refresh_dirty_state(self):
        self._dirty_check_id = None
        self._dirty = (self._current_snapshot() != self._initial_snapshot)
        self._refresh_compose_summary()
        return GLib.SOURCE_REMOVE

    def _note_compose_change(self):
        self._invalidate_snapshot_cache()
        self._dirty = True
        self._refresh_compose_summary()
        self._cancel_dirty_check()
        self._dirty_check_id = GLib.timeout_add(150, self._refresh_dirty_state)

    def _attachment_count(self):
        return len(getattr(self, '_attachments', []))

    def has_unsaved_changes(self):
        if not self._dirty and self._dirty_check_id is None:
            return False
        self._cancel_dirty_check()
        self._dirty = (self._current_snapshot() != self._initial_snapshot)
        return self._dirty

    def mark_clean(self):
        self._cancel_dirty_check()
        self._initial_snapshot = self._snapshot_state()
        self._snapshot_cache = self._initial_snapshot
        self._dirty = False
        self._refresh_compose_summary()

    def _finish_close(self):
        self._cancel_dirty_check()
        if callable(self._on_close):
            self._on_close(self)

    def request_close(self, on_done=None):
        if not self.has_unsaved_changes():
            self._finish_close()
            if on_done:
                on_done(True)
            return

        dialog = Adw.AlertDialog(
            heading='Discard draft?',
            body='You have unsaved compose changes. Save a draft, discard them, or keep editing.',
        )
        dialog.add_response('cancel', 'Keep Editing')
        dialog.add_response('draft', 'Save Draft')
        dialog.add_response('discard', 'Discard')
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.set_response_appearance('draft', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)

        def _on_choice(_dialog, result, data=None):
            response = dialog.choose_finish(result)
            if response == 'discard':
                self._finish_close()
                if on_done:
                    on_done(True)
            elif response == 'draft':
                saved = self._save_draft()
                if saved:
                    self._finish_close()
                if on_done:
                    on_done(saved)
            else:
                if on_done:
                    on_done(False)

        dialog.choose(self._parent, None, _on_choice)

    def _on_discard_clicked(self, *_):
        self.request_close()

    def _build_operator_bar(self, backend):
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=0,
            margin_bottom=0,
            hexpand=True,
        )
        bar.set_halign(Gtk.Align.FILL)
        bar.add_css_class('compose-operator-bar')

        from_control = self._build_from_control(backend)
        bar.append(from_control)

        bcc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bcc_box.add_css_class('compose-bcc-toggle')
        bcc_box.set_valign(Gtk.Align.CENTER)
        bcc_label = Gtk.Label(label='BCC me', halign=Gtk.Align.START, xalign=0)
        bcc_label.add_css_class('dim-label')
        self._bcc_switch = Gtk.Switch()
        self._bcc_switch.set_active(False)
        self._bcc_switch.set_tooltip_text('Send a copy to yourself as Bcc')
        self._bcc_switch.set_valign(Gtk.Align.CENTER)
        self._bcc_switch.connect('notify::active', self._on_compose_content_changed)
        bcc_box.append(bcc_label)
        bcc_box.append(self._bcc_switch)
        bar.append(bcc_box)

        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)

        discard_btn = self._make_tool_button(
            ['user-trash-symbolic', 'window-close-symbolic'],
            'Discard',
            self._on_discard_clicked,
            '×',
        )
        discard_btn.add_css_class('compose-operator-action')
        discard_btn.add_css_class('compose-discard')
        discard_btn.set_size_request(30, 30)
        discard_btn.set_valign(Gtk.Align.CENTER)
        bar.append(discard_btn)

        draft_btn = self._make_tool_button(
            ['document-save-symbolic', 'document-save-as-symbolic'],
            'Save Draft',
            self._on_save_draft,
            '◰',
        )
        draft_btn.add_css_class('compose-operator-action')
        draft_btn.add_css_class('compose-draft')
        draft_btn.set_size_request(30, 30)
        draft_btn.set_valign(Gtk.Align.CENTER)
        bar.append(draft_btn)

        attach_btn = self._make_tool_button(
            ['mail-attachment-symbolic', 'document-open-symbolic'],
            'Attach file',
            self._on_attach_clicked,
            '⊕',
        )
        attach_btn.add_css_class('compose-operator-action')
        attach_btn.set_size_request(30, 30)
        attach_btn.set_valign(Gtk.Align.CENTER)
        bar.append(attach_btn)

        self.send_btn = Gtk.Button(label='Send')
        self.send_btn.add_css_class('suggested-action')
        self.send_btn.add_css_class('compose-send-btn')
        self.send_btn.set_size_request(94, 30)
        self.send_btn.set_valign(Gtk.Align.CENTER)
        self.send_btn.connect('clicked', self._on_send)
        bar.append(self.send_btn)

        return bar

    def _build_from_control(self, backend):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class('compose-from-surface')

        inline_label = Gtk.Label(label='Send from', halign=Gtk.Align.START, xalign=0)
        inline_label.add_css_class('compose-from-inline-label')
        row.append(inline_label)

        if len(self._backends) > 1:
            self._from_button = Gtk.MenuButton()
            self._from_button.add_css_class('flat')
            self._from_button.add_css_class('compose-from-button')
            self._from_button.set_halign(Gtk.Align.START)

            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            inner.add_css_class('compose-account-pill')
            inner.add_css_class(self._selected_backend_class)
            inner.set_margin_top(1)
            inner.set_margin_bottom(1)
            self._from_label = Gtk.Label(label=self._backends[self._selected_backend_index].identity, xalign=0)
            self._from_label.set_hexpand(True)
            self._from_label.set_wrap(False)
            self._from_label.set_ellipsize(1)
            self._from_label.add_css_class('compose-from-value')
            inner.append(self._from_label)
            arrow = Gtk.Image(icon_name='pan-down-symbolic', pixel_size=12)
            inner.append(arrow)
            self._from_button.set_child(inner)
            self._from_button.set_tooltip_text(self._backends[self._selected_backend_index].identity)
            self._from_pill = inner

            identities = Gtk.StringList.new([b.identity for b in self._backends])
            self._from_popover = Gtk.Popover()
            self._from_popover.add_css_class('compose-account-popover')
            self._from_popover.set_position(Gtk.PositionType.TOP)
            list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            list_box.add_css_class('boxed-list')
            list_box.set_size_request(254, -1)
            for i in range(identities.get_n_items()):
                identity = identities.get_string(i)
                choice_row = Gtk.ListBoxRow()
                choice_row._account_index = i
                choice_row.add_css_class('compose-account-row')
                choice_row.add_css_class(account_class_for_index(i))
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row_box.add_css_class('compose-account-row-content')
                strip = Gtk.Box()
                strip.add_css_class('compose-account-strip')
                strip.add_css_class(account_class_for_index(i))
                row_box.append(strip)
                label = Gtk.Label(label=identity, halign=Gtk.Align.START, xalign=0)
                label.set_hexpand(True)
                label.set_wrap(False)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.add_css_class('compose-account-label')
                row_box.append(label)
                choice_row.set_child(row_box)
                list_box.append(choice_row)
            list_box.select_row(list_box.get_row_at_index(self._selected_backend_index))
            list_box.connect('row-activated', self._on_from_account_activated)
            self._from_popover.set_child(list_box)
            self._from_button.set_popover(self._from_popover)
            row.append(self._from_button)
            return row

        from_value = Gtk.Label(label=backend.identity, halign=Gtk.Align.START, xalign=0)
        from_value.set_selectable(True)
        from_value.set_wrap(False)
        from_value.set_ellipsize(1)
        from_value.add_css_class('compose-from-value')
        from_value.add_css_class('compose-account-pill')
        from_value.add_css_class(account_class_for_index(0))
        from_value.add_css_class('compose-from-button')
        from_value.set_size_request(254, -1)
        row.append(from_value)
        return row

    def _make_entry_field(self, label_text, entry):
        field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        field.add_css_class('compose-field')
        label = Gtk.Label(label=label_text, halign=Gtk.Align.START, xalign=0)
        label.add_css_class('compose-field-label')
        field.append(label)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.add_css_class('compose-field-line')
        line.append(entry)
        field.append(line)
        return field

    def _make_tool_button(self, icon_names, tooltip, callback, fallback):
        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.set_tooltip_text(tooltip)
        btn.connect('clicked', callback)
        btn.set_focus_on_click(False)
        display = self.get_display() or self._parent.get_display()
        theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
        child = None
        for icon_name in icon_names:
            if theme is None or theme.has_icon(icon_name):
                child = Gtk.Image(icon_name=icon_name, pixel_size=14)
                break
        if child is None:
            child = Gtk.Label(label=fallback)
            child.add_css_class('compose-tool-fallback')
        btn.set_child(child)
        return btn

    def _make_toggle_tool_button(self, icon_names, tooltip, callback, fallback):
        btn = Gtk.ToggleButton()
        btn.add_css_class('flat')
        btn.set_tooltip_text(tooltip)
        btn.connect('toggled', callback)
        btn.set_focus_on_click(False)
        display = self.get_display() or self._parent.get_display()
        theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
        child = None
        for icon_name in icon_names:
            if theme is None or theme.has_icon(icon_name):
                child = Gtk.Image(icon_name=icon_name, pixel_size=14)
                break
        if child is None:
            child = Gtk.Label(label=fallback)
            child.add_css_class('compose-tool-fallback')
        btn.set_child(child)
        return btn

    def _get_selected_backend(self):
        return self._backends[self._selected_backend_index]

    def _apply_account_css(self):
        return build_compose_account_css()

    def _apply_css(self):
        display = self.get_display() or self._parent.get_display()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(COMPOSE_CSS + self._apply_account_css())
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _current_backend_identity(self):
        return self._get_selected_backend().identity

    def _setup_editor_tags(self):
        self._tag_bold = self._buffer.create_tag('bold', weight=Pango.Weight.BOLD)
        self._tag_italic = self._buffer.create_tag('italic', style=Pango.Style.ITALIC)
        self._tag_quote = self._buffer.create_tag(
            'quote',
            left_margin=18,
            pixels_above_lines=2,
            pixels_below_lines=2,
        )
        self._insert_handler_id = self._buffer.connect('insert-text', self._on_buffer_insert_text)
        self._buffer.connect('mark-set', self._on_buffer_mark_set)

    def _color_tag_name(self, color_hex):
        return f'fg:{color_hex.lower()}'

    def _size_tag_name(self, size_points):
        return f'size:{int(size_points)}'

    def _get_or_create_color_tag(self, color_hex):
        color_hex = color_hex.lower()
        tag = self._color_tags.get(color_hex)
        if tag is not None:
            return tag
        rgba = Gdk.RGBA()
        rgba.parse(color_hex)
        tag = self._buffer.create_tag(self._color_tag_name(color_hex), foreground_rgba=rgba)
        self._color_tags[color_hex] = tag
        return tag

    def _get_or_create_size_tag(self, size_points):
        size_points = int(size_points)
        tag = self._size_tags.get(size_points)
        if tag is not None:
            return tag
        tag = self._buffer.create_tag(self._size_tag_name(size_points), size_points=float(size_points))
        self._size_tags[size_points] = tag
        return tag

    def _remove_tags_by_prefix(self, start, end, prefix):
        tags = self._color_tags.values() if prefix == 'fg:' else self._size_tags.values()
        for tag in list(tags):
            self._buffer.remove_tag(tag, start, end)

    def _get_selection_bounds(self, buffer=None):
        buffer = buffer or self._buffer
        bounds = buffer.get_selection_bounds()
        if len(bounds) == 2:
            start, end = bounds
            return True, start, end
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        return False, cursor.copy(), cursor.copy()

    def _on_buffer_mark_set(self, buffer, location, mark):
        if mark.get_name() != 'selection_bound':
            return
        has_selection, start, end = self._get_selection_bounds(buffer)
        if has_selection:
            self._last_selection = (start.copy(), end.copy())

    def _on_buffer_insert_text(self, buffer, location, text, length):
        if self._inserting_rich_text or not text:
            return
        tags = []
        if self._bold_active:
            tags.append(self._tag_bold)
        if self._italic_active:
            tags.append(self._tag_italic)
        if self._active_color:
            tags.append(self._get_or_create_color_tag(self._active_color))
        if self._active_font_size:
            tags.append(self._get_or_create_size_tag(self._active_font_size))
        if not tags:
            return
        buffer.stop_emission_by_name('insert-text')
        self._inserting_rich_text = True
        try:
            handler = self._insert_handler_id
            if handler is not None:
                buffer.handler_block(handler)
            start = location.copy()
            buffer.insert_with_tags(start, text, *tags)
            end = start.copy()
            end.forward_chars(len(text))
            buffer.place_cursor(end)
        finally:
            if self._insert_handler_id is not None:
                buffer.handler_unblock(self._insert_handler_id)
            self._inserting_rich_text = False

    def _selected_bounds(self):
        has_selection, start, end = self._get_selection_bounds()
        if has_selection:
            self._last_selection = (start.copy(), end.copy())
            return True, start, end
        if self._last_selection is not None:
            start, end = self._last_selection
            return True, start.copy(), end.copy()
        return False, start, end

    def _range_has_tag(self, start, end, tag):
        it = start.copy()
        while it.compare(end) < 0:
            if tag not in it.get_tags():
                return False
            it.forward_char()
        return True

    def _toggle_inline_tag(self, tag_name):
        tag = self._tag_bold if tag_name == 'bold' else self._tag_italic
        has_selection, start, end = self._selected_bounds()
        if has_selection:
            if self._range_has_tag(start, end, tag):
                self._buffer.remove_tag(tag, start, end)
            else:
                self._buffer.apply_tag(tag, start, end)
            self._last_selection = (start.copy(), end.copy())
            self._note_compose_change()
            self.body.grab_focus()
            return
        if tag_name == 'bold':
            self._bold_active = not self._bold_active
        else:
            self._italic_active = not self._italic_active
        self.body.grab_focus()

    def _apply_color_to_selection(self, color_hex):
        has_selection, start, end = self._selected_bounds()
        if has_selection:
            self._remove_tags_by_prefix(start, end, 'fg:')
            self._buffer.apply_tag(self._get_or_create_color_tag(color_hex), start, end)
            self._last_selection = (start.copy(), end.copy())
            self._note_compose_change()
        self._active_color = color_hex
        self.body.grab_focus()

    def _apply_font_size_to_selection(self, size_points):
        has_selection, start, end = self._selected_bounds()
        if has_selection:
            self._remove_tags_by_prefix(start, end, 'size:')
            self._buffer.apply_tag(self._get_or_create_size_tag(size_points), start, end)
            self._last_selection = (start.copy(), end.copy())
            self._note_compose_change()
        self._active_font_size = int(size_points)
        self.body.grab_focus()

    def _on_color_changed(self, button, _pspec):
        self._apply_color_to_selection(_rgba_to_hex(button.get_rgba()))

    def _on_font_size_changed(self, dropdown, _pspec):
        selected = dropdown.get_selected()
        if selected >= len(FONT_SIZE_OPTIONS):
            return
        self._apply_font_size_to_selection(FONT_SIZE_OPTIONS[selected])

    def _current_line_bounds(self):
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        start = cursor.copy()
        start.set_line_offset(0)
        end = cursor.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return start, end

    def _toggle_quote(self, *_):
        has_selection, start, end = self._selected_bounds()
        if not has_selection:
            start, end = self._current_line_bounds()
        if self._range_has_tag(start, end, self._tag_quote):
            self._buffer.remove_tag(self._tag_quote, start, end)
        else:
            self._buffer.apply_tag(self._tag_quote, start, end)
        self._last_selection = (start.copy(), end.copy())
        self._note_compose_change()
        self.body.grab_focus()

    def _toggle_list(self, *_):
        has_selection, start, end = self._selected_bounds()
        if has_selection:
            text = self._buffer.get_text(start, end, False)
            lines = text.splitlines() or ['']
            self._buffer.delete(start, end)
            self._buffer.insert_at_cursor('\n'.join(f'• {line}' for line in lines))
        else:
            line_start, _line_end = self._current_line_bounds()
            current = self._buffer.get_text(line_start, _line_end, False)
            if not current.startswith('• '):
                self._buffer.insert(line_start, '• ')
        self._last_selection = (start.copy(), end.copy())
        self.body.grab_focus()

    def _on_bold_clicked(self, _):
        self._toggle_inline_tag('bold')

    def _on_italic_clicked(self, _):
        self._toggle_inline_tag('italic')

    def _on_quote_clicked(self, _):
        self._toggle_quote()

    def _on_list_clicked(self, _):
        self._toggle_list()

    def _inline_html(self, start, end):
        chunks = []
        active_style = None
        active_bold = False
        active_italic = False
        it = start.copy()
        while it.compare(end) < 0:
            tags = {t.get_property('name') for t in it.get_tags()}
            style_parts = []
            for tag_name in tags:
                if tag_name.startswith('fg:'):
                    style_parts.append(f'color: {tag_name[3:]}')
                elif tag_name.startswith('size:'):
                    style_parts.append(f'font-size: {tag_name[5:]}pt')
            desired_style = '; '.join(sorted(style_parts)) if style_parts else None
            desired_bold = 'bold' in tags
            desired_italic = 'italic' in tags

            if (
                desired_style != active_style
                or desired_bold != active_bold
                or desired_italic != active_italic
            ):
                if active_italic:
                    chunks.append('</i>')
                if active_bold:
                    chunks.append('</b>')
                if active_style is not None:
                    chunks.append('</span>')
                if desired_style is not None:
                    chunks.append(f'<span style="{desired_style}">')
                if desired_bold:
                    chunks.append('<b>')
                if desired_italic:
                    chunks.append('<i>')
                active_style = desired_style
                active_bold = desired_bold
                active_italic = desired_italic

            ch = it.get_char()
            if ch == '\n':
                chunks.append('<br>')
            else:
                chunks.append(_html_escape(ch))
            it.forward_char()
        if active_italic:
            chunks.append('</i>')
        if active_bold:
            chunks.append('</b>')
        if active_style is not None:
            chunks.append('</span>')
        return ''.join(chunks)

    def _buffer_to_html(self):
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        if start.equal(end):
            return ''
        lines = []
        it = start.copy()
        while True:
            line_start = it.copy()
            line_end = it.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            text = self._buffer.get_text(line_start, line_end, False)
            if self._range_has_tag(line_start, line_end, self._tag_quote):
                lines.append(f'<blockquote>{self._inline_html(line_start, line_end)}</blockquote>')
            elif text.startswith('• '):
                lines.append(f'<p>{self._inline_html(line_start, line_end)}</p>')
            else:
                html = self._inline_html(line_start, line_end)
                lines.append(f'<p>{html if html else "<br>"}</p>')
            if not line_end.forward_line():
                break
            it = line_end
        body = ''.join(lines)
        return (
            '<html><body style="font-family: sans-serif; font-size: 14px; line-height: 1.5;">'
            + body +
            '</body></html>'
        )

    def _buffer_to_plain_text(self):
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        return self._buffer.get_text(start, end, False)

    def _on_from_account_activated(self, listbox, row):
        idx = getattr(row, '_account_index', None)
        if idx is None or idx >= len(self._backends):
            return
        old_class = self._selected_backend_class
        self._selected_backend_index = idx
        self._selected_backend_class = account_class_for_index(idx)
        if hasattr(self, '_from_label'):
            self._from_label.set_label(self._backends[idx].identity)
        if hasattr(self, '_from_pill'):
            self._from_pill.remove_css_class(old_class)
            self._from_pill.add_css_class(self._selected_backend_class)
        if hasattr(self, '_from_button'):
            self._from_button.set_tooltip_text(self._backends[idx].identity)
        listbox.select_row(row)
        self._note_compose_change()

    def _ensure_buffer_text(self):
        return self.body.get_buffer()

    def _wrap_selection(self, marker):
        buf = self.body.get_buffer()
        has_selection, start, end = self._get_selection_bounds(buf)
        if has_selection:
            text = buf.get_text(start, end, False)
            buf.delete(start, end)
            buf.insert_at_cursor(f'{marker}{text}{marker}')
        else:
            insert = buf.get_iter_at_mark(buf.get_insert())
            cursor = insert.copy()
            buf.insert(insert, marker + marker)
            cursor.forward_chars(len(marker))
            buf.place_cursor(cursor)
        self.body.grab_focus()

    def _prefix_lines(self, prefix):
        buf = self.body.get_buffer()
        has_selection, start, end = self._get_selection_bounds(buf)
        if has_selection:
            text = buf.get_text(start, end, False)
            lines = text.splitlines() or ['']
            buf.delete(start, end)
            buf.insert_at_cursor('\n'.join(prefix + line for line in lines))
        else:
            insert = buf.get_iter_at_mark(buf.get_insert())
            buf.insert(insert, prefix)
        self.body.grab_focus()

    # ── File attachments ──────────────────────────────────────────────────────

    def _on_attach_clicked(self, _button=None):
        try:
            dialog = Gtk.FileDialog()
            dialog.open_multiple(self._parent, None, self._on_files_chosen)
        except Exception:
            # Fallback for GTK < 4.10
            chooser = Gtk.FileChooserDialog(
                title='Attach files',
                transient_for=self._parent,
                action=Gtk.FileChooserAction.OPEN,
            )
            chooser.add_button('Cancel', Gtk.ResponseType.CANCEL)
            chooser.add_button('Open', Gtk.ResponseType.ACCEPT)
            chooser.set_select_multiple(True)
            chooser.connect('response', self._on_chooser_response)
            chooser.present()

    def _on_files_chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except Exception:
            return
        if files is None:
            return
        for i in range(files.get_n_items()):
            gfile = files.get_item(i)
            self._load_gfile(gfile)

    def _on_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            for gfile in chooser.get_files():
                self._load_gfile(gfile)
        chooser.destroy()

    def _load_gfile(self, gfile):
        try:
            path = gfile.get_path()
            if not path:
                return
            import os, mimetypes
            name = os.path.basename(path)
            size = os.path.getsize(path)
            if size > 25 * 1024 * 1024:
                self._show_toast(f'{name} is too large (max 25 MB)')
                return
            with open(path, 'rb') as f:
                data = f.read()
            content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            self._attachments.append({
                'name': name,
                'data': data,
                'content_type': content_type,
                'size': size,
            })
            self._add_attachment_chip(name, len(self._attachments) - 1)
            self._note_compose_change()
        except Exception as e:
            self._show_toast(f'Could not attach file: {e}')

    def _add_attachment_chip(self, name, index):
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chip.add_css_class('compose-attach-chip')
        chip._attach_index = index
        lbl = Gtk.Label(label=name)
        lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        lbl.set_max_width_chars(24)
        remove_btn = Gtk.Button()
        remove_btn.add_css_class('compose-attach-chip-remove')
        remove_btn.add_css_class('flat')
        try:
            remove_btn.set_icon_name('window-close-symbolic')
        except Exception:
            remove_btn.set_label('×')
        remove_btn.set_tooltip_text(f'Remove {name}')
        remove_btn.connect('clicked', self._on_remove_attachment, chip)
        chip.append(lbl)
        chip.append(remove_btn)
        self._attach_strip.append(chip)
        self._attach_strip.set_visible(True)

    def _on_remove_attachment(self, _btn, chip):
        idx = chip._attach_index
        if 0 <= idx < len(self._attachments):
            self._attachments.pop(idx)
            # Re-index remaining chips
            child = self._attach_strip.get_first_child()
            i = 0
            while child is not None:
                if hasattr(child, '_attach_index'):
                    child._attach_index = i
                    i += 1
                child = child.get_next_sibling()
        self._attach_strip.remove(chip)
        if self._attach_strip.get_first_child() is None:
            self._attach_strip.set_visible(False)
        self._note_compose_change()

    def _show_toast(self, msg):
        if hasattr(self._parent, '_show_toast'):
            self._parent._show_toast(msg)

    # ── Contacts autocomplete ─────────────────────────────────────────────────

    def _on_to_changed(self, entry):
        self.to_entry.remove_css_class('error')
        if self._contact_timer:
            GLib.source_remove(self._contact_timer)
            self._contact_timer = None
        # Use the last token after comma as the query
        text = self.to_entry.get_text()
        query = text.split(',')[-1].strip()
        if len(query) < 2:
            self._contact_popover.popdown()
            return
        self._contact_timer = GLib.timeout_add(400, self._fetch_contacts, query)

    def _fetch_contacts(self, query):
        self._contact_timer = None
        self._contact_fetch_gen += 1
        gen = self._contact_fetch_gen
        backend = self._get_selected_backend()
        def fetch():
            contacts = backend.fetch_contacts(query)
            GLib.idle_add(self._show_contacts, contacts, gen)
        threading.Thread(target=fetch, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _show_contacts(self, contacts, gen=None):
        if gen is not None and gen != self._contact_fetch_gen:
            return
        while (child := self._contact_list_box.get_first_child()):
            self._contact_list_box.remove(child)
        if not contacts:
            self._contact_popover.popdown()
            return
        for c in contacts[:8]:
            row = Gtk.ListBoxRow()
            row._contact = c
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                halign=Gtk.Align.FILL,
                margin_start=10, margin_end=10,
                margin_top=8, margin_bottom=8,
                spacing=1,
            )
            name = c.get('name', '').strip()
            email = c.get('email', '').strip()
            if name:
                lbl = Gtk.Label(label=name, halign=Gtk.Align.START, xalign=0)
                lbl.set_ellipsize(1)
                box.append(lbl)
                email_lbl = Gtk.Label(label=email, halign=Gtk.Align.START, xalign=0)
                email_lbl.add_css_class('dim-label')
                email_lbl.add_css_class('caption')
                email_lbl.set_ellipsize(1)
                box.append(email_lbl)
            else:
                lbl = Gtk.Label(label=email, halign=Gtk.Align.START, xalign=0)
                lbl.set_ellipsize(1)
                box.append(lbl)
            row.set_child(box)
            self._contact_list_box.append(row)
        self._contact_popover.popup()

    def _on_contact_selected(self, _, row):
        self._contact_fetch_gen += 1
        c = row._contact
        addr = f"{c['name']} <{c['email']}>" if c['name'] else c['email']
        current = self.to_entry.get_text()
        parts = [p.strip() for p in current.split(',')]
        parts[-1] = addr
        self.to_entry.set_text(', '.join(parts) + ', ')
        # Move cursor to end
        self.to_entry.grab_focus()
        self._contact_popover.popdown()
        self._refresh_compose_summary()

    # ── Send ──────────────────────────────────────────────────────────────────

    def _on_send(self, _):
        try:
            to = self.to_entry.get_text().strip().rstrip(',').strip()
            subject = self.subject_entry.get_text().strip()
            body = self._buffer_to_plain_text()
            html = self._buffer_to_html()
            cc_entry = getattr(self, 'cc_entry', None)
            cc_text = cc_entry.get_text().strip().rstrip(',').strip() if cc_entry else ''
            bcc = [self._current_backend_identity()] if self._bcc_switch.get_active() else []
            backend = self._get_selected_backend()
        except Exception as e:
            self._on_send_error(str(e))
            return

        if not to:
            self.to_entry.add_css_class('error')
            return
        self.to_entry.remove_css_class('error')

        self.send_btn.set_sensitive(False)
        self.send_btn.set_label('Sending…')
        attachments = list(self._attachments)
        def send():
            try:
                backend.send_message(to, subject, body, html=html, cc=cc_text, bcc=bcc,
                                     reply_to_msg=self._reply_to_msg, attachments=attachments)
                GLib.idle_add(self._on_send_success)
            except Exception as e:
                GLib.idle_add(self._on_send_error, str(e))

        threading.Thread(target=send, daemon=True).start()

    def _on_save_draft(self, _):
        if self._save_draft():
            self._finish_close()

    def _save_draft(self):
        _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        backend = self._get_selected_backend()
        draft = {
            'backend': backend.identity,
            'to': self.to_entry.get_text(),
            'subject': self.subject_entry.get_text(),
            'body': self._buffer_to_plain_text(),
            'html': self._buffer_to_html(),
            'bcc_me': self._bcc_switch.get_active(),
            'saved_at': datetime.utcnow().isoformat() + 'Z',
        }
        path = _DRAFTS_DIR / f'draft-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}.json'
        try:
            import json
            path.write_text(json.dumps(draft, indent=2))
            if hasattr(self._parent, '_show_toast'):
                GLib.idle_add(self._parent._show_toast, f'Saved draft {path.name}')
            self.mark_clean()
            return True
        except Exception as e:
            self._on_send_error(f'Could not save draft: {e}')
            return False

    def _on_send_success(self):
        self.mark_clean()
        if hasattr(self._parent, '_show_toast'):
            self._parent._show_toast('Message sent')
        if hasattr(self._parent, 'refresh_visible_mail'):
            GLib.idle_add(self._parent.refresh_visible_mail, True)
        app = self._parent.get_application() if hasattr(self._parent, 'get_application') else None
        if app is not None and hasattr(app, 'wake_background_updates'):
            app.wake_background_updates()
        self._finish_close()

    def _on_send_error(self, msg):
        self.send_btn.set_sensitive(True)
        self.send_btn.set_label('Send')
        dialog = Adw.AlertDialog(heading='Send failed', body=msg)
        dialog.add_response('ok', 'OK')
        dialog.present(self._parent)

    def _recipient_count(self):
        counts = []
        for entry in (self.to_entry, getattr(self, 'cc_entry', None)):
            if entry is None:
                continue
            text = entry.get_text().strip().rstrip(',')
            if not text:
                continue
            counts.extend([part.strip() for part in text.split(',') if part.strip()])
        return len(counts)

    def _compose_context_summary(self):
        parts = [f'From: {self._current_backend_identity()}']
        if self._title == 'Reply':
            sender = ''
            if self.to_entry.get_text().strip():
                sender = self.to_entry.get_text().strip().rstrip(',')
            if sender:
                parts.append(f'Replying to {sender}')
        recipient_count = self._recipient_count()
        if recipient_count == 0:
            parts.append('No recipients yet')
        elif recipient_count == 1:
            parts.append('1 recipient')
        else:
            parts.append(f'{recipient_count} recipients')
        attachment_count = self._attachment_count()
        if attachment_count > 0:
            parts.append(f'Attachments: {attachment_count}')
        if self._bcc_switch.get_active():
            parts.append('BCC on')
        if self._dirty:
            parts.append('Unsaved changes')
        return ' • '.join(parts)

    def _refresh_compose_summary(self, *_):
        if hasattr(self, '_summary_label'):
            self._summary_label.set_label(self._compose_context_summary())

    def _on_compose_content_changed(self, *_):
        self._note_compose_change()

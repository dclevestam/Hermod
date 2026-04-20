"""Ctrl-K command palette — modal search over the active message store."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib


class CommandPalette(Gtk.Window):
    def __init__(self, window):
        super().__init__(
            transient_for=window,
            modal=True,
            decorated=False,
            resizable=False,
            default_width=620,
            default_height=480,
        )
        self._window = window
        self.add_css_class("command-palette")
        self.set_hide_on_close(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("command-palette-shell")

        # Header: search icon + entry + LOCAL chip + Esc hint
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("command-palette-header")

        icon = Gtk.Image(icon_name="system-search-symbolic", pixel_size=18)
        icon.add_css_class("command-palette-icon")
        header.append(icon)

        self._entry = Gtk.Entry(placeholder_text="Search mail, commands…", hexpand=True)
        self._entry.add_css_class("command-palette-entry")
        self._entry.connect("changed", self._on_entry_changed)
        self._entry.connect("activate", self._on_activate)
        header.append(self._entry)

        chip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chip_row.add_css_class("command-palette-chips")
        semantic_chip = Gtk.Label(label="Semantic")
        semantic_chip.add_css_class("command-palette-chip")
        chip_row.append(semantic_chip)
        local_chip = Gtk.Label(label="LOCAL")
        local_chip.add_css_class("command-palette-chip")
        local_chip.add_css_class("command-palette-chip-local")
        chip_row.append(local_chip)
        header.append(chip_row)

        esc_hint = Gtk.Label(label="Esc")
        esc_hint.add_css_class("command-palette-kbd")
        header.append(esc_hint)

        root.append(header)

        # Results list — reuses the window's filtered message model so typing
        # filters the same store the main list shows.
        self._results_model = window._filtered_message_model
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        selection = Gtk.SingleSelection.new(self._results_model)
        selection.set_autoselect(False)
        self._selection = selection
        list_view = Gtk.ListView.new(selection, factory)
        list_view.add_css_class("command-palette-list")
        list_view.connect("activate", self._on_row_activate)

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        scroller.set_child(list_view)
        root.append(scroller)

        # Footer hint
        footer = Gtk.Label(
            label="Try natural language like \"invoices from last month\" — semantic search runs on your device.",
            xalign=0.0,
        )
        footer.add_css_class("command-palette-footer")
        footer.set_wrap(True)
        root.append(footer)

        self.set_child(root)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

    def _setup_row(self, _factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("command-palette-row")
        title = Gtk.Label(xalign=0.0)
        title.add_css_class("command-palette-row-title")
        title.set_ellipsize(3)
        subtitle = Gtk.Label(xalign=0.0)
        subtitle.add_css_class("command-palette-row-subtitle")
        subtitle.set_ellipsize(3)
        box.append(title)
        box.append(subtitle)
        list_item.set_child(box)
        list_item._title = title
        list_item._subtitle = subtitle

    def _bind_row(self, _factory, list_item):
        item = list_item.get_item()
        msg = getattr(item, "msg", None) or {}
        sender = (msg.get("sender_name") or msg.get("sender_email") or "").strip()
        subject = (msg.get("subject") or "(no subject)").strip()
        snippet = (msg.get("snippet") or msg.get("preview") or "").strip()
        list_item._title.set_label(f"{sender} — {subject}" if sender else subject)
        list_item._subtitle.set_label(snippet[:140])

    def _on_entry_changed(self, entry):
        text = entry.get_text()
        src = getattr(self._window, "_search_entry", None)
        if src is not None and src.get_text() != text:
            src.set_text(text)

    def _on_activate(self, _entry):
        self._activate_selected()

    def _on_row_activate(self, _view, _position):
        self._activate_selected()

    def _activate_selected(self):
        index = self._selection.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            return
        item = self._results_model.get_item(index)
        msg = getattr(item, "msg", None)
        if msg and hasattr(self._window, "_commit_email_selection"):
            class _RowProxy:
                pass
            proxy = _RowProxy()
            proxy.msg = msg
            self._window._commit_email_selection(proxy)
        self.close()

    def _on_key(self, _ctrl, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            count = self._results_model.get_n_items()
            if count == 0:
                return True
            index = self._selection.get_selected()
            if index == Gtk.INVALID_LIST_POSITION:
                index = -1 if keyval == Gdk.KEY_Down else count
            new_index = index + (1 if keyval == Gdk.KEY_Down else -1)
            new_index = max(0, min(count - 1, new_index))
            self._selection.set_selected(new_index)
            return True
        return False

    def open(self):
        src = getattr(self._window, "_search_entry", None)
        if src is not None:
            self._entry.set_text(src.get_text())
        self.present()
        GLib.idle_add(self._entry.grab_focus)

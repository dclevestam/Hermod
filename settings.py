import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

try:
    from .styles import contrasting_foreground
    from .settings_accounts import (
        AccountSettingsController,
        _auto_account_color,
        _unique_alias,
    )
except ImportError:
    from styles import contrasting_foreground
    from settings_accounts import (
        AccountSettingsController,
        _auto_account_color,
        _unique_alias,
    )

_CONFIG_DIR = Path(GLib.get_user_config_dir()) / "hermod"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"

DEFAULTS = {
    "poll_interval": 1,
    "reconcile_interval": 10,
    "load_images": True,
    "mark_read_on_open": True,
    "close_minimizes": False,
    "show_unified_trash": True,
    "show_unified_spam": True,
    "diagnostics_enabled": True,
    "debug_logging": True,
    "disk_cache_budget_mb": 64,
    "google_oauth_client_id": "",
}

_MIN_DISK_CACHE_BUDGET_MB = 8
_MAX_DISK_CACHE_BUDGET_MB = 512
_settings_lock = threading.RLock()

_instance = None
_instance_lock = threading.Lock()


def get_disk_cache_budget_limit_mb():
    try:
        free_bytes = shutil.disk_usage(Path(GLib.get_user_cache_dir())).free
    except Exception:
        free_bytes = 0
    free_based_limit = int(free_bytes * 0.10 / (1024 * 1024))
    limit = min(
        _MAX_DISK_CACHE_BUDGET_MB, free_based_limit or _MIN_DISK_CACHE_BUDGET_MB
    )
    return max(_MIN_DISK_CACHE_BUDGET_MB, limit)


def get_disk_cache_free_space_bytes():
    try:
        return shutil.disk_usage(Path(GLib.get_user_cache_dir())).free
    except Exception:
        return 0


def _format_bytes(n):
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.1f} GB"


def clamp_disk_cache_budget_mb(value):
    try:
        value = int(value)
    except Exception:
        value = DEFAULTS["disk_cache_budget_mb"]
    return max(_MIN_DISK_CACHE_BUDGET_MB, min(get_disk_cache_budget_limit_mb(), value))


class Settings:
    def __init__(self):
        self._data = dict(DEFAULTS)
        loaded = {}
        try:
            with open(_SETTINGS_FILE) as f:
                loaded = json.load(f)
                self._data.update(loaded)
        except Exception:
            pass
        migrated = False
        if "reconcile_interval" not in loaded:
            self._data["reconcile_interval"] = DEFAULTS["reconcile_interval"]
            migrated = True
        if (
            "poll_interval" in loaded
            and "reconcile_interval" not in loaded
            and int(self._data.get("poll_interval", 0) or 0) > 1
        ):
            self._data["poll_interval"] = DEFAULTS["poll_interval"]
            migrated = True
        if migrated:
            try:
                self.save()
            except Exception:
                pass

    def save(self):
        with _settings_lock:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix="settings.", suffix=".tmp", dir=_CONFIG_DIR
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self._data, f, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, _SETTINGS_FILE)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise

    def get(self, key):
        with _settings_lock:
            if key == "disk_cache_budget_mb":
                return clamp_disk_cache_budget_mb(self._data.get(key, DEFAULTS[key]))
            return self._data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.update({key: value})

    def update(self, values):
        with _settings_lock:
            changed = False
            for key, value in (values or {}).items():
                if key == "disk_cache_budget_mb":
                    value = clamp_disk_cache_budget_mb(value)
                if self._data.get(key) == value:
                    continue
                self._data[key] = value
                changed = True
            if not changed:
                return
            self.save()


def get_settings():
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = Settings()
    return _instance


_SETTINGS_CSS = """
.settings-section-title {
    font-size: 1.02em;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: alpha(#b7beb8, 0.82);
    margin-bottom: 3px;
}
.account-tile {
    min-width: 192px;
    border-radius: 18px;
    border: 1px solid alpha(#dfe4de, 0.08);
    background:
        linear-gradient(180deg, alpha(white, 0.03), alpha(white, 0.01)),
        alpha(#121715, 0.92);
}
.account-tile:hover {
    background:
        linear-gradient(180deg, alpha(white, 0.05), alpha(white, 0.02)),
        alpha(#18211d, 0.96);
}
.account-tile-icon {
    color: alpha(#f2efe8, 0.88);
}
.account-row {
    min-height: 50px;
}
.account-row-subtitle {
    color: alpha(#b7beb8, 0.70);
    font-size: 0.82em;
}
.account-row.striped {
    border-radius: 12px;
}
.account-color-preview {
    min-width: 18px;
    min-height: 18px;
    border-radius: 999px;
    border: 1px solid alpha(#dfe4de, 0.10);
}
.account-color-chip {
    min-height: 24px;
    padding: 0 9px;
    border-radius: 999px;
    font-size: 0.80em;
    font-weight: 700;
    background: alpha(#121715, 0.90);
    border: 1px solid alpha(#dfe4de, 0.08);
}
.account-editor-header {
    font-size: 1.14em;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: #f2efe8;
    font-family: Georgia, "Times New Roman", serif;
}
.account-editor-page {
    padding-top: 8px;
}
"""


def _install_settings_css(widget):
    display = widget.get_display() or Gdk.Display.get_default()
    if display is None:
        return
    css = Gtk.CssProvider()
    css.load_from_string(_SETTINGS_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _make_settings_section(title):
    section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    heading = Gtk.Label(label=title, halign=Gtk.Align.START)
    heading.add_css_class("settings-section-title")
    heading.add_css_class("heading")
    group = Adw.PreferencesGroup()
    section.append(heading)
    section.append(group)
    return section, group


def build_settings_content(parent, on_close=None, on_back=None, scrollable=True):
    if scrollable:
        root = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
            hexpand=True,
        )
    else:
        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            vexpand=True,
            hexpand=True,
        )
    stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
    if scrollable:
        root.set_child(stack)
    else:
        root.append(stack)
    _install_settings_css(root)

    s = get_settings()
    pending = {
        "poll_interval": s.get("poll_interval"),
        "reconcile_interval": s.get("reconcile_interval"),
        "disk_cache_budget_mb": s.get("disk_cache_budget_mb"),
    }

    main_page = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_top=18,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
        hexpand=True,
        vexpand=True,
    )
    editor_page = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
        hexpand=True,
        vexpand=True,
    )
    stack.add_named(main_page, "main")
    stack.add_named(editor_page, "editor")
    stack.set_visible_child_name("main")

    account_controller = AccountSettingsController(
        parent, stack, main_page, editor_page, s, on_back=on_back
    )
    account_controller.build_sections()
    root.account_controller = account_controller
    root.open_account_editor = account_controller.open_account_editor
    root.show_accounts_main = account_controller.show_main

    reading_section, reading_group = _make_settings_section("Reading")
    images_row = Adw.SwitchRow(
        title="Load images",
        subtitle="Automatically display images in emails",
    )
    images_row.set_active(s.get("load_images"))

    def on_images_changed(r, _):
        value = r.get_active()
        s.set("load_images", value)
        if parent is not None and hasattr(parent, "_apply_load_images"):
            parent._apply_load_images(value)

    images_row.connect("notify::active", on_images_changed)
    reading_group.add(images_row)

    mark_row = Adw.SwitchRow(
        title="Mark as read on open",
        subtitle="Mark emails as read when you open them",
    )
    mark_row.set_active(s.get("mark_read_on_open"))
    mark_row.connect(
        "notify::active", lambda r, _: s.set("mark_read_on_open", r.get_active())
    )
    reading_group.add(mark_row)
    main_page.append(reading_section)

    behavior_section, behavior_group = _make_settings_section("Behavior")
    close_row = Adw.SwitchRow(
        title="Close minimizes app",
        subtitle="Keep Hermod running in the background when you close the window",
    )
    close_row.set_active(s.get("close_minimizes"))
    close_row.connect(
        "notify::active", lambda r, _: s.set("close_minimizes", r.get_active())
    )
    behavior_group.add(close_row)
    main_page.append(behavior_section)

    sidebar_section, sidebar_group = _make_settings_section("Sidebar")
    trash_row = Adw.SwitchRow(
        title="Show All Trash",
        subtitle="Unified trash folder — takes effect on restart",
    )
    trash_row.set_active(s.get("show_unified_trash"))
    trash_row.connect(
        "notify::active", lambda r, _: s.set("show_unified_trash", r.get_active())
    )
    sidebar_group.add(trash_row)

    spam_row = Adw.SwitchRow(
        title="Show All Spam",
        subtitle="Unified spam folder — takes effect on restart",
    )
    spam_row.set_active(s.get("show_unified_spam"))
    spam_row.connect(
        "notify::active", lambda r, _: s.set("show_unified_spam", r.get_active())
    )
    sidebar_group.add(spam_row)
    main_page.append(sidebar_section)

    sync_section, sync_group = _make_settings_section("Background Updates & Disk Cache")
    poll_row = Adw.ActionRow(
        title="Light check interval",
        subtitle="Minutes between quick background mailbox checks",
    )
    poll_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
    poll_spin.set_value(pending["poll_interval"])
    poll_spin.set_valign(Gtk.Align.CENTER)
    poll_spin.set_halign(Gtk.Align.END)
    poll_spin.set_width_chars(3)
    poll_spin.set_numeric(True)
    poll_spin.set_alignment(0.5)
    sync_group.add(poll_row)
    poll_row.add_suffix(poll_spin)

    reconcile_row = Adw.ActionRow(
        title="Full reconcile interval",
        subtitle="Minutes between full unread count and folder reconciliation",
    )
    reconcile_spin = Gtk.SpinButton.new_with_range(5, 120, 1)
    reconcile_spin.set_value(pending["reconcile_interval"])
    reconcile_spin.set_valign(Gtk.Align.CENTER)
    reconcile_spin.set_halign(Gtk.Align.END)
    reconcile_spin.set_width_chars(3)
    reconcile_spin.set_numeric(True)
    reconcile_spin.set_alignment(0.5)
    sync_group.add(reconcile_row)
    reconcile_row.add_suffix(reconcile_spin)

    cache_limit = get_disk_cache_budget_limit_mb()
    free_space = get_disk_cache_free_space_bytes()
    cache_row = Adw.ActionRow(
        title="Disk Cache",
        subtitle=(
            f"Free space on cache volume: {_format_bytes(free_space)}. "
            f"Allowed range: {_MIN_DISK_CACHE_BUDGET_MB} to {cache_limit} MB "
            f"(hard cap {_MAX_DISK_CACHE_BUDGET_MB} MB)."
        ),
    )
    cache_spin = Gtk.SpinButton.new_with_range(
        _MIN_DISK_CACHE_BUDGET_MB,
        cache_limit,
        1,
    )
    cache_spin.set_value(pending["disk_cache_budget_mb"])
    cache_spin.set_valign(Gtk.Align.CENTER)
    cache_spin.set_halign(Gtk.Align.END)
    cache_spin.set_numeric(True)
    cache_spin.set_width_chars(4)
    cache_spin.set_alignment(0.5)
    sync_group.add(cache_row)
    cache_row.add_suffix(cache_spin)

    apply_row = Adw.ActionRow(
        title="Apply changes",
        subtitle="Save background check interval and disk cache budget",
    )
    save_btn = Gtk.Button(label="Save")
    save_btn.add_css_class("suggested-action")
    save_btn.add_css_class("small")
    save_btn.set_valign(Gtk.Align.CENTER)
    save_btn.set_margin_top(2)
    save_btn.set_margin_bottom(2)
    save_btn.set_margin_start(2)
    save_btn.set_margin_end(2)
    save_btn.set_size_request(68, -1)
    apply_row.add_suffix(save_btn)
    sync_group.add(apply_row)
    main_page.append(sync_section)

    def update_save_state():
        save_btn.set_sensitive(
            pending["poll_interval"] != s.get("poll_interval")
            or pending["reconcile_interval"] != s.get("reconcile_interval")
            or pending["disk_cache_budget_mb"] != s.get("disk_cache_budget_mb")
        )

    def on_poll_changed(w):
        pending["poll_interval"] = int(w.get_value())
        update_save_state()

    def on_reconcile_changed(w):
        pending["reconcile_interval"] = int(w.get_value())
        update_save_state()

    def on_cache_changed(w):
        pending["disk_cache_budget_mb"] = int(w.get_value())
        update_save_state()

    def on_save(_btn):
        old_poll = s.get("poll_interval")
        old_reconcile = s.get("reconcile_interval")
        old_cache = s.get("disk_cache_budget_mb")
        new_poll = pending["poll_interval"]
        new_reconcile = pending["reconcile_interval"]
        new_cache = pending["disk_cache_budget_mb"]
        if (
            old_poll == new_poll
            and old_reconcile == new_reconcile
            and old_cache == new_cache
        ):
            if on_close is not None:
                on_close()
            return
        s.update(
            {
                "poll_interval": new_poll,
                "reconcile_interval": new_reconcile,
                "disk_cache_budget_mb": new_cache,
            }
        )
        if (
            new_cache < old_cache
            and parent is not None
            and hasattr(parent, "_show_toast")
        ):
            parent._show_toast(f"Disk cache will be pruned to {new_cache} MB")
            if hasattr(parent, "_prune_disk_body_cache"):
                parent._prune_disk_body_cache()
        if (
            old_poll != new_poll or old_reconcile != new_reconcile
        ) and parent is not None:
            app = (
                parent.get_application() if hasattr(parent, "get_application") else None
            )
            if app is not None and hasattr(app, "wake_background_updates"):
                app.wake_background_updates(reconcile=True)
        update_save_state()
        if on_close is not None:
            on_close()

    poll_spin.connect("value-changed", on_poll_changed)
    reconcile_spin.connect("value-changed", on_reconcile_changed)
    cache_spin.connect("value-changed", on_cache_changed)
    save_btn.connect("clicked", on_save)
    update_save_state()

    debug_section, debug_group = _make_settings_section("Debug")
    debug_row = Adw.SwitchRow(
        title="Verbose logging",
        subtitle="Print full tracebacks to stderr during development",
    )
    debug_row.set_active(s.get("debug_logging"))
    debug_row.connect(
        "notify::active", lambda r, _: s.set("debug_logging", r.get_active())
    )
    debug_group.add(debug_row)

    diagnostics_row = Adw.SwitchRow(
        title="Diagnostics capture",
        subtitle="Store a small redacted local diagnostics log for bug exports",
    )
    diagnostics_row.set_active(s.get("diagnostics_enabled"))
    diagnostics_row.connect(
        "notify::active", lambda r, _: s.set("diagnostics_enabled", r.get_active())
    )
    debug_group.add(diagnostics_row)

    export_row = Adw.ActionRow(
        title="Export diagnostics",
        subtitle="Write a redacted diagnostics zip to Downloads. No message bodies, attachments, or tokens are included.",
    )
    export_btn = Gtk.Button(label="Export")
    export_btn.add_css_class("flat")

    def on_export(_btn):
        try:
            try:
                from .diagnostics.export import export_diagnostics_bundle
            except ImportError:
                from diagnostics.export import export_diagnostics_bundle
            path = export_diagnostics_bundle()
        except Exception as exc:
            if parent is not None and hasattr(parent, "_show_toast"):
                parent._show_toast(f"Diagnostics export failed: {exc}")
            return
        if parent is not None and hasattr(parent, "_show_toast"):
            parent._show_toast(f"Diagnostics exported to {path}")

    export_btn.connect("clicked", on_export)
    export_row.add_suffix(export_btn)
    export_row.set_activatable(False)
    debug_group.add(export_row)
    main_page.append(debug_section)

    return root

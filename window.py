import base64
import collections
import json
import html as html_lib
import os
import re
import sys
import threading
import traceback
import time
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, GLib, WebKit, Pango, Gdk, Gio

try:
    from .backends import get_backends
    from .styles import CSS, build_window_account_css, account_class_for_index, build_theme_override_css
    from .settings import get_settings, get_disk_cache_budget_limit_mb
    from .window_mailbox_controller import MailboxControllerMixin
    from .window_message_cache import MessageListCacheMixin
    from .window_constants import (
        BODY_CACHE_LIMIT as _BODY_CACHE_LIMIT,
        PREFETCH_WARMUP_LIMIT as _PREFETCH_WARMUP_LIMIT,
        SIDEBAR_MIN_WIDTH as _SIDEBAR_MIN_WIDTH,
        SIDEBAR_MAX_WIDTH as _SIDEBAR_MAX_WIDTH,
        MESSAGE_LIST_MIN_WIDTH as _MESSAGE_LIST_MIN_WIDTH,
        MESSAGE_LIST_MAX_WIDTH as _MESSAGE_LIST_MAX_WIDTH,
        MESSAGE_PAGE_STEP as _MESSAGE_PAGE_STEP,
    )
    from .window_message_list import MessageListMixin
    from .window_reader_controller import ReaderControllerMixin
    from .window_reader import ReaderMixin, _inject_styles, _wrap_email_html_frame
    from .window_welcome import (
        WelcomeScreen,
        WelcomeSettingsShell,
        build_more_providers_dialog,
        hermod_app_icon_path,
    )
    from .settings_accounts import build_account_setup_dialog
    from .widgets import (
        EmailRow,
        ThreadNavRow,
        UnifiedRow,
        FolderRow,
        AccountHeaderRow,
        MoreFoldersRow,
        SidebarSectionRow,
        StartupStatusPanel,
        MailListItem,
        MessageListItem,
        LoadMoreListItem,
        LoadMoreRow,
    )
    from .thread_renderer import build_thread_html, thread_reply_msg_for_records
    from .body_cache import load_disk_body, store_disk_body, prune_disk_body_cache
    from .snapshot_cache import SnapshotSaveQueue
    from .utils import (
        _UNIFIED,
        _UNIFIED_TRASH,
        _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR,
        _format_date,
        _format_received_date,
        _thread_day_label,
        _format_size,
        _pick_icon_name,
        _log_exception,
        _body_cache_key,
        _disk_cache_budget_bytes,
        _snapshot_scope,
        _snapshot_path,
        _attachment_content_id,
        _attachment_is_inline_image,
        _attachment_cacheable,
        _inline_image_data_uri,
        _replace_cid_images,
        _thread_inline_image_records,
        _make_count_slot,
        _normalize_thread_subject,
        _html_to_text,
        _strip_thread_quotes,
        _thread_message_summary,
        _thread_day_label,
        _rgb_to_hex,
        _sender_key,
        _sender_initials,
        _thread_palette,
        _thread_color_map,
        _email_background_hint,
        _demo_thread_fixture,
        _perf_counter,
        _log_perf,
    )
except ImportError:
    from backends import get_backends
    from styles import CSS, build_window_account_css, account_class_for_index, build_theme_override_css
    from settings import get_settings, get_disk_cache_budget_limit_mb
    from window_mailbox_controller import MailboxControllerMixin
    from window_message_cache import MessageListCacheMixin
    from window_constants import (
        BODY_CACHE_LIMIT as _BODY_CACHE_LIMIT,
        PREFETCH_WARMUP_LIMIT as _PREFETCH_WARMUP_LIMIT,
        SIDEBAR_MIN_WIDTH as _SIDEBAR_MIN_WIDTH,
        SIDEBAR_MAX_WIDTH as _SIDEBAR_MAX_WIDTH,
        MESSAGE_LIST_MIN_WIDTH as _MESSAGE_LIST_MIN_WIDTH,
        MESSAGE_LIST_MAX_WIDTH as _MESSAGE_LIST_MAX_WIDTH,
        MESSAGE_PAGE_STEP as _MESSAGE_PAGE_STEP,
    )
    from window_message_list import MessageListMixin
    from window_reader_controller import ReaderControllerMixin
    from window_reader import ReaderMixin, _inject_styles, _wrap_email_html_frame
    from window_welcome import (
        WelcomeScreen,
        WelcomeSettingsShell,
        build_more_providers_dialog,
        hermod_app_icon_path,
    )
    from settings_accounts import build_account_setup_dialog
    from widgets import (
        EmailRow,
        ThreadNavRow,
        UnifiedRow,
        FolderRow,
        AccountHeaderRow,
        MoreFoldersRow,
        SidebarSectionRow,
        StartupStatusPanel,
        MailListItem,
        MessageListItem,
        LoadMoreListItem,
        LoadMoreRow,
    )
    from thread_renderer import build_thread_html, thread_reply_msg_for_records
    from body_cache import load_disk_body, store_disk_body, prune_disk_body_cache
    from snapshot_cache import SnapshotSaveQueue
    from utils import (
        _UNIFIED,
        _UNIFIED_TRASH,
        _UNIFIED_SPAM,
        _DISK_BODY_CACHE_DIR,
        _format_date,
        _format_received_date,
        _thread_day_label,
        _format_size,
        _pick_icon_name,
        _log_exception,
        _body_cache_key,
        _disk_cache_budget_bytes,
        _snapshot_scope,
        _snapshot_path,
        _attachment_content_id,
        _attachment_is_inline_image,
        _attachment_cacheable,
        _inline_image_data_uri,
        _replace_cid_images,
        _thread_inline_image_records,
        _make_count_slot,
        _normalize_thread_subject,
        _html_to_text,
        _strip_thread_quotes,
        _thread_message_summary,
        _thread_day_label,
        _rgb_to_hex,
        _sender_key,
        _sender_initials,
        _thread_palette,
        _thread_color_map,
        _email_background_hint,
        _demo_thread_fixture,
        _perf_counter,
        _log_perf,
    )

# ── Main window ───────────────────────────────────────────────────────────────


class _HeaderTitleStrip(Gtk.Box):
    """Left-packed header content: `H HERMOD` wordmark + folder crumb.

    Exposes ``set_title`` / ``set_subtitle`` so existing call sites
    (`window_message_list.py`) continue to work unchanged; the title
    becomes the folder crumb and the subtitle becomes a small muted
    suffix after it.
    """

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            valign=Gtk.Align.CENTER,
        )
        self.add_css_class("hermod-header-brand-row")

        brand = Gtk.Label(label="HERMOD")
        brand.add_css_class("hermod-header-brand-label")
        brand.set_valign(Gtk.Align.CENTER)
        self.append(brand)

        self._separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self._separator.add_css_class("hermod-header-separator")
        self._separator.set_margin_top(6)
        self._separator.set_margin_bottom(6)
        self._separator.set_margin_start(2)
        self._separator.set_margin_end(2)
        self.append(self._separator)

        self._title_lbl = Gtk.Label(label="", halign=Gtk.Align.START, xalign=0.0)
        self._title_lbl.add_css_class("hermod-header-crumb-title")
        self._title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._title_lbl.set_valign(Gtk.Align.CENTER)
        self.append(self._title_lbl)

        self._subtitle_lbl = Gtk.Label(label="", halign=Gtk.Align.START, xalign=0.0)
        self._subtitle_lbl.add_css_class("hermod-header-crumb-subtitle")
        self._subtitle_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._subtitle_lbl.set_valign(Gtk.Align.CENTER)
        self._subtitle_lbl.set_visible(False)
        self.append(self._subtitle_lbl)

    def _refresh_separator_visibility(self):
        has_crumb = bool(self._title_lbl.get_text()) or self._subtitle_lbl.get_visible()
        self._separator.set_visible(has_crumb)

    def set_title(self, text):
        self._title_lbl.set_label(text or "")
        self._refresh_separator_visibility()

    def set_subtitle(self, text):
        text = (text or "").strip()
        self._subtitle_lbl.set_label(text)
        self._subtitle_lbl.set_visible(bool(text))
        self._refresh_separator_visibility()


class HermodWindow(
    MailboxControllerMixin,
    MessageListCacheMixin,
    MessageListMixin,
    ReaderControllerMixin,
    ReaderMixin,
    Adw.ApplicationWindow,
):
    def __init__(self, app, backends):
        super().__init__(application=app, title="Hermod")
        self.set_default_size(1520, 920)
        self._install_app_icon_theme()
        self.backends = backends
        has_accounts = bool(backends)
        self.current_backend = None
        self.current_folder = None
        self._folder_rows = {}
        self._account_state = {}
        self._search_text = ""
        self._unread_counts = collections.defaultdict(
            lambda: {
                "inbox": 0, "trash": 0, "spam": 0,
                "drafts": 0, "sent": 0, "archive": 0, "flagged": 0,
            }
        )
        self._all_inboxes_row = None
        self._flagged_row = None
        self._drafts_row = None
        self._sent_row = None
        self._archive_row = None
        self._syncing = False
        self._sync_in_flight = False
        self._last_sync_at = None
        self._last_sync_had_errors = False
        self._sync_status_timer_id = None
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
        self._message_live_generation = 0
        self._body_load_generation = 0
        self._startup_autoselect_pending = has_accounts
        self._startup_status_active = has_accounts
        self._startup_status_complete_id = None
        self._startup_visible_ready = False
        self._startup_counts_ready = not has_accounts
        self._startup_counts_seen = set()
        self._startup_counts_warmup_started = False
        self._content_title = "Hermod"
        self._content_subtitle = ""
        self._account_classes = {
            b.identity: account_class_for_index(i) for i, b in enumerate(backends)
        }
        self._account_display_names = {
            b.identity: getattr(b, "presentation_name", "") or b.identity
            for b in backends
        }
        self._account_css = self._build_account_css()
        self._displayed_message_list_key = None
        self._snapshot_save_queue = SnapshotSaveQueue(error_logger=_log_exception)
        self._message_page_limit = _MESSAGE_PAGE_STEP
        self._message_has_more = False
        self._message_loading = False
        self._message_loading_generation = None
        self._pending_list_scroll_value = None
        self._pending_list_scroll_attempts = 0
        self._pending_list_scroll_watcher = None
        self._sort_order = "newest"
        self._show_unread_only = False
        self._filter_mode = "unified"
        self._filter_segmented_buttons = {}
        self._unread_filter_had_results = False
        self._thread_sidebar_open = False
        self._active_thread_id = None
        self._original_message_source = None
        self._thread_original_sources = {}

        self._apply_css()
        self._build_ui()
        self._populate_sidebar()
        if self.backends and hasattr(self, "_warm_startup_unread_counts"):
            self._warm_startup_unread_counts()
        self._setup_shortcuts()
        self.connect("close-request", self._on_close_request)

        force_welcome = os.environ.get("HERMOD_FORCE_WELCOME") == "1"
        if self.backends and not force_welcome:
            self._select_initial_folder_row()
            self._show_app_root()
        else:
            self._show_welcome_mode(reset_editor=False)

        self._update_sync_status_labels()
        self._diag_watchdog_id = GLib.timeout_add_seconds(5, self._diag_watchdog_tick)

    def _apply_css(self):
        s = get_settings()
        forced = os.environ.get("HERMOD_FORCE_THEME")
        theme = (forced or s.get("theme_mode") or "night").lower()
        try:
            sm = Adw.StyleManager.get_default()
            sm.set_color_scheme(
                Adw.ColorScheme.FORCE_LIGHT if theme == "day" else Adw.ColorScheme.FORCE_DARK
            )
        except Exception:
            pass
        theme_css = self._build_theme_override_css()
        provider = Gtk.CssProvider()
        full_css = CSS + self._account_css + theme_css
        provider.load_from_string(full_css)
        if getattr(self, "_style_provider", None) is not None:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    self.get_display(), self._style_provider
                )
            except Exception:
                pass
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._style_provider = provider

    def _build_theme_override_css(self):
        s = get_settings()
        forced = os.environ.get("HERMOD_FORCE_THEME")
        return build_theme_override_css(
            theme=(forced or s.get("theme_mode") or "night").lower(),
            day_variant=s.get("day_variant") or "paper",
            accent=s.get("accent") or "teal",
            density=s.get("density") or "balanced",
        )

    def apply_theme(self):
        self._apply_css()

    def _build_account_css(self):
        return build_window_account_css(self.backends)

    def _install_app_icon_theme(self):
        display = self.get_display()
        if display is None:
            return
        theme = Gtk.IconTheme.get_for_display(display)
        icon_root = hermod_app_icon_path().parents[3]
        icon_root_str = str(icon_root)
        try:
            search_path = list(theme.get_search_path() or [])
        except Exception:
            search_path = []
        if icon_root_str not in search_path:
            theme.add_search_path(icon_root_str)
        self.set_icon_name("io.github.hermod.Hermod")
        Gtk.Window.set_default_icon_name("io.github.hermod.Hermod")

    def _on_style_scheme_changed(self):
        self._update_webview_bg()

    def _account_class_for(self, identity):
        return self._account_classes.get(identity)

    def _account_display_name_for(self, identity):
        return self._account_display_names.get(identity, identity)

    def refresh_account_chrome(self):
        self._account_display_names = {
            b.identity: getattr(b, "presentation_name", "") or b.identity
            for b in self.backends
        }
        self._account_css = self._build_account_css()
        self._apply_css()
        if hasattr(self, "_startup_status_panel"):
            self._startup_status_panel.set_backends(self.backends)
        if hasattr(self, "folder_list"):
            for backend in self.backends:
                state = getattr(self, "_account_state", {}).get(backend.identity)
                if not state:
                    continue
                header = state.get("header")
                if header is not None and hasattr(header, "set_label"):
                    header.set_label(self._account_display_name_for(backend.identity))

    def _has_accounts(self):
        return bool(getattr(self, "backends", None))

    def _reset_startup_state(self, active=None):
        active = self._has_accounts() if active is None else bool(active)
        if getattr(self, "_startup_status_complete_id", None):
            GLib.source_remove(self._startup_status_complete_id)
            self._startup_status_complete_id = None
        self._startup_autoselect_pending = active
        self._startup_status_active = active
        self._startup_visible_ready = False
        self._startup_counts_ready = not active
        self._startup_counts_seen = set()
        self._startup_counts_warmup_started = False
        panel = getattr(self, "_startup_status_panel", None)
        if panel is not None:
            panel.set_backends(self.backends)
            if active:
                panel.set_title(
                    "Hermod is waking your mail",
                    "Loading mail, refreshing counts, and restoring the first view.",
                )
                panel.set_all_pending()
        if (
            not active
            and getattr(self, "_viewer_stack", None) is not None
            and self._viewer_stack.get_visible_child_name() == "startup-status"
        ):
            self._viewer_stack.set_visible_child_name("viewer")

    def _enter_account_startup_mode(self):
        if not self._has_accounts():
            self._reset_startup_state(active=False)
            return
        self._reset_startup_state(active=True)
        if getattr(self, "_viewer_stack", None) is not None:
            self._viewer_stack.set_visible_child_name("startup-status")

    def _select_initial_folder_row(
        self, selected_backend_id=None, selected_folder=None
    ):
        if not self._has_accounts() or not hasattr(self, "folder_list"):
            return None
        target_row = None
        if selected_backend_id is not None and selected_folder is not None:
            target_row = self._folder_rows.get((selected_backend_id, selected_folder))
        if target_row is None and self._all_inboxes_row is not None:
            target_row = self._all_inboxes_row
        if target_row is None and self.backends:
            first_backend = self.backends[0]
            target_row = self._folder_rows.get(
                (first_backend.identity, first_backend.FOLDERS[0][0])
            )
        if target_row is not None:
            self.folder_list.select_row(target_row)
        return target_row

    def _show_app_root(self):
        header = getattr(self, "_header_bar", None)
        if header is not None:
            header.set_visible(True)
        root_stack = getattr(self, "_root_mode_stack", None)
        if root_stack is not None:
            root_stack.set_visible_child_name("app")

    def _present_settings_modal(self):
        content = getattr(self, "_app_settings_content", None)
        if content is None:
            return
        win = getattr(self, "_settings_window", None)
        if win is None:
            win = Gtk.Window(transient_for=self, modal=True)
            win.set_decorated(False)
            win.set_default_size(1020, 680)
            win.add_css_class("preferences-window")
            parent = content.get_parent()
            if parent is not None:
                try:
                    parent.remove(content)
                except Exception:
                    pass
            win.set_child(content)
            win.connect("close-request", self._on_settings_modal_close_request)
            self._settings_window = win
        show_main = getattr(content, "show_accounts_main", None)
        if callable(show_main):
            show_main()
        select_pane = getattr(content, "select_pane", None)
        if callable(select_pane):
            select_pane("accounts")
        win.present()

    def _close_settings_modal(self):
        win = getattr(self, "_settings_window", None)
        if win is not None:
            win.set_visible(False)

    def _on_settings_modal_close_request(self, _win):
        self._close_settings_modal()
        return True

    def _show_welcome_settings_view(self):
        header = getattr(self, "_header_bar", None)
        if header is not None:
            header.set_visible(False)
        settings_view = getattr(self, "_welcome_settings_content", None)
        show_main = getattr(settings_view, "show_accounts_main", None)
        if callable(show_main):
            show_main()
        root_stack = getattr(self, "_root_mode_stack", None)
        if root_stack is not None:
            root_stack.set_visible_child_name("welcome-settings")

    def _build_welcome_settings_content(self):
        try:
            from .settings import build_settings_content
        except ImportError:
            from settings import build_settings_content
        content = build_settings_content(
            self,
            on_close=self._show_welcome_mode,
            on_back=self._show_welcome_mode,
            scrollable=False,
        )
        controller = getattr(content, "account_controller", None)
        if controller is not None:
            controller.editor_done_callback = self._show_welcome_mode
        return content

    def _refresh_onboarding_hub(self):
        screen = getattr(self, "_welcome_screen", None)
        if screen is not None and hasattr(screen, "refresh_accounts"):
            screen.refresh_accounts(self.backends)

    def _onboarding_provider_selected(self, provider_kind="imap-smtp"):
        provider_kind = str(provider_kind or "imap-smtp").strip().lower() or "imap-smtp"
        if provider_kind == "more-providers":
            dialog = build_more_providers_dialog(
                self, on_pick=self._onboarding_provider_selected
            )
            dialog.present()
            return
        dialog = build_account_setup_dialog(
            self,
            provider_kind,
            on_saved=self._show_welcome_mode,
        )

    def _show_welcome_account_setup(self, provider_kind="imap-smtp"):
        self._onboarding_provider_selected(provider_kind)

    def _show_welcome_mode(self, reset_editor=True):
        header = getattr(self, "_header_bar", None)
        if header is not None:
            header.set_visible(False)
            header.remove_css_class("welcome-header-bar")
        self._reset_startup_state(active=False)
        self.current_backend = None
        self.current_folder = None
        if reset_editor:
            settings_view = getattr(self, "_welcome_settings_content", None)
            show_main = getattr(settings_view, "show_accounts_main", None)
            if callable(show_main):
                show_main()
        root_stack = getattr(self, "_root_mode_stack", None)
        if root_stack is not None:
            root_stack.set_visible_child_name("welcome")
        self._refresh_onboarding_hub()

    def reload_backends(self):
        had_accounts = self._has_accounts()
        selected_backend_id = getattr(self.current_backend, "identity", None)
        selected_folder = self.current_folder
        current_root = None
        if getattr(self, "_root_mode_stack", None) is not None:
            try:
                current_root = self._root_mode_stack.get_visible_child_name()
            except Exception:
                current_root = None
        self.backends = get_backends()
        self._account_classes = {
            b.identity: account_class_for_index(i) for i, b in enumerate(self.backends)
        }
        self.refresh_account_chrome()
        self._refresh_onboarding_hub()
        if hasattr(self, "folder_list"):
            child = self.folder_list.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                self.folder_list.remove(child)
                child = next_child
            self._folder_rows.clear()
            self._account_state.clear()
            self._all_inboxes_row = None
            self._populate_sidebar()
            if self._compose_view is not None and hasattr(
                self._compose_view, "refresh_account_labels"
            ):
                self._compose_view.refresh_account_labels()
            if not self.backends:
                self._show_welcome_mode()
                return
            if current_root in {"welcome", "welcome-settings"}:
                self._refresh_onboarding_hub()
                return
            self._show_app_root()
            if not had_accounts:
                self._enter_account_startup_mode()
                if hasattr(self, "_warm_startup_unread_counts"):
                    self._warm_startup_unread_counts()
            target_row = self._select_initial_folder_row(
                selected_backend_id, selected_folder
            )
            if target_row is None:
                self.refresh_visible_mail(force=True, preserve_selected=True)

    def _on_close_request(self, *_):
        if self._close_after_compose_prompt:
            self._close_after_compose_prompt = False
            if self._diag_watchdog_id is not None:
                GLib.source_remove(self._diag_watchdog_id)
                self._diag_watchdog_id = None
            self._arm_close_hard_exit()
            return False
        if self._compose_active():
            self._compose_view.request_close(
                lambda proceed: GLib.idle_add(
                    self._finish_window_close_request, bool(proceed)
                )
            )
            return True
        if get_settings().get("close_minimizes"):
            self.hide()
            return True
        if self._diag_watchdog_id is not None:
            GLib.source_remove(self._diag_watchdog_id)
            self._diag_watchdog_id = None
        # Belt-and-braces: Adw.ApplicationWindow normally triggers
        # app shutdown when the last window closes, but if a GLib
        # source or stuck background thread keeps the main loop
        # from draining, the process will sit at 100% CPU forever.
        # Arm a short os._exit() fallback in a daemon thread.
        app = self.get_application()
        if app is not None:
            try:
                app.quit()
            except Exception:
                pass
        self._arm_close_hard_exit()
        return False

    def _on_list_stack_visible_child(self, stack, _pspec):
        spinner = getattr(self, "_list_loading_spinner", None)
        if spinner is None:
            return
        visible = stack.get_visible_child_name()
        if visible == "loading":
            spinner.start()
        else:
            spinner.stop()

    def _arm_close_hard_exit(self, grace_s=5.0):
        if getattr(self, "_hard_exit_armed", False):
            return
        self._hard_exit_armed = True

        def _force():
            import os as _os
            import time as _time
            _time.sleep(max(0.5, float(grace_s)))
            _os._exit(0)

        threading.Thread(target=_force, daemon=True).start()

    def _finish_window_close_request(self, proceed):
        if proceed:
            if get_settings().get("close_minimizes"):
                self.hide()
            else:
                self._close_after_compose_prompt = True
                self.close()
        return False

    def _compose_active(self):
        return (
            self._compose_view is not None
            and self._viewer_stack.get_visible_child_name() == "compose"
        )

    def _flash_action_feedback(self, widget):
        if widget is None:
            return
        widget.add_css_class("action-feedback")

        def clear():
            try:
                widget.remove_css_class("action-feedback")
            except Exception:
                pass
            return False

        GLib.timeout_add(120, clear)

    def _close_inline_compose(self, _compose=None):
        while child := self._compose_holder.get_first_child():
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
        if not get_settings().get("debug_logging"):
            return None
        token = object()
        with self._diag_lock:
            self._diag_ops[token] = {
                "kind": kind,
                "detail": detail,
                "hint": hint,
                "started": _perf_counter(),
                "warned": False,
            }
        return token

    def _end_background_op(self, token):
        if token is None:
            return
        with self._diag_lock:
            op = self._diag_ops.pop(token, None)
        if op is not None:
            _log_perf(op["kind"], op["detail"], started=op["started"])

    def _diag_watchdog_tick(self):
        if not get_settings().get("debug_logging"):
            return GLib.SOURCE_CONTINUE
        now = _perf_counter()
        stale = []
        with self._diag_lock:
            for op in self._diag_ops.values():
                age = now - op["started"]
                if age >= 15 and not op["warned"]:
                    op["warned"] = True
                    stale.append((op["kind"], op["detail"], op.get("hint"), age))
        for kind, detail, hint, age in stale:
            print(
                f"Watchdog: {kind} still running after {age:.1f}s ({detail})"
                + (f" | check: {hint}" if hint else ""),
                file=sys.stderr,
            )
        return GLib.SOURCE_CONTINUE

    def _open_command_palette(self):
        palette = getattr(self, "_command_palette", None)
        if palette is None:
            try:
                from command_palette import CommandPalette
            except ImportError:
                from .command_palette import CommandPalette
            palette = CommandPalette(self)
            self._command_palette = palette
        palette.open()

    def _build_ui(self):
        app_root = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.add_css_class("hermod-header")
        header.set_hexpand(True)
        self._header_bar = header
        self.title_widget = _HeaderTitleStrip()
        # Empty centered title so the brand strip we pack on the left
        # is the only textual content in the header.
        header.set_title_widget(Gtk.Box())
        header.pack_start(self.title_widget)

        # ── Right side: sync indicator + settings gear ──
        self._header_sync_btn = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text="Sync now (F5)"
        )
        self._header_sync_btn.add_css_class("flat")
        self._header_sync_btn.add_css_class("hermod-header-sync")
        self._header_sync_btn.connect("clicked", self._on_sync)
        settings_btn = Gtk.Button(
            icon_name="emblem-system-symbolic", tooltip_text="Settings"
        )
        settings_btn.add_css_class("flat")
        settings_btn.add_css_class("hermod-header-settings")
        settings_btn.connect("clicked", self._on_settings)
        self._settings_btn = settings_btn
        # pack_end stacks right-to-left — settings sits closest to CSD,
        # sync sits immediately to its left.
        header.pack_end(settings_btn)
        header.pack_end(self._header_sync_btn)

        # Sync control: refresh icon on the left, background status on the right.
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
        left_box.add_css_class("sync-left-side")
        left_box.set_size_request(38, 30)
        left_box.set_hexpand(False)
        left_box.set_vexpand(False)
        self._sync_icon = Gtk.Image(icon_name="view-refresh-symbolic")
        self._sync_icon.add_css_class("sync-online-icon")
        left_box.set_center_widget(self._sync_icon)
        online_box.append(left_box)

        sync_divider = Gtk.Box()
        sync_divider.add_css_class("sync-divider")
        sync_divider.set_size_request(1, 30)
        sync_divider.set_hexpand(False)
        sync_divider.set_vexpand(False)
        sync_divider.set_valign(Gtk.Align.FILL)
        online_box.append(sync_divider)

        right_box = Gtk.CenterBox(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        right_box.add_css_class("sync-right-side")
        right_box.set_size_request(81, 30)
        right_box.set_hexpand(False)
        right_box.set_vexpand(False)
        right_stack = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        self._countdown_hint_lbl = Gtk.Label(label="")
        self._countdown_hint_lbl.set_visible(False)

        self._countdown_lbl = Gtk.Label(label="ONLINE")
        self._countdown_lbl.add_css_class("sync-auto-value")
        self._countdown_lbl.set_hexpand(True)
        self._countdown_lbl.set_halign(Gtk.Align.CENTER)
        self._countdown_lbl.set_xalign(0.5)
        self._countdown_lbl.set_width_chars(9)
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
        self._sync_offline_lbl = Gtk.Label(label="Offline")
        self._sync_offline_lbl.add_css_class("sync-offline-label")
        offline_box.append(self._sync_offline_lbl)

        self._sync_state_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        self._sync_state_stack.set_hexpand(False)
        self._sync_state_stack.set_vexpand(False)
        self._sync_state_stack.add_named(online_box, "online")
        self._sync_state_stack.add_named(offline_box, "offline")
        self._sync_state_stack.set_visible_child_name("online")

        sync_overlay = Gtk.Overlay()
        sync_overlay.set_hexpand(True)
        sync_overlay.set_vexpand(False)
        sync_overlay.set_halign(Gtk.Align.FILL)
        sync_overlay.set_valign(Gtk.Align.CENTER)
        self._sync_btn = Gtk.Button(
            child=self._sync_state_stack, tooltip_text="Sync now (F5)"
        )
        self._sync_btn.add_css_class("sync-control")
        self._sync_btn.add_css_class("sidebar-action-btn")
        self._sync_btn.add_css_class("sync-online")
        self._sync_btn.set_hexpand(True)
        self._sync_btn.set_vexpand(False)
        self._sync_btn.set_halign(Gtk.Align.FILL)
        self._sync_btn.set_valign(Gtk.Align.CENTER)
        self._sync_btn.set_focusable(False)
        self._sync_btn.connect("clicked", self._on_sync)
        sync_overlay.set_child(self._sync_btn)

        self._sync_badge = Gtk.Label()
        self._sync_badge.add_css_class("sync-badge")
        self._sync_badge.set_halign(Gtk.Align.END)
        self._sync_badge.set_valign(Gtk.Align.START)
        self._sync_badge.set_visible(False)
        sync_overlay.add_overlay(self._sync_badge)

        compose_inner = Gtk.CenterBox(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        compose_inner.set_size_request(-1, 30)
        compose_stack = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
        )
        compose_stack.set_margin_start(4)
        compose_stack.append(
            Gtk.Image(
                icon_name=_pick_icon_name(
                    "hermod-pencil-symbolic", "mail-message-new-symbolic"
                ),
                pixel_size=14,
            )
        )
        compose_lbl = Gtk.Label(label="Compose")
        compose_lbl.add_css_class("sidebar-compose-label")
        compose_stack.append(compose_lbl)
        compose_inner.set_start_widget(compose_stack)
        compose_chip = Gtk.Label(label="Ctrl N", valign=Gtk.Align.CENTER)
        compose_chip.add_css_class("sidebar-compose-chip")
        compose_inner.set_end_widget(compose_chip)
        compose_overlay = Gtk.Overlay()
        compose_overlay.set_hexpand(True)
        compose_overlay.set_vexpand(False)
        compose_overlay.set_halign(Gtk.Align.FILL)
        compose_overlay.set_valign(Gtk.Align.CENTER)
        compose_btn = Gtk.Button(child=compose_inner, tooltip_text="Compose (c)")
        compose_btn.add_css_class("sidebar-compose-btn")
        compose_btn.add_css_class("sidebar-action-btn")
        compose_btn.set_hexpand(True)
        compose_btn.set_vexpand(False)
        compose_btn.set_halign(Gtk.Align.FILL)
        compose_btn.set_valign(Gtk.Align.CENTER)
        compose_btn.set_focusable(False)
        compose_btn.connect("clicked", self._on_compose)
        self._compose_btn = compose_btn
        compose_overlay.set_child(compose_btn)

        app_root.add_top_bar(header)

        # Body
        body = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True
        )

        sidebar_col = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, width_request=_SIDEBAR_MIN_WIDTH
        )
        sidebar_col.add_css_class("hermod-sidebar-column")
        sidebar_col.set_size_request(_SIDEBAR_MIN_WIDTH, -1)
        sidebar_col.set_hexpand(False)
        sidebar_col.set_halign(Gtk.Align.START)
        sidebar_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            homogeneous=True,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.START,
        )
        sidebar_actions.add_css_class("sidebar-actions")
        sidebar_actions.set_size_request(_SIDEBAR_MIN_WIDTH, -1)
        sidebar_actions.set_hexpand(False)
        sidebar_actions.set_vexpand(False)
        compose_overlay.set_hexpand(True)
        compose_overlay.set_halign(Gtk.Align.FILL)
        # Sync moved into the main header (sub-phase A); the sync widget
        # constructed above stays alive but unparented so the state
        # plumbing in window_message_list.py (ONLINE/SYNCING/OFFLINE
        # label, new-mail badge) keeps working without a second visible
        # sync surface in the sidebar.
        sidebar_actions.append(compose_overlay)
        sidebar_col.append(sidebar_actions)

        sidebar_search = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        sidebar_search.add_css_class("sidebar-search")
        sidebar_search_icon = Gtk.Image(
            icon_name=_pick_icon_name(
                "hermod-search-symbolic", "system-search-symbolic"
            )
        )
        sidebar_search_icon.add_css_class("sidebar-search-icon")
        sidebar_search.append(sidebar_search_icon)
        self._search_entry = Gtk.Entry(
            placeholder_text="Search mail…", hexpand=True
        )
        self._search_entry.add_css_class("sidebar-search-entry")
        self._search_entry.connect("changed", self._on_search_changed)
        sidebar_search.append(self._search_entry)
        sidebar_search_kbd = Gtk.Label(label="Ctrl K")
        sidebar_search_kbd.add_css_class("sidebar-search-kbd")
        sidebar_search.append(sidebar_search_kbd)
        sidebar_col.append(sidebar_search)

        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            width_request=_SIDEBAR_MIN_WIDTH,
            vexpand=True,
        )
        self.folder_list = Gtk.ListBox()
        self.folder_list.add_css_class("navigation-sidebar")
        self.folder_list.connect("row-selected", self._on_folder_selected)
        self.folder_list.connect("row-activated", self._on_row_activated)
        sidebar_scroll.set_child(self.folder_list)
        sidebar_col.append(sidebar_scroll)

        sidebar_status = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        sidebar_status.add_css_class("sidebar-status")
        sidebar_synced_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        sidebar_synced_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        sidebar_synced_dot.set_size_request(8, 8)
        sidebar_synced_dot.add_css_class("sidebar-status-dot")
        sidebar_synced_dot.add_css_class("sidebar-status-dot-pending")
        self._sidebar_synced_dot = sidebar_synced_dot
        sidebar_synced_row.append(sidebar_synced_dot)
        self._sidebar_synced_lbl = Gtk.Label(
            label="All synced", halign=Gtk.Align.START, hexpand=True, xalign=0.0
        )
        self._sidebar_synced_lbl.add_css_class("sidebar-status-label")
        sidebar_synced_row.append(self._sidebar_synced_lbl)
        self._sidebar_synced_age_lbl = Gtk.Label(
            label="just now", halign=Gtk.Align.END, xalign=1.0
        )
        self._sidebar_synced_age_lbl.add_css_class("sidebar-status-age")
        sidebar_synced_row.append(self._sidebar_synced_age_lbl)
        sidebar_status.append(sidebar_synced_row)

        sidebar_local_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        sidebar_local_icon = Gtk.Image(
            icon_name=_pick_icon_name(
                "hermod-cpu-symbolic", "computer-symbolic"
            )
        )
        sidebar_local_icon.add_css_class("sidebar-local-icon")
        sidebar_local_row.append(sidebar_local_icon)
        sidebar_local_lbl = Gtk.Label(
            label="Local model", halign=Gtk.Align.START, hexpand=True, xalign=0.0
        )
        sidebar_local_lbl.add_css_class("sidebar-status-label")
        sidebar_local_row.append(sidebar_local_lbl)
        sidebar_local_dot = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        sidebar_local_dot.set_size_request(8, 8)
        sidebar_local_dot.add_css_class("sidebar-status-dot")
        sidebar_local_dot.add_css_class("sidebar-status-dot-online")
        sidebar_local_row.append(sidebar_local_dot)
        sidebar_status.append(sidebar_local_row)
        sidebar_col.append(sidebar_status)
        body.append(sidebar_col)

        right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        right.add_css_class("content-split")
        right.set_position(380)
        right.set_shrink_start_child(False)
        right.set_shrink_end_child(False)
        right.set_resize_start_child(False)
        right.set_resize_end_child(True)
        self._content_paned = right
        right.connect("notify::position", self._on_content_paned_position_changed)

        list_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=380)
        list_col.add_css_class("message-column")

        # Column header: eyebrow + meta + segmented Unified/Unread/Flagged filter.
        column_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=4)
        column_header.add_css_class("message-column-header")

        header_top = Gtk.CenterBox(hexpand=True)
        eyebrow_lbl = Gtk.Label(label="ALL INBOXES", halign=Gtk.Align.START, xalign=0.0)
        eyebrow_lbl.add_css_class("message-column-eyebrow")
        header_top.set_start_widget(eyebrow_lbl)
        self._message_col_eyebrow = eyebrow_lbl

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        filter_row.add_css_class("message-filter-segmented")
        filter_row.set_valign(Gtk.Align.CENTER)
        self._filter_segmented_buttons = {}
        filter_specs = (
            ("unified", "Unified"),
            ("unread", "Unread"),
            ("flagged", "Flagged"),
        )
        group_leader = None
        for mode_key, caption in filter_specs:
            btn = Gtk.ToggleButton(label=caption)
            btn.add_css_class("message-filter-chip")
            btn.add_css_class("flat")
            if group_leader is None:
                group_leader = btn
                btn.set_active(True)
                btn.add_css_class("selected")
            else:
                btn.set_group(group_leader)
            btn.connect(
                "toggled",
                lambda b, key=mode_key: (
                    self.set_filter_mode(key) if b.get_active() else None
                ),
            )
            self._filter_segmented_buttons[mode_key] = btn
            filter_row.append(btn)
        header_top.set_end_widget(filter_row)
        column_header.append(header_top)

        meta_lbl = Gtk.Label(label="", halign=Gtk.Align.START, xalign=0.0)
        meta_lbl.add_css_class("message-column-meta")
        column_header.append(meta_lbl)
        self._message_col_meta = meta_lbl

        list_col.append(column_header)
        self._message_column_header = column_header

        # Legacy sort / unread state lives on invisible stub widgets so existing
        # callers (tests, keyboard shortcuts) keep working without cluttering the
        # redesigned column header.
        self._load_older_btn = Gtk.Button(label="Load older")
        self._load_older_btn.add_css_class("load-older-toolbar")
        self._load_older_btn.add_css_class("flat")
        self._load_older_btn.set_visible(False)
        self._load_older_btn.connect(
            "clicked", lambda *_: self._on_load_more_requested()
        )

        self._sort_toggle_btn = Gtk.Button()
        self._sort_toggle_btn.add_css_class("sorting-toggle")
        self._sort_toggle_btn.add_css_class("active")
        self._sort_toggle_icon = Gtk.Image()
        self._sort_toggle_btn.set_child(self._sort_toggle_icon)
        self._sort_toggle_btn.connect("clicked", lambda _: self._toggle_sort_order())
        self._sort_toggle_btn.set_visible(False)
        self._sync_sort_toggle_button()

        self._unread_toggle_btn = Gtk.ToggleButton()
        self._unread_toggle_btn.add_css_class("sorting-toggle")
        self._unread_toggle_btn.add_css_class("unread-toggle")
        self._unread_toggle_icon = Gtk.Image(icon_name="mail-unread-symbolic")
        self._unread_toggle_btn.set_child(self._unread_toggle_icon)
        self._unread_toggle_btn.set_tooltip_text("Unread only")
        self._unread_toggle_btn.set_visible(False)
        self._unread_toggle_btn.connect(
            "toggled", lambda btn: self._toggle_unread_only(btn.get_active())
        )
        self._sync_unread_toggle_button()

        self._search_bar = None

        self._list_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        email_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._email_scroll = email_scroll
        self._message_store = Gio.ListStore.new(MailListItem)
        self._message_filter = Gtk.CustomFilter.new(self._email_filter)
        self._filtered_message_model = Gtk.FilterListModel.new(
            self._message_store, self._message_filter
        )
        self._message_selection = Gtk.SingleSelection.new(self._filtered_message_model)
        self._message_selection.set_autoselect(False)
        self._message_selection.set_can_unselect(True)
        self._message_selection.connect(
            "notify::selected-item", self._on_email_selected
        )
        self._email_factory = Gtk.SignalListItemFactory.new()
        self._email_factory.connect("setup", self._setup_email_list_item)
        self._email_factory.connect("bind", self._bind_email_list_item)
        self._email_factory.connect("unbind", self._unbind_email_list_item)
        self.email_list = Gtk.ListView.new(self._message_selection, self._email_factory)
        self.email_list.add_css_class("message-list-view")
        self.email_list.set_single_click_activate(True)
        self.email_list.connect("activate", self._on_email_list_activated)
        email_scroll.set_child(self.email_list)

        loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True
        )
        # Gtk.Spinner runs a 60fps internal tick as long as spinning=True.
        # Keep it off by default and only enable it while the "loading"
        # stack child is the visible one — otherwise a wedged sync leaves
        # the spinner churning the main loop at full frame rate (100% CPU).
        spinner = Gtk.Spinner(spinning=False, halign=Gtk.Align.CENTER, margin_top=60)
        spinner.set_size_request(32, 32)
        loading_box.append(spinner)
        self._list_loading_spinner = spinner

        self._empty_page = Adw.StatusPage(
            icon_name="mail-inbox-symbolic", title="No messages"
        )

        self._list_stack.add_named(email_scroll, "list")
        self._list_stack.add_named(loading_box, "loading")
        self._list_stack.add_named(self._empty_page, "empty")
        self._list_stack.set_visible_child_name("loading")
        self._list_stack.connect(
            "notify::visible-child-name", self._on_list_stack_visible_child
        )
        # Start the spinner synchronised with the initial "loading" child
        # so the first paint already has it animating.
        spinner.start()

        list_col.append(self._list_stack)
        right.set_start_child(list_col)

        # Viewer: webview + attachment bar
        viewer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        wk_settings = WebKit.Settings()
        wk_settings.set_enable_javascript(False)
        wk_settings.set_auto_load_images(get_settings().get("load_images"))
        wk_settings.set_enable_write_console_messages_to_stdout(False)
        self._webview_settings = wk_settings
        self._current_body = None

        self.webview = WebKit.WebView(vexpand=True, hexpand=True)

        self._message_info_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._message_info_bar.add_css_class("message-info-bar")
        self._message_info_bar.add_css_class("reader-header")
        self._message_info_top = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.START,
        )
        self._message_info_top.add_css_class("message-info-top")
        self._message_info_accent = Gtk.Box()
        self._message_info_accent.set_visible(False)
        self._message_info_subject = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_subject.add_css_class("message-info-subject")
        self._message_info_subject.add_css_class("reader-subject")
        self._message_info_subject.set_wrap(True)
        self._message_info_subject.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._message_info_subject.set_lines(2)
        self._message_info_subject.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_subject.set_hexpand(True)
        self._message_info_subject.set_valign(Gtk.Align.START)
        self._message_info_top.append(self._message_info_subject)

        # Top-right action cluster: reply / reply-all / forward, then
        # secondary actions (Original, thread-toggle, delete).
        self._info_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._info_actions.add_css_class("reader-actions")
        self._info_actions.set_valign(Gtk.Align.START)
        self._info_actions.set_visible(False)

        self._reader_reply_btn = Gtk.Button(
            icon_name=_pick_icon_name(
                "hermod-reply-symbolic", "mail-reply-sender-symbolic"
            ),
            tooltip_text="Reply (r)",
        )
        self._reader_reply_btn.add_css_class("flat")
        self._reader_reply_btn.add_css_class("reader-action-btn")
        self._reader_reply_btn.connect("clicked", lambda _: self._on_current_reply())
        self._info_actions.append(self._reader_reply_btn)

        self._reader_reply_all_btn = Gtk.Button(
            icon_name=_pick_icon_name(
                "hermod-reply-all-symbolic", "mail-reply-all-symbolic"
            ),
            tooltip_text="Reply all (a)",
        )
        self._reader_reply_all_btn.add_css_class("flat")
        self._reader_reply_all_btn.add_css_class("reader-action-btn")
        self._reader_reply_all_btn.connect(
            "clicked", lambda _: self._on_current_reply_all()
        )
        self._info_actions.append(self._reader_reply_all_btn)

        self._reader_forward_btn = Gtk.Button(
            icon_name=_pick_icon_name(
                "hermod-forward-symbolic", "mail-forward-symbolic"
            ),
            tooltip_text="Forward (f)",
        )
        self._reader_forward_btn.add_css_class("flat")
        self._reader_forward_btn.add_css_class("reader-action-btn")
        self._reader_forward_btn.connect(
            "clicked", lambda _: self._on_current_forward()
        )
        self._info_actions.append(self._reader_forward_btn)

        self._reader_delete_btn = Gtk.Button(
            icon_name=_pick_icon_name(
                "hermod-trash-symbolic", "user-trash-symbolic"
            ),
            tooltip_text="Delete (d)",
        )
        self._reader_delete_btn.add_css_class("flat")
        self._reader_delete_btn.add_css_class("reader-action-btn")
        self._reader_delete_btn.connect(
            "clicked", lambda _: self._on_current_delete()
        )
        self._info_actions.append(self._reader_delete_btn)

        _thread_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=5, valign=Gtk.Align.CENTER
        )
        self._thread_messages_icon = Gtk.Image(icon_name="view-list-symbolic")
        self._thread_messages_count_lbl = Gtk.Label(label="")
        self._thread_messages_count_lbl.add_css_class("thread-msg-count")
        _thread_btn_box.append(self._thread_messages_icon)
        _thread_btn_box.append(self._thread_messages_count_lbl)
        self._thread_messages_btn = Gtk.Button(
            child=_thread_btn_box, tooltip_text="Toggle message list"
        )
        self._thread_messages_btn.add_css_class("flat")
        self._thread_messages_btn.add_css_class("reader-action-btn")
        self._thread_messages_btn.add_css_class("reader-thread-btn")
        self._thread_messages_btn.set_visible(False)
        self._thread_messages_btn.connect(
            "clicked",
            lambda *_: self._set_thread_sidebar_visible(
                not getattr(self, "_thread_sidebar_revealer", None).get_reveal_child()
                if getattr(self, "_thread_sidebar_revealer", None) is not None
                else True
            ),
        )
        self._info_actions.append(self._thread_messages_btn)

        self._message_info_top.append(self._info_actions)

        self._message_info_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
        )
        self._message_info_actions.add_css_class("message-info-actions")
        self._message_info_original_btn = Gtk.Button(label="Original")
        self._message_info_original_btn.add_css_class("thread-info-button")
        self._message_info_original_btn.add_css_class("flat")
        self._message_info_original_btn.set_visible(False)
        self._message_info_original_btn.connect(
            "clicked", self._show_original_message_dialog
        )
        self._message_info_actions.append(self._message_info_original_btn)
        self._message_info_top.append(self._message_info_actions)
        self._message_info_bar.append(self._message_info_top)

        # New reader meta line: "N messages · participants" (threads) or
        # "sender · date" (single message). Replaces the older sender + date
        # + size/attachment stack from the pre-design layout.
        self._reader_meta_lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._reader_meta_lbl.add_css_class("reader-meta")
        self._reader_meta_lbl.set_wrap(False)
        self._reader_meta_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._reader_meta_lbl.set_hexpand(True)
        self._message_info_bar.append(self._reader_meta_lbl)

        # Legacy sender / date / meta labels stay in the widget tree (so
        # existing callers that poke their labels keep working) but are
        # hidden behind the new `reader-meta` summary.
        self._message_info_sender = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_sender.add_css_class("message-info-sender")
        self._message_info_sender.add_css_class("message-info-sender-line")
        self._message_info_sender.set_wrap(False)
        self._message_info_sender.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_sender.set_hexpand(True)
        self._message_info_sender.set_visible(False)

        self._message_info_date = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_date.add_css_class("message-info-date")
        self._message_info_date.set_wrap(False)
        self._message_info_date.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_date.set_visible(False)

        self._message_info_meta = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self._message_info_meta.add_css_class("message-info-meta")
        self._message_info_meta.set_wrap(False)
        self._message_info_meta.set_ellipsize(Pango.EllipsizeMode.END)
        self._message_info_meta.set_visible(False)
        self._message_info_bar.set_visible(False)

        self.webview.set_settings(wk_settings)
        self.webview.connect("decide-policy", self._on_webview_decide_policy)
        self.webview.connect("load-changed", self._on_webview_load_changed)
        viewer_box.append(self._message_info_bar)

        att_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        att_bar.add_css_class("attachment-bar")
        att_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_bottom=6
        )
        att_header.append(
            Gtk.Image(
                icon_name=_pick_icon_name(
                    "mail-attachment-symbolic", "paperclip-symbolic"
                )
            )
        )
        att_title = Gtk.Label(label="Attachments", halign=Gtk.Align.START)
        att_title.add_css_class("dim-label")
        att_header.append(att_title)
        att_bar.append(att_header)
        att_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self._attachment_flow = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_bottom=2
        )
        att_scroll.set_child(self._attachment_flow)
        att_bar.append(att_scroll)
        self._attachment_bar = att_bar
        viewer_box.append(self._attachment_bar)

        self._smart_reply_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self._smart_reply_bar.add_css_class("smart-reply-bar")
        smart_reply_title = Gtk.Label(label="SMART REPLY", xalign=0.0)
        smart_reply_title.add_css_class("smart-reply-title")
        self._smart_reply_bar.append(smart_reply_title)
        smart_reply_chip = Gtk.Label(label="LOCAL")
        smart_reply_chip.add_css_class("smart-reply-chip")
        self._smart_reply_bar.append(smart_reply_chip)
        self._smart_reply_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True
        )
        self._smart_reply_btn_box.set_halign(Gtk.Align.START)
        for chip_label in ("Accept Wed 2pm", "Propose Thu", "Decline"):
            chip_btn = Gtk.Button(label=chip_label)
            chip_btn.add_css_class("smart-reply-chip-btn")
            chip_btn.connect(
                "clicked",
                lambda _btn, text=chip_label: self._prefill_reply_with(text),
            )
            self._smart_reply_btn_box.append(chip_btn)
        self._smart_reply_bar.append(self._smart_reply_btn_box)
        smart_reply_write_btn = Gtk.Button(label="Write my own")
        smart_reply_write_btn.add_css_class("flat")
        smart_reply_write_btn.add_css_class("smart-reply-write")
        smart_reply_write_btn.connect(
            "clicked", lambda _: self._thread_reply_view.grab_focus()
        )
        self._smart_reply_bar.append(smart_reply_write_btn)
        self._smart_reply_bar.set_visible(False)
        viewer_box.append(self._smart_reply_bar)

        self._thread_reply_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            valign=Gtk.Align.FILL,
        )
        self._thread_reply_bar.add_css_class("thread-reply-bar")
        reply_pencil = Gtk.Image(
            icon_name=_pick_icon_name(
                "hermod-pencil-symbolic", "document-edit-symbolic"
            )
        )
        reply_pencil.add_css_class("thread-reply-pencil")
        reply_pencil.set_valign(Gtk.Align.START)
        reply_pencil.set_margin_top(10)
        self._thread_reply_bar.append(reply_pencil)
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
        self._thread_reply_view.add_css_class("thread-reply-editor")
        reply_scroller.set_child(self._thread_reply_view)
        self._thread_reply_send = Gtk.Button(label="Send", hexpand=False)
        self._thread_reply_send.add_css_class("suggested-action")
        self._thread_reply_send.add_css_class("thread-reply-send")
        self._thread_reply_send.connect("clicked", self._on_thread_reply_send)
        self._thread_reply_bar.append(reply_scroller)
        self._thread_reply_bar.append(self._thread_reply_send)
        self._thread_reply_bar.set_visible(False)
        viewer_box.append(self._thread_reply_bar)

        try:
            from .settings import build_settings_content
        except ImportError:
            from settings import build_settings_content
        self._settings_window = None
        self._app_settings_content = build_settings_content(
            self, on_close=self._close_settings_modal, scrollable=False
        )
        self._welcome_settings_content = self._build_welcome_settings_content()
        self._welcome_settings_shell = WelcomeSettingsShell(
            self._welcome_settings_content,
            on_back=self._show_welcome_mode,
        )
        self._welcome_screen = WelcomeScreen(
            on_provider_selected=self._onboarding_provider_selected,
            on_open_hermod=self._show_app_root,
            get_backends=lambda: self.backends,
        )
        self._startup_status_panel = StartupStatusPanel(
            self.backends,
            accent_for_identity=self._account_class_for,
            on_close=self._dismiss_startup_status_view,
        )
        self._viewer_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
            hexpand=True,
        )
        self._compose_holder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True
        )
        self._viewer_stack.add_named(self._startup_status_panel, "startup-status")
        self._viewer_stack.add_named(viewer_box, "viewer")
        self._viewer_stack.add_named(self._compose_holder, "compose")
        self._viewer_stack.set_visible_child_name(
            "startup-status" if self._startup_status_active else "viewer"
        )

        self._thread_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._thread_sidebar.add_css_class("thread-sidebar")
        sidebar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_hexpand(True)
        self._thread_sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._thread_sidebar_row_cls = ThreadNavRow
        self._thread_sidebar_list.add_css_class("thread-sidebar-list")
        self._thread_sidebar_list.connect(
            "row-activated", self._on_thread_sidebar_row_activated
        )
        sidebar_scroll.set_child(self._thread_sidebar_list)
        self._thread_sidebar.append(sidebar_scroll)
        self._thread_sidebar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=240,
            hexpand=False,
        )
        self._thread_sidebar_revealer.set_child(self._thread_sidebar)
        self._thread_sidebar_revealer.set_reveal_child(False)

        self._thread_webview_overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self._thread_webview_overlay.set_child(self.webview)
        self._thread_body_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True
        )
        self._thread_body_box.append(self._thread_webview_overlay)
        self._thread_body_box.append(self._thread_sidebar_revealer)

        self._thread_summary_banner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self._thread_summary_banner.add_css_class("thread-summary-banner")
        summary_head = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        summary_title = Gtk.Label(label="THREAD SUMMARY", xalign=0.0)
        summary_title.add_css_class("thread-summary-title")
        summary_head.append(summary_title)
        summary_chip = Gtk.Label(label="LOCAL")
        summary_chip.add_css_class("thread-summary-chip")
        summary_head.append(summary_chip)
        summary_spacer = Gtk.Box(hexpand=True)
        summary_head.append(summary_spacer)
        summary_close = Gtk.Button(
            icon_name=_pick_icon_name(
                "hermod-x-symbolic", "window-close-symbolic"
            ),
            tooltip_text="Hide summary",
        )
        summary_close.add_css_class("flat")
        summary_close.add_css_class("thread-summary-close")
        summary_close.connect(
            "clicked", lambda _: self._thread_summary_banner.set_visible(False)
        )
        summary_head.append(summary_close)
        self._thread_summary_banner.append(summary_head)
        self._thread_summary_text = Gtk.Label(
            label="Summary appears when a local model is connected.",
            xalign=0.0,
        )
        self._thread_summary_text.add_css_class("thread-summary-text")
        self._thread_summary_text.set_wrap(True)
        self._thread_summary_banner.append(self._thread_summary_text)
        self._thread_summary_banner.set_visible(False)

        viewer_box.insert_child_after(self._thread_body_box, self._message_info_bar)
        viewer_box.insert_child_after(
            self._thread_summary_banner, self._message_info_bar
        )

        viewer_shell = Gtk.Frame(vexpand=True, hexpand=True)
        viewer_shell.add_css_class("reading-pane-shell")
        viewer_shell.set_child(self._viewer_stack)
        right.set_end_child(viewer_shell)
        body.append(right)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(body)
        app_root.set_content(self._toast_overlay)

        self._root_mode_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
            hexpand=True,
        )
        self._root_mode_stack.add_named(self._welcome_screen, "welcome")
        self._root_mode_stack.add_named(
            self._welcome_settings_shell, "welcome-settings"
        )
        self._root_mode_stack.add_named(app_root, "app")
        force_welcome = os.environ.get("HERMOD_FORCE_WELCOME") == "1"
        self._root_mode_stack.set_visible_child_name(
            "welcome" if force_welcome or not self._has_accounts() else "app"
        )
        self.set_content(self._root_mode_stack)

        force_settings = os.environ.get("HERMOD_FORCE_SETTINGS")
        if (
            force_settings
            and self._has_accounts()
            and hasattr(self, "_show_settings_view")
        ):
            GLib.idle_add(self._show_settings_view)
            pane_id = force_settings if force_settings != "1" else None
            if pane_id and self._app_settings_content is not None:
                content = self._app_settings_content
                def _switch():
                    selector = getattr(content, "select_pane", None)
                    if callable(selector):
                        selector(pane_id)
                    return False
                GLib.idle_add(_switch)

        self._content_paned.set_position(
            max(
                _MESSAGE_LIST_MIN_WIDTH,
                min(_MESSAGE_LIST_MAX_WIDTH, self._content_paned.get_position()),
            )
        )
        self._show_empty_viewer()
        GLib.idle_add(self._prune_disk_body_cache)

    def _on_ui_sort_changed(self, order):
        MessageListMixin._on_sort_changed(self, order)
        self._sync_sort_toggle_button()

    def _toggle_sort_order(self):
        self._on_ui_sort_changed(
            "oldest" if getattr(self, "_sort_order", "newest") == "newest" else "newest"
        )

    def _toggle_unread_only(self, active):
        MessageListMixin._toggle_unread_only(self, active)
        self._sync_unread_toggle_button()

    def _sync_unread_toggle_button(self):
        button = getattr(self, "_unread_toggle_btn", None)
        if button is None:
            return
        active = bool(getattr(self, "_show_unread_only", False))
        button.set_active(active)
        button.set_tooltip_text("Show all mail" if active else "Unread only")
        if active:
            button.add_css_class("active")
        else:
            button.remove_css_class("active")

    def _sort_icon_name_for_order(self, order):
        return (
            "view-sort-descending-symbolic"
            if order == "newest"
            else "view-sort-ascending-symbolic"
        )

    def _sort_tooltip_for_order(self, order):
        return "Newest first" if order == "newest" else "Oldest first"

    def _sync_sort_toggle_button(self):
        button = getattr(self, "_sort_toggle_btn", None)
        icon = getattr(self, "_sort_toggle_icon", None)
        if icon is not None:
            icon.set_from_icon_name(
                self._sort_icon_name_for_order(getattr(self, "_sort_order", "newest"))
            )
        if button is not None:
            button.set_tooltip_text(
                self._sort_tooltip_for_order(getattr(self, "_sort_order", "newest"))
            )
        self._sync_message_toolbar_controls()

    def _sync_message_toolbar_controls(self):
        button = getattr(self, "_load_older_btn", None)
        if button is None:
            return
        sort_order = getattr(self, "_sort_order", "newest")
        show_button = sort_order == "oldest" and bool(
            getattr(self, "_message_has_more", False)
        )
        button.set_visible(show_button)
        if not show_button:
            return
        loading = bool(getattr(self, "_message_loading", False))
        button.set_sensitive(not loading)
        button.set_label("Loading..." if loading else "Load older")

    def _set_startup_status_state(self, identity, state, detail=""):
        panel = getattr(self, "_startup_status_panel", None)
        if panel is None:
            return

        def apply():
            panel.set_account_state(identity, state, detail)
            return False

        GLib.idle_add(apply)

    def _set_startup_status_title(self, title, subtitle=None):
        panel = getattr(self, "_startup_status_panel", None)
        if panel is None:
            return

        def apply():
            panel.set_title(title, subtitle)
            return False

        GLib.idle_add(apply)

    def _show_startup_status_view(self):
        if getattr(self, "_startup_status_panel", None) is None:
            return
        self._viewer_stack.set_visible_child_name("startup-status")

    def _clear_startup_status_view(self):
        was_active = bool(getattr(self, "_startup_status_active", False))
        self._startup_status_active = False
        self._startup_visible_ready = False
        self._startup_counts_ready = False
        self._startup_counts_seen = set()
        self._startup_counts_warmup_started = False
        if getattr(self, "_startup_status_complete_id", None):
            GLib.source_remove(self._startup_status_complete_id)
            self._startup_status_complete_id = None
        if (
            getattr(self, "_viewer_stack", None) is not None
            and self._viewer_stack.get_visible_child_name() == "startup-status"
        ):
            self._viewer_stack.set_visible_child_name("viewer")
        # Startup was gating the poll loop (see __main__.py::_poll_loop,
        # which skips polls while _startup_status_active is True). Kick off
        # an immediate reconcile so sidebar badges get their first counts
        # without waiting the 15s poll-grace window.
        if was_active:
            app = self.get_application() if hasattr(self, "get_application") else None
            if app is not None and hasattr(app, "wake_background_updates"):
                app.wake_background_updates(reconcile=True)

    def _dismiss_startup_status_view(self):
        self._clear_startup_status_view()
        if hasattr(self, "_refresh_all_unread_counts"):
            self._refresh_all_unread_counts()
        self._show_mail_view()

    def _schedule_startup_status_completion(
        self, total_new=0, delay_ms=1000, force=False
    ):
        if threading.current_thread() is not threading.main_thread():
            GLib.idle_add(
                self._schedule_startup_status_completion, total_new, delay_ms, force
            )
            return
        if not getattr(self, "_startup_status_active", False):
            return
        if not force and not (
            getattr(self, "_startup_visible_ready", False)
            and getattr(self, "_startup_counts_ready", False)
        ):
            return
        panel = getattr(self, "_startup_status_panel", None)
        if panel is not None:
            blocking_attention = False
            if hasattr(panel, "has_blocking_attention"):
                blocking_attention = panel.has_blocking_attention()
            elif hasattr(panel, "has_attention"):
                blocking_attention = panel.has_attention()
            if blocking_attention:
                if getattr(self, "_startup_status_complete_id", None):
                    GLib.source_remove(self._startup_status_complete_id)
                    self._startup_status_complete_id = None
                return
        if getattr(self, "_startup_status_complete_id", None):
            return

        def _complete():
            self._startup_status_complete_id = None
            self._clear_startup_status_view()
            if hasattr(self, "_refresh_all_unread_counts"):
                self._refresh_all_unread_counts()
            if getattr(self, "_viewer_stack", None) is not None:
                self._viewer_stack.set_visible_child_name("viewer")
            self._show_mail_view()
            return False

        self._startup_status_complete_id = GLib.idle_add(_complete)

    def _on_current_reply(self):
        if self._active_email_row:
            self._on_reply(self._active_email_row.msg)

    def _on_current_reply_all(self):
        if self._active_email_row:
            self._on_reply_all(self._active_email_row.msg)

    def _on_current_forward(self):
        if self._active_email_row:
            self._on_forward(self._active_email_row.msg)

    def _on_current_delete(self):
        if self._active_email_row:
            self._on_delete(self._active_email_row.widget, self._active_email_row.msg)

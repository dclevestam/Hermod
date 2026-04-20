import collections
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import window as window_module
import window_welcome as welcome_module
from widgets import LoadMoreListItem, MessageListItem


def _message(uid="1"):
    return {
        "uid": uid,
        "subject": "Subject",
        "sender_name": "Tester",
        "sender_email": "tester@example.com",
        "to_addrs": [],
        "cc_addrs": [],
        "date": datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
        "is_read": False,
        "has_attachments": False,
        "snippet": "",
        "folder": "INBOX",
        "backend": "gmail",
        "account": "test@example.com",
        "thread_id": "",
        "thread_source": "gmail-imap",
    }


class _DummyWidget:
    def __init__(self):
        self.selected = None
        self.thread_count = None
        self.read_calls = 0
        self.unread_calls = 0

    def set_selected(self, selected):
        self.selected = selected

    def set_thread_count(self, count):
        self.thread_count = count

    def mark_read(self):
        self.read_calls += 1

    def mark_unread(self):
        self.unread_calls += 1

    def grab_focus(self):
        return None


class _DummyAdjustment:
    def __init__(self, value=0.0, lower=0.0, upper=1000.0, page_size=100.0):
        self.value = value
        self.lower = lower
        self.upper = upper
        self.page_size = page_size

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def get_lower(self):
        return self.lower

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page_size


class _DummyScroll:
    def __init__(self, adjustment):
        self.adjustment = adjustment

    def get_vadjustment(self):
        return self.adjustment


class _DummyStack:
    def __init__(self, visible_child_name=None):
        self.visible_child_name = visible_child_name

    def set_visible_child_name(self, name):
        self.visible_child_name = name

    def get_visible_child_name(self):
        return self.visible_child_name


class _DummyListBox:
    def get_first_child(self):
        return None

    def remove(self, _child):
        return None

    def select_row(self, _row):
        return None


class _DummyLabel:
    def __init__(self):
        self.use_markup = None
        self.label = None

    def set_use_markup(self, value):
        self.use_markup = value

    def set_label(self, value):
        self.label = value

    def set_visible(self, _value):
        return None


class MessageListTests(unittest.TestCase):
    def test_message_list_item_delegates_widget_state(self):
        item = MessageListItem(_message())
        widget = _DummyWidget()
        item.bind_widget(widget)

        item.set_selected(True)
        item.set_thread_count(4)
        item.mark_read()
        item.mark_unread()

        self.assertTrue(widget.selected)
        self.assertEqual(widget.thread_count, 4)
        self.assertEqual(widget.read_calls, 1)
        self.assertEqual(widget.unread_calls, 1)
        self.assertFalse(item.msg["is_read"])
        self.assertEqual(item.msg["thread_count"], 4)

    def test_load_more_list_item_delegates_selection(self):
        item = LoadMoreListItem()
        widget = _DummyWidget()
        item.bind_widget(widget)

        item.set_selected(True)

        self.assertTrue(widget.selected)

    def test_paged_messages_reports_has_more(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._message_page_limit = 2

        page, has_more = win._paged_messages(
            [_message("1"), _message("2"), _message("3")]
        )

        self.assertEqual([msg["uid"] for msg in page], ["1", "2"])
        self.assertTrue(has_more)

    def test_build_message_items_appends_load_more_sentinel(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._account_class_for = lambda _identity: "account-accent-1"

        items = win._build_message_items([_message("1")], has_more=True)

        self.assertEqual(len(items), 2)
        self.assertIsInstance(items[0], MessageListItem)
        self.assertEqual(items[0].accent_class, "account-accent-1")
        self.assertIsInstance(items[1], LoadMoreListItem)

    def test_build_message_items_omits_load_more_for_oldest_sort(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._account_class_for = lambda _identity: "account-accent-1"
        win._sort_order = "oldest"

        items = win._build_message_items([_message("1")], has_more=True)

        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], MessageListItem)

    def test_sync_message_toolbar_controls_shows_load_button_in_oldest_sort(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._sort_order = "oldest"
        win._message_has_more = True
        win._message_loading = False
        win._load_older_btn = mock.Mock()

        window_module.HermodWindow._sync_message_toolbar_controls(win)

        win._load_older_btn.set_visible.assert_called_once_with(True)
        win._load_older_btn.set_sensitive.assert_called_once_with(True)
        win._load_older_btn.set_label.assert_called_once_with("Load older")

    def test_sync_message_toolbar_controls_shows_loading_state(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._sort_order = "oldest"
        win._message_has_more = True
        win._message_loading = True
        win._load_older_btn = mock.Mock()

        window_module.HermodWindow._sync_message_toolbar_controls(win)

        win._load_older_btn.set_visible.assert_called_once_with(True)
        win._load_older_btn.set_sensitive.assert_called_once_with(False)
        win._load_older_btn.set_label.assert_called_once_with("Loading...")

    def test_seed_unread_counts_from_messages_does_not_override_provider_counts(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._unread_counts = collections.defaultdict(
            lambda: {"inbox": 87, "trash": 0, "spam": 0}
        )
        win.current_folder = "INBOX"
        win._startup_status_active = False
        win.update_account_counts = lambda *args, **kwargs: self.fail(
            "visible rows should not seed unread counts"
        )

        window_module.HermodWindow._seed_unread_counts_from_messages(
            win, [_message("1")]
        )

        self.assertEqual(win._unread_counts["test@example.com"]["inbox"], 87)

    def test_apply_provider_cached_messages_marks_source_explicitly(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        set_calls = []
        win._paged_messages = lambda msgs: (list(msgs), False)
        win._set_messages = (
            lambda msgs, generation, preserve_selected_key, source, persist_snapshot, prefetch_bodies, has_more: (
                set_calls.append(
                    (
                        msgs,
                        generation,
                        preserve_selected_key,
                        source,
                        persist_snapshot,
                        prefetch_bodies,
                        has_more,
                    )
                )
            )
        )

        applied = window_module.HermodWindow._apply_provider_cached_messages(
            win, [_message("1")], 4, ("acct", "INBOX", "1")
        )

        self.assertTrue(applied)
        self.assertEqual(
            set_calls,
            [
                (
                    [_message("1")],
                    4,
                    ("acct", "INBOX", "1"),
                    "provider-cache",
                    False,
                    False,
                    False,
                ),
            ],
        )

    def test_set_messages_treats_provider_cache_as_live_generation(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._message_load_generation = 3
        win._message_live_generation = 0
        win._set_message_loading = lambda loading, generation=None: None
        win._message_list_context_key = lambda: ("acct", "INBOX")
        win._sync_message_toolbar_controls = lambda: None
        win._message_store = mock.Mock()
        win._thread_groups = {}
        win._prefetch_generation = 0
        win._update_message_empty_state = lambda: None
        win._message_has_more = False

        window_module.HermodWindow._set_messages(
            win,
            [],
            generation=3,
            source="provider-cache",
            persist_snapshot=False,
            prefetch_bodies=False,
        )

        self.assertEqual(win._message_live_generation, 3)

    def test_unified_cached_messages_aggregates_provider_cache(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        backend_a = mock.Mock()
        backend_a.identity = "acct-a"
        backend_a.FOLDERS = [("INBOX", "Inbox", "mail-inbox-symbolic")]
        backend_b = mock.Mock()
        backend_b.identity = "acct-b"
        backend_b.FOLDERS = [("INBOX", "Inbox", "mail-inbox-symbolic")]
        win.backends = [backend_a, backend_b]
        win._cached_messages_for_backend = lambda backend, folder, limit: [
            _message(f"{backend.identity}-{folder}")
            | {
                "account": backend.identity,
                "backend_obj": backend,
                "date": datetime(
                    2026,
                    4,
                    7,
                    8 if backend.identity == "acct-a" else 9,
                    30,
                    tzinfo=timezone.utc,
                ),
            }
        ]

        msgs = window_module.HermodWindow._unified_cached_messages(win, None, 10)

        self.assertEqual([msg["account"] for msg in msgs], ["acct-b", "acct-a"])

    def test_load_more_request_advances_page_limit_and_refreshes(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.current_folder = "INBOX"
        win._message_page_limit = 100
        win._email_scroll = None
        win._pending_list_scroll_value = None
        calls = []
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: (
            calls.append((force, preserve_selected))
        )

        win._on_load_more_requested()

        self.assertEqual(win._message_page_limit, 200)
        self.assertEqual(calls, [(True, True)])

    def test_load_more_request_captures_scroll_position(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.current_folder = "INBOX"
        win._message_page_limit = 100
        win._pending_list_scroll_value = None
        adjustment = _DummyAdjustment(value=312.0)
        win._email_scroll = _DummyScroll(adjustment)
        calls = []
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: (
            calls.append((force, preserve_selected))
        )

        win._on_load_more_requested()

        self.assertEqual(win._pending_list_scroll_value, 312.0)
        self.assertEqual(calls, [(True, True)])

    def test_sort_toggle_icon_matches_order(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)

        self.assertEqual(
            win._sort_icon_name_for_order("newest"), "view-sort-descending-symbolic"
        )
        self.assertEqual(
            win._sort_icon_name_for_order("oldest"), "view-sort-ascending-symbolic"
        )
        self.assertEqual(win._sort_tooltip_for_order("newest"), "Newest first")
        self.assertEqual(win._sort_tooltip_for_order("oldest"), "Oldest first")

    def test_sort_toggle_click_flips_order_and_updates_icon(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._sort_order = "newest"
        win._sort_toggle_icon = mock.Mock()
        win._sort_toggle_btn = mock.Mock()
        calls = []
        win._on_ui_sort_changed = lambda order: calls.append(order)

        win._toggle_sort_order()

        self.assertEqual(calls, ["oldest"])

    def test_sync_sort_toggle_button_sets_icon_and_tooltip(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._sort_order = "oldest"
        win._sort_toggle_icon = mock.Mock()
        win._sort_toggle_btn = mock.Mock()

        window_module.HermodWindow._sync_sort_toggle_button(win)

        win._sort_toggle_icon.set_from_icon_name.assert_called_once_with(
            "view-sort-ascending-symbolic"
        )
        win._sort_toggle_btn.set_tooltip_text.assert_called_once_with("Oldest first")

    def test_email_filter_hides_read_messages_in_unread_mode(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._search_text = ""
        win._show_unread_only = True
        unread_item = MessageListItem(_message("1") | {"is_read": False})
        read_item = MessageListItem(_message("2") | {"is_read": True})

        self.assertTrue(window_module.HermodWindow._email_filter(win, unread_item))
        self.assertFalse(window_module.HermodWindow._email_filter(win, read_item))

    def test_update_message_empty_state_uses_friendly_unread_messages_copy(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._show_unread_only = True
        win._unread_filter_had_results = False
        win._search_text = ""
        win._message_store = mock.Mock()
        win._message_store.get_n_items.return_value = 0
        win._filtered_message_model = mock.Mock()
        win._filtered_message_model.get_n_items.return_value = 0
        win._empty_page = mock.Mock()
        win._list_stack = mock.Mock()

        window_module.HermodWindow._update_message_empty_state(win)

        win._empty_page.set_title.assert_called_once_with("No unread messages")
        win._empty_page.set_description.assert_called_once_with(
            "You are already caught up."
        )
        win._list_stack.set_visible_child_name.assert_called_once_with("empty")

    def test_update_message_empty_state_says_all_caught_up_after_unreads_clear(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._show_unread_only = True
        win._unread_filter_had_results = True
        win._search_text = ""
        win._message_store = mock.Mock()
        win._message_store.get_n_items.return_value = 0
        win._filtered_message_model = mock.Mock()
        win._filtered_message_model.get_n_items.return_value = 0
        win._empty_page = mock.Mock()
        win._list_stack = mock.Mock()

        window_module.HermodWindow._update_message_empty_state(win)

        win._empty_page.set_title.assert_called_once_with("All caught up")
        win._empty_page.set_description.assert_called_once_with(
            "Nice work. You cleared every unread message."
        )

    def test_startup_completion_waits_for_attention(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        panel = mock.Mock()
        panel.has_attention.return_value = True
        win._startup_status_active = True
        win._startup_visible_ready = True
        win._startup_counts_ready = True
        win._startup_status_complete_id = None
        win._startup_status_panel = panel

        window_module.HermodWindow._schedule_startup_status_completion(win, force=True)

        self.assertIsNone(win._startup_status_complete_id)

    def test_show_settings_view_routes_to_welcome_setup_without_accounts(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.backends = []
        calls = []
        win._show_welcome_settings_view = lambda: calls.append("welcome-settings")

        window_module.HermodWindow._show_settings_view(win)

        self.assertEqual(calls, ["welcome-settings"])

    def test_show_mail_view_routes_to_welcome_without_accounts(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.backends = []
        calls = []
        win._show_welcome_mode = lambda: calls.append("welcome")

        window_module.HermodWindow._show_mail_view(win)

        self.assertEqual(calls, ["welcome"])

    def test_build_welcome_settings_content_returns_to_welcome_after_save(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        calls = []
        win._show_welcome_mode = lambda reset_editor=True: calls.append(reset_editor)
        content = mock.Mock()
        content.account_controller = mock.Mock(editor_done_callback=None)

        with mock.patch(
            "settings.build_settings_content", return_value=content
        ) as build_settings_content:
            result = window_module.HermodWindow._build_welcome_settings_content(win)

        self.assertIs(result, content)
        build_settings_content.assert_called_once_with(
            win,
            on_close=win._show_welcome_mode,
            on_back=win._show_welcome_mode,
            scrollable=False,
        )
        content.account_controller.editor_done_callback()
        self.assertEqual(calls, [True])

    def test_show_welcome_account_setup_opens_requested_editor(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._root_mode_stack = _DummyStack()
        win._show_welcome_mode = mock.Mock()
        with mock.patch.object(
            window_module, "build_account_setup_dialog"
        ) as build_dialog:
            build_dialog.return_value = mock.Mock()

            window_module.HermodWindow._show_welcome_account_setup(win, "gmail")

            build_dialog.assert_called_once()
            self.assertEqual(
                build_dialog.call_args.kwargs.get("on_saved"), win._show_welcome_mode
            )
            self.assertEqual(win._root_mode_stack.visible_child_name, None)

    def test_onboarding_provider_set_is_four(self):
        self.assertEqual(
            [item[0] for item in welcome_module.ACTIVE_ONBOARDING_PROVIDERS],
            ["Gmail", "Proton", "Microsoft", "IMAP"],
        )

    def test_welcome_move_strip_adds_drag_controller(self):
        class _DummyWindow:
            def __init__(self):
                self._surface = mock.Mock()
                self._surface.begin_move = mock.Mock()

            def get_root(self):
                return self

            def get_native(self):
                return self

            def get_surface(self):
                return self._surface

        class _DummyWidget:
            def __init__(self):
                self.controllers = []

            def add_controller(self, controller):
                self.controllers.append(controller)

        widget = _DummyWidget()
        welcome_module._attach_window_move_controller(widget, _DummyWindow())
        self.assertEqual(len(widget.controllers), 1)

    def test_reload_backends_shows_welcome_after_last_account_is_removed(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        backend = mock.Mock()
        backend.identity = "acct"
        win.backends = [backend]
        win.current_backend = backend
        win.current_folder = "INBOX"
        win.refresh_account_chrome = lambda: None
        win.folder_list = _DummyListBox()
        win._folder_rows = {}
        win._account_state = {}
        win._all_inboxes_row = None
        win._populate_sidebar = lambda: None
        win._compose_view = None
        calls = []
        win._show_welcome_mode = lambda reset_editor=True: calls.append(reset_editor)

        with mock.patch.object(window_module, "get_backends", return_value=[]):
            window_module.HermodWindow.reload_backends(win)

        self.assertEqual(calls, [True])

    def test_reload_backends_restarts_startup_flow_when_first_account_is_added(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        backend = mock.Mock()
        backend.identity = "acct"
        backend.FOLDERS = [("INBOX", "Inbox", "mail-inbox-symbolic")]
        win.backends = []
        win.current_backend = None
        win.current_folder = None
        win.refresh_account_chrome = lambda: None
        win.folder_list = _DummyListBox()
        win._folder_rows = {}
        win._account_state = {}
        win._all_inboxes_row = None
        win._populate_sidebar = lambda: None
        win._compose_view = None
        calls = []
        win._show_app_root = lambda: calls.append("app")
        win._enter_account_startup_mode = lambda: calls.append("startup")
        win._warm_startup_unread_counts = lambda: calls.append("warm")
        win._select_initial_folder_row = lambda backend_id=None, folder=None: (
            calls.append(("select", backend_id, folder)) or object()
        )
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: (
            self.fail("selection should drive the first load")
        )

        with mock.patch.object(window_module, "get_backends", return_value=[backend]):
            window_module.HermodWindow.reload_backends(win)

        self.assertEqual(calls, ["app", "startup", "warm", ("select", None, None)])

    def test_startup_completion_does_not_reschedule_once_queued(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._startup_status_active = True
        win._startup_visible_ready = True
        win._startup_counts_ready = True
        win._startup_status_complete_id = 42
        panel = mock.Mock()
        panel.has_blocking_attention.return_value = False
        win._startup_status_panel = panel
        with mock.patch.object(window_module.GLib, "idle_add") as idle_add:
            window_module.HermodWindow._schedule_startup_status_completion(win)

        idle_add.assert_not_called()
        self.assertEqual(win._startup_status_complete_id, 42)

    def test_startup_completion_cancels_pending_timer_when_error_appears(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        panel = mock.Mock()
        panel.has_blocking_attention.return_value = True
        win._startup_status_active = True
        win._startup_visible_ready = True
        win._startup_counts_ready = True
        win._startup_status_complete_id = 42
        win._startup_status_panel = panel

        with mock.patch.object(window_module.GLib, "source_remove") as source_remove:
            window_module.HermodWindow._schedule_startup_status_completion(
                win, force=True
            )

        source_remove.assert_called_once_with(42)
        self.assertIsNone(win._startup_status_complete_id)

    def test_startup_completion_marshals_off_main_thread(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._startup_status_active = True
        win._startup_visible_ready = True
        win._startup_counts_ready = True
        win._startup_status_complete_id = None
        win._startup_status_panel = mock.Mock()
        win._startup_status_panel.has_blocking_attention.return_value = False
        idle_calls = []
        with mock.patch.object(
            window_module.threading, "current_thread", return_value=object()
        ):
            with mock.patch.object(
                window_module.threading, "main_thread", return_value=object()
            ):
                with mock.patch.object(
                    window_module.GLib,
                    "idle_add",
                    side_effect=lambda fn, *args: idle_calls.append((fn, args)),
                ):
                    window_module.HermodWindow._schedule_startup_status_completion(
                        win, force=True
                    )

        self.assertEqual(len(idle_calls), 1)
        self.assertEqual(
            idle_calls[0][0].__func__,
            window_module.HermodWindow._schedule_startup_status_completion,
        )

    def test_startup_completion_does_not_wake_background_updates(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        app = mock.Mock()
        win.get_application = lambda: app
        win._startup_status_active = True
        win._startup_visible_ready = True
        win._startup_counts_ready = True
        win._startup_status_complete_id = None
        win._startup_status_panel = mock.Mock()
        win._startup_status_panel.has_blocking_attention.return_value = False
        win._clear_startup_status_view = lambda: None
        win._refresh_all_unread_counts = lambda: None
        win._show_mail_view = lambda: None
        idle_calls = []
        with mock.patch.object(
            window_module.GLib,
            "idle_add",
            side_effect=lambda fn, *args: idle_calls.append((fn, args)),
        ):
            window_module.HermodWindow._schedule_startup_status_completion(
                win, force=True
            )

        self.assertEqual(len(idle_calls), 1)
        idle_calls[0][0](*idle_calls[0][1])
        app.wake_background_updates.assert_not_called()

    def test_background_update_marks_accounts_ready_when_no_notice_is_reported(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.backends = [mock.Mock(identity="acct-a")]
        win._startup_status_active = True
        win._startup_counts_seen = set()
        win._startup_counts_ready = False
        win._startup_visible_ready = True
        win._startup_status_complete_id = None
        win.set_syncing = lambda syncing: None
        win.update_account_counts = lambda backend_identity, **kwargs: None
        win._background_result_affects_current_view = lambda result: False
        states = []
        win._set_startup_status_state = lambda identity, state, detail="": (
            states.append((identity, state, detail))
        )
        win._schedule_startup_status_completion = (
            lambda total_new=0, delay_ms=1000, force=False: None
        )

        window_module.HermodWindow.on_background_update(
            win,
            [{"account": "acct-a", "counts": {"inbox": 1}}],
            total_new=0,
        )

        self.assertEqual(states, [("acct-a", "ready", "Ready")])

    def test_background_update_refreshes_view_when_provider_count_changes(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        backend = mock.Mock(identity="acct-a")
        win.backends = [backend]
        win.current_backend = backend
        win.current_folder = "INBOX"
        win._startup_status_active = False
        win._startup_counts_seen = set()
        win._startup_counts_ready = False
        win._startup_visible_ready = True
        win._startup_status_complete_id = None
        win.set_syncing = lambda syncing: None
        win._background_result_affects_current_view = lambda result: False
        win.update_account_counts = lambda backend_identity, **kwargs: None
        refresh_calls = []
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: (
            refresh_calls.append((force, preserve_selected))
        )

        window_module.HermodWindow.on_background_update(
            win,
            [{"account": "acct-a", "counts": {"inbox": 1}}],
            total_new=0,
        )

        self.assertEqual(refresh_calls, [(True, True)])

    def test_provider_count_refresh_updates_current_view_after_recount(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        backend = mock.Mock(identity="acct-a")
        win.current_backend = backend
        win.current_folder = "INBOX"
        win._startup_status_active = False
        win._unread_counts = collections.defaultdict(
            lambda: {"inbox": 0, "trash": 0, "spam": 0}
        )
        updates = []
        refresh_calls = []
        win.update_account_counts = lambda backend_identity, inbox_count=None, trash_count=None, spam_count=None, **kwargs: updates.append((backend_identity, inbox_count, trash_count, spam_count))
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: (
            refresh_calls.append((force, preserve_selected))
        )
        backend.get_unread_count.return_value = 4
        msg = _message("1") | {
            "backend_obj": backend,
            "account": "acct-a",
            "folder": "INBOX",
        }

        with mock.patch.object(
            window_module.GLib,
            "idle_add",
            side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
        ):
            window_module.HermodWindow._refresh_provider_counts_for_message(
                win, msg, backend
            )

        self.assertEqual(updates, [("acct-a", 4, None, None)])
        self.assertEqual(refresh_calls, [(True, True)])

    def test_seed_unread_counts_from_messages_does_not_override_provider_counts(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.current_folder = window_module._UNIFIED
        win._unread_counts = {
            "acct-a": {"inbox": 2, "trash": 0, "spam": 0},
            "acct-b": {"inbox": 1, "trash": 0, "spam": 0},
        }
        updates = []
        win.update_account_counts = lambda backend_identity, inbox_count=None, trash_count=None, spam_count=None, **kwargs: updates.append((backend_identity, inbox_count, trash_count, spam_count))

        msgs = [
            _message("1") | {"account": "acct-a", "folder": "INBOX", "is_read": False},
            _message("2") | {"account": "acct-a", "folder": "INBOX", "is_read": False},
            _message("3") | {"account": "acct-a", "folder": "INBOX", "is_read": False},
            _message("4") | {"account": "acct-b", "folder": "INBOX", "is_read": False},
            _message("5") | {"account": "acct-b", "folder": "INBOX", "is_read": False},
        ]

        window_module.HermodWindow._seed_unread_counts_from_messages(win, msgs)

        self.assertEqual(updates, [])
        self.assertEqual(win._unread_counts["acct-a"]["inbox"], 2)
        self.assertEqual(win._unread_counts["acct-b"]["inbox"], 1)

    def test_seed_unread_counts_from_messages_is_hidden_during_startup(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.current_folder = window_module._UNIFIED
        win._startup_status_active = True
        updates = []
        win.update_account_counts = lambda backend_identity, inbox_count=None, trash_count=None, spam_count=None, **kwargs: updates.append((backend_identity, inbox_count, trash_count, spam_count))

        msgs = [
            _message("1") | {"account": "acct-a", "folder": "INBOX", "is_read": False},
            _message("2") | {"account": "acct-b", "folder": "INBOX", "is_read": False},
        ]

        window_module.HermodWindow._seed_unread_counts_from_messages(win, msgs)

        self.assertEqual(updates, [])

    def test_restore_pending_list_scroll_clamps_and_clears(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        adjustment = _DummyAdjustment(
            value=0.0, lower=0.0, upper=500.0, page_size=120.0
        )
        win._email_scroll = _DummyScroll(adjustment)
        win._pending_list_scroll_value = 460.0

        result = win._restore_pending_list_scroll()

        self.assertFalse(result)
        self.assertEqual(adjustment.value, 380.0)
        self.assertIsNone(win._pending_list_scroll_value)

    def test_email_selection_change_does_not_auto_commit_reader(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._suppress_email_selection = False
        win._commit_email_selection = lambda row: self.fail(
            "selection change should not auto-commit"
        )

        win._on_email_selected()

    def test_email_list_activation_selects_before_commit(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        item = MessageListItem(_message("1"))
        calls = []
        win._visible_message_item = lambda position: item if position == 3 else None
        win._set_selected_visible_index = (
            lambda position, suppress=False, grab_focus=False: calls.append(
                ("select", position, suppress, grab_focus)
            )
        )
        win._request_commit_email_selection = lambda selected_item: calls.append(
            ("commit", selected_item)
        )

        win._on_email_list_activated(None, 3)

        self.assertEqual(calls[0], ("select", 3, True, False))
        self.assertEqual(calls[1], ("commit", item))

    def test_commit_email_selection_updates_cached_read_state(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        item = MessageListItem(_message("1"))
        sync_calls = []
        refresh_calls = []
        win._startup_autoselect_pending = True
        win._body_load_generation = 0
        win._show_mail_view = lambda: None
        win._load_body = lambda msg, generation=None: None
        win._load_thread_view = lambda msg, generation=None: self.fail(
            "thread path should not run"
        )
        win._sync_backend_cached_read_state = lambda msg, is_read: sync_calls.append(
            (msg["uid"], is_read)
        )
        win._refresh_provider_counts_for_message = lambda msg, backend=None: (
            refresh_calls.append((msg["uid"], getattr(backend, "identity", None)))
        )
        win._message_filter = mock.Mock()
        win._update_message_empty_state = lambda: None

        with mock.patch.object(
            window_module, "get_settings", return_value={"mark_read_on_open": True}
        ):
            win._commit_email_selection(item)

        self.assertEqual(sync_calls, [("1", True)])
        self.assertEqual(refresh_calls, [("1", None)])
        self.assertTrue(item.msg["is_read"])

    def test_update_message_info_bar_disables_markup_for_normal_message(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._message_info_sender = _DummyLabel()
        win._message_info_date = _DummyLabel()
        win._message_info_subject = _DummyLabel()
        win._message_info_meta = _DummyLabel()
        win._message_info_bar = _DummyLabel()
        win._format_message_size = lambda msg, attachments=None: ""

        win._update_message_info_bar(_message("1"), attachments=[])

        self.assertFalse(win._message_info_sender.use_markup)
        self.assertEqual(win._message_info_sender.label, "Tester <tester@example.com>")

    def test_original_button_shown_only_for_single_message(self):
        # Header Original button is always hidden; thread bubbles provide their
        # own per-message original affordance now.
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        btn = mock.Mock()
        win._message_info_original_btn = btn
        win._thread_view_active = False

        win._set_original_message_source("Subject", "<html></html>", "text")
        btn.set_visible.assert_called_once_with(False)
        btn.reset_mock()

        win._set_original_message_source("Subject", None, None)
        btn.set_visible.assert_called_once_with(False)
        btn.reset_mock()

        win._thread_view_active = True
        win._set_original_message_source("Subject", "<html></html>", "text")
        btn.set_visible.assert_called_once_with(False)

    def test_webview_decide_policy_opens_original_dialog_for_custom_uri(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._thread_original_sources = {
            "1": {"subject": "Subject", "html": "<html></html>", "text": "text"}
        }
        win._set_original_message_source = mock.Mock()
        win._show_original_message_dialog = mock.Mock()
        decision = mock.Mock()
        request = mock.Mock()
        nav = mock.Mock()
        request.get_uri.return_value = "hermod://original?uid=1"
        nav.get_request.return_value = request
        decision.get_navigation_action.return_value = nav

        result = window_module.HermodWindow._on_webview_decide_policy(
            win,
            None,
            decision,
            window_module.WebKit.PolicyDecisionType.NAVIGATION_ACTION,
        )

        self.assertTrue(result)
        decision.ignore.assert_called_once_with()
        win._set_original_message_source.assert_called_once_with(
            "Subject", "<html></html>", "text"
        )
        win._show_original_message_dialog.assert_called_once_with()

    def test_webview_decide_policy_launches_external_links(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        decision = mock.Mock()
        request = mock.Mock()
        nav = mock.Mock()
        request.get_uri.return_value = "https://example.com"
        nav.get_request.return_value = request
        decision.get_navigation_action.return_value = nav
        with mock.patch.object(
            window_module.Gio.AppInfo, "launch_default_for_uri"
        ) as launch:
            result = window_module.HermodWindow._on_webview_decide_policy(
                win,
                None,
                decision,
                window_module.WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            )
        self.assertTrue(result)
        launch.assert_called_once_with("https://example.com", None)
        decision.ignore.assert_called_once_with()

    def test_update_account_counts_defers_render_during_startup(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win._startup_status_active = True
        win._unread_counts = collections.defaultdict(
            lambda: {"inbox": 0, "trash": 0, "spam": 0}
        )
        win._folder_rows = {}
        inbox_row = mock.Mock()
        trash_row = mock.Mock()
        spam_row = mock.Mock()
        header = mock.Mock()
        header.backend = mock.Mock(
            FOLDERS=[
                ("inbox", "Inbox", None),
                ("trash", "Trash", None),
                ("spam", "Spam", None),
            ]
        )
        all_inboxes = mock.Mock()
        win._folder_rows[("acct", "inbox")] = inbox_row
        win._folder_rows[("acct", "trash")] = trash_row
        win._folder_rows[("acct", "spam")] = spam_row
        win._account_state = {"acct": {"header": header}}
        win._all_inboxes_row = all_inboxes

        window_module.HermodWindow.update_account_counts(
            win, "acct", inbox_count=3, trash_count=1, spam_count=2
        )

        self.assertEqual(win._unread_counts["acct"]["inbox"], 3)
        inbox_row.set_count.assert_not_called()
        header.set_count.assert_not_called()
        all_inboxes.set_count.assert_not_called()

        win._startup_status_active = False
        window_module.HermodWindow._refresh_all_unread_counts(win)

        inbox_row.set_count.assert_called_once_with(3)
        trash_row.set_count.assert_called_once_with(1, dim=True)
        spam_row.set_count.assert_called_once_with(2, dim=True)
        header.set_count.assert_called_once_with(3)
        all_inboxes.set_count.assert_called_once_with(3)

    def test_startup_completion_waits_for_all_account_count_results(self):
        win = window_module.HermodWindow.__new__(window_module.HermodWindow)
        win.backends = [mock.Mock(identity="acct-a"), mock.Mock(identity="acct-b")]
        win._startup_status_active = True
        win._startup_visible_ready = False
        win._startup_counts_ready = False
        win._startup_counts_seen = set()
        win._startup_status_complete_id = None
        win.set_syncing = lambda syncing: None
        win.update_account_counts = lambda backend_identity, **kwargs: None
        win._background_result_affects_current_view = lambda result: False
        scheduled = []
        with mock.patch.object(
            window_module.GLib,
            "idle_add",
            side_effect=lambda fn, *args: scheduled.append(fn) or 1,
        ):
            window_module.HermodWindow.on_background_update(
                win,
                [{"account": "acct-a", "counts": {"inbox": 2}}],
                total_new=0,
            )

            self.assertFalse(win._startup_counts_ready)
            self.assertEqual(scheduled, [])

            win._startup_visible_ready = True
            window_module.HermodWindow.on_background_update(
                win,
                [{"account": "acct-b", "counts": {"inbox": 4}}],
                total_new=0,
            )

        self.assertTrue(win._startup_counts_ready)
        self.assertEqual(len(scheduled), 1)


if __name__ == "__main__":
    unittest.main()

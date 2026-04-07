import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import window as window_module
from widgets import LoadMoreListItem, MessageListItem


def _message(uid='1'):
    return {
        'uid': uid,
        'subject': 'Subject',
        'sender_name': 'Tester',
        'sender_email': 'tester@example.com',
        'to_addrs': [],
        'cc_addrs': [],
        'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
        'is_read': False,
        'has_attachments': False,
        'snippet': '',
        'folder': 'INBOX',
        'backend': 'gmail',
        'account': 'test@example.com',
        'thread_id': '',
        'thread_source': 'gmail-imap',
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
        self.assertFalse(item.msg['is_read'])
        self.assertEqual(item.msg['thread_count'], 4)

    def test_load_more_list_item_delegates_selection(self):
        item = LoadMoreListItem()
        widget = _DummyWidget()
        item.bind_widget(widget)

        item.set_selected(True)

        self.assertTrue(widget.selected)

    def test_paged_messages_reports_has_more(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win._message_page_limit = 2

        page, has_more = win._paged_messages([_message('1'), _message('2'), _message('3')])

        self.assertEqual([msg['uid'] for msg in page], ['1', '2'])
        self.assertTrue(has_more)

    def test_build_message_items_appends_load_more_sentinel(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win._account_class_for = lambda _identity: 'account-accent-1'

        items = win._build_message_items([_message('1')], has_more=True)

        self.assertEqual(len(items), 2)
        self.assertIsInstance(items[0], MessageListItem)
        self.assertEqual(items[0].accent_class, 'account-accent-1')
        self.assertIsInstance(items[1], LoadMoreListItem)

    def test_load_more_request_advances_page_limit_and_refreshes(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win.current_folder = 'INBOX'
        win._message_page_limit = 100
        win._email_scroll = None
        win._pending_list_scroll_value = None
        calls = []
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: calls.append((force, preserve_selected))

        win._on_load_more_requested()

        self.assertEqual(win._message_page_limit, 200)
        self.assertEqual(calls, [(True, True)])

    def test_load_more_request_captures_scroll_position(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win.current_folder = 'INBOX'
        win._message_page_limit = 100
        win._pending_list_scroll_value = None
        adjustment = _DummyAdjustment(value=312.0)
        win._email_scroll = _DummyScroll(adjustment)
        calls = []
        win.refresh_visible_mail = lambda force=False, preserve_selected=True: calls.append((force, preserve_selected))

        win._on_load_more_requested()

        self.assertEqual(win._pending_list_scroll_value, 312.0)
        self.assertEqual(calls, [(True, True)])

    def test_restore_pending_list_scroll_clamps_and_clears(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        adjustment = _DummyAdjustment(value=0.0, lower=0.0, upper=500.0, page_size=120.0)
        win._email_scroll = _DummyScroll(adjustment)
        win._pending_list_scroll_value = 460.0

        result = win._restore_pending_list_scroll()

        self.assertFalse(result)
        self.assertEqual(adjustment.value, 380.0)
        self.assertIsNone(win._pending_list_scroll_value)

    def test_email_selection_change_does_not_auto_commit_reader(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win._suppress_email_selection = False
        win._commit_email_selection = lambda row: self.fail('selection change should not auto-commit')

        win._on_email_selected()

    def test_email_list_activation_selects_before_commit(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        item = MessageListItem(_message('1'))
        calls = []
        win._visible_message_item = lambda position: item if position == 3 else None
        win._set_selected_visible_index = lambda position, suppress=False, grab_focus=False: calls.append(
            ('select', position, suppress, grab_focus)
        )
        win._request_commit_email_selection = lambda selected_item: calls.append(('commit', selected_item))

        win._on_email_list_activated(None, 3)

        self.assertEqual(calls[0], ('select', 3, True, False))
        self.assertEqual(calls[1], ('commit', item))

    def test_commit_email_selection_updates_cached_read_state(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        item = MessageListItem(_message('1'))
        sync_calls = []
        adjust_calls = []
        win._startup_autoselect_pending = True
        win._body_load_generation = 0
        win._show_mail_view = lambda: None
        win._load_body = lambda msg, generation=None: None
        win._load_thread_view = lambda msg, generation=None: self.fail('thread path should not run')
        win._adjust_unread_count_for_message = lambda msg, delta: adjust_calls.append((msg['uid'], delta))
        win._sync_backend_cached_read_state = lambda msg, is_read: sync_calls.append((msg['uid'], is_read))

        with mock.patch.object(window_module, 'get_settings', return_value={'mark_read_on_open': True}):
            win._commit_email_selection(item)

        self.assertEqual(sync_calls, [('1', True)])
        self.assertEqual(adjust_calls, [('1', -1)])
        self.assertTrue(item.msg['is_read'])

    def test_update_message_info_bar_disables_markup_for_normal_message(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        win._message_info_sender = _DummyLabel()
        win._message_info_date = _DummyLabel()
        win._message_info_subject = _DummyLabel()
        win._message_info_meta = _DummyLabel()
        win._message_info_bar = _DummyLabel()
        win._format_message_size = lambda msg, attachments=None: ''

        win._update_message_info_bar(_message('1'), attachments=[])

        self.assertFalse(win._message_info_sender.use_markup)
        self.assertEqual(win._message_info_sender.label, 'Tester <tester@example.com>')

    def test_original_button_shown_only_for_threads(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        btn = mock.Mock()
        win._message_info_original_btn = btn
        win._thread_view_active = False

        win._set_original_message_source('Subject', '<html></html>', 'text')

        btn.set_visible.assert_called_once_with(False)
        btn.reset_mock()

        win._thread_view_active = True
        win._set_original_message_source('Subject', '<html></html>', 'text')
        btn.set_visible.assert_called_once_with(True)

    def test_original_button_checks_thread_sources(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)
        btn = mock.Mock()
        win._message_info_original_btn = btn
        win._thread_view_active = True
        win._thread_original_sources = {'xyz': {}}

        win._set_original_message_source('Subject', '<html></html>', 'text', uid='other')
        btn.set_visible.assert_called_once_with(False)
        btn.reset_mock()

        win._set_original_message_source('Subject', '<html></html>', 'text', uid='xyz')
        btn.set_visible.assert_called_once_with(True)


if __name__ == '__main__':
    unittest.main()

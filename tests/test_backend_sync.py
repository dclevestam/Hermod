import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backends as backends_module
from backends import GmailBackend, MicrosoftBackend, _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT, _aware_utc_datetime


def _message(uid, folder='INBOX', gmail_msgid='100'):
    return {
        'uid': uid,
        'subject': '(no subject)',
        'sender_name': 'Tester',
        'sender_email': 'tester@example.com',
        'to_addrs': [],
        'cc_addrs': [],
        'date': datetime.now(timezone.utc),
        'is_read': True,
        'has_attachments': False,
        'snippet': '',
        'folder': folder,
        'backend': 'gmail',
        'account': 'test@example.com',
        'backend_obj': None,
        'thread_id': '',
        'thread_source': 'gmail-imap',
        'message_id': f'<{uid}@example.com>',
        'gmail_msgid': gmail_msgid,
    }


class GmailBackendSyncTests(unittest.TestCase):
    def make_backend(self):
        backend = object.__new__(GmailBackend)
        backend.identity = 'test@gmail.com'
        backend.provider = 'gmail'
        backend._sync_lock = threading.Lock()
        backend._lock = threading.Lock()
        backend._imap = None
        backend._special_folders = {}
        backend._cached_inbox_messages = []
        backend._inbox_history_id = ''
        backend._folder_sync = {}
        backend._gmail_history_supported = True
        backend._gmail_history_seed_inflight = set()
        backend._gmail_labels_by_name = None
        backend._gmail_labels_loaded_at = ''
        return backend

    def test_gmail_label_bridge_handles_localized_special_folders(self):
        backend = self.make_backend()
        backend._special_folders = {'[Gmail]/Sent Mail': '[Google Mail]/Gesendet'}
        backend._gmail_labels_by_name = {
            'Projects/Foo': {'id': 'Label_1', 'type': 'user'},
            'Ärende': {'id': 'Label_2', 'type': 'user'},
        }

        self.assertEqual(backend.gmail_label_for_folder('INBOX')['id'], 'INBOX')
        self.assertEqual(backend.gmail_label_for_folder('[Gmail]/Sent Mail')['id'], 'SENT')
        self.assertEqual(backend.gmail_label_for_folder('[Google Mail]/Gesendet')['id'], 'SENT')
        self.assertEqual(backend.gmail_label_for_folder('Projects/Foo')['id'], 'Label_1')
        self.assertEqual(backend.gmail_label_for_folder('&AMQ-rende')['id'], 'Label_2')
        self.assertIsNone(backend.gmail_label_for_folder('[Gmail]/All Mail'))

    def test_gmail_fetch_messages_reuses_cached_sent_folder_when_unchanged(self):
        backend = self.make_backend()
        cached = [_message('1', folder='[Gmail]/Sent Mail', gmail_msgid='500')]
        backend._folder_sync['[Gmail]/Sent Mail'] = {
            'messages': cached,
            'history_id': 'history-1',
        }
        probe_calls = []
        backend._probe_cached_folder_messages = lambda folder, label_id: (
            probe_calls.append((folder, label_id)) or {'status': 'unchanged', 'messages': cached}
        )
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')

        messages = backend.fetch_messages('[Gmail]/Sent Mail', limit=10)

        self.assertEqual(messages, cached)
        self.assertEqual(probe_calls, [('[Gmail]/Sent Mail', 'SENT')])

    def test_gmail_fetch_messages_reuses_changed_sent_folder_refresh(self):
        backend = self.make_backend()
        refreshed = [_message('2', folder='[Gmail]/Sent Mail', gmail_msgid='600')]
        probe_calls = []
        refresh_calls = []
        backend._probe_cached_folder_messages = lambda folder, label_id: (
            probe_calls.append((folder, label_id)) or {
                'status': 'changed',
                'history_id': 'history-2',
                'refresh_map': {'600': 'api-600'},
                'remove_ids': set(),
            }
        )
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: (
            refresh_calls.append((folder, history_probe['history_id'], limit)) or refreshed
        )
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')

        messages = backend.fetch_messages('[Gmail]/Sent Mail', limit=10)

        self.assertEqual(messages, refreshed)
        self.assertEqual(probe_calls, [('[Gmail]/Sent Mail', 'SENT')])
        self.assertEqual(refresh_calls, [('[Gmail]/Sent Mail', 'history-2', 10)])

    def test_gmail_fetch_messages_large_limit_bypasses_cached_partial_sync(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: self.fail('cached probe should not run')
        backend._refresh_cached_folder_messages = lambda *args, **kwargs: self.fail('cached refresh should not run')
        fetched = [_message(str(idx), folder='INBOX', gmail_msgid=str(idx)) for idx in range(150)]
        fetch_calls = []
        backend._fetch_messages_imap = lambda folder, limit: (fetch_calls.append((folder, limit)) or fetched)
        backend._ensure_gmail_history_seed_async = lambda folder: None

        messages = backend.fetch_messages('INBOX', limit=150)

        self.assertEqual(len(messages), 150)
        self.assertEqual(fetch_calls, [('INBOX', 150)])

    def test_gmail_apply_history_actions_tracks_folder_membership_and_unread(self):
        backend = self.make_backend()
        actions = {}
        entry = {
            'labelsAdded': [
                {'message': {'id': '0xA'}, 'labelIds': ['UNREAD']},
                {'message': {'id': '0xB'}, 'labelIds': ['SENT']},
            ],
            'labelsRemoved': [
                {'message': {'id': '0xC'}, 'labelIds': ['UNREAD']},
                {'message': {'id': '0xD'}, 'labelIds': ['SENT']},
            ],
            'messagesAdded': [
                {'message': {'id': '0xE'}, 'labelIds': ['SENT']},
            ],
            'messagesDeleted': [
                {'message': {'id': '0xF'}, 'labelIds': ['SENT']},
            ],
        }

        backend._apply_history_actions(actions, entry, label_id='SENT')

        self.assertEqual(actions['10']['action'], 'refresh')
        self.assertEqual(actions['11']['action'], 'refresh')
        self.assertEqual(actions['12']['action'], 'refresh')
        self.assertEqual(actions['13']['action'], 'remove')
        self.assertEqual(actions['14']['action'], 'refresh')
        self.assertEqual(actions['15']['action'], 'remove')

    def test_gmail_non_inbox_sync_state_persists(self):
        backend = self.make_backend()
        sent_messages = [_message('42', folder='[Gmail]/Sent Mail', gmail_msgid='900')]

        with mock.patch.object(backends_module, 'set_account_state') as set_state:
            backend._update_folder_sync_state('[Gmail]/Sent Mail', messages=sent_messages, history_id='history-sent')

        provider, identity, state = set_state.call_args.args
        self.assertEqual(provider, 'gmail')
        self.assertEqual(identity, 'test@gmail.com')
        self.assertEqual(state['folders']['[Gmail]/Sent Mail']['history_id'], 'history-sent')
        self.assertEqual(state['folders']['[Gmail]/Sent Mail']['messages'][0]['uid'], '42')

    def test_gmail_deserialize_sync_messages_normalizes_naive_dates(self):
        backend = self.make_backend()

        restored = backend._deserialize_sync_messages([
            {
                'uid': '42',
                'subject': 'Subject',
                'sender_name': 'Tester',
                'sender_email': 'tester@example.com',
                'to_addrs': [],
                'cc_addrs': [],
                'date': '2026-04-07T08:30:00',
                'is_read': True,
                'has_attachments': False,
                'snippet': '',
                'folder': 'INBOX',
                'thread_id': '',
                'thread_source': 'gmail-imap',
                'message_id': '<42@example.com>',
                'gmail_msgid': '42',
            }
        ])

        self.assertEqual(restored[0]['date'].tzinfo, timezone.utc)

    def test_gmail_background_updates_report_new_inbox_messages(self):
        backend = self.make_backend()
        existing = _message('1', folder='INBOX', gmail_msgid='100')
        backend._cached_inbox_messages = [existing]
        backend._inbox_history_id = 'history-1'
        new_message = _message('2', folder='INBOX', gmail_msgid='200')
        new_message['is_read'] = False
        backend._probe_cached_folder_messages = lambda folder, label_id: {
            'status': 'changed',
            'history_id': 'history-2',
            'refresh_map': {'100': 'api-100', '200': 'api-200'},
            'remove_ids': set(),
            'new_ids': {'200'},
        } if folder == 'INBOX' else None
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: [existing, new_message]
        backend.get_unread_count = mock.Mock(side_effect=lambda folder='INBOX': 4 if folder == 'INBOX' else 0)

        result = backend.check_background_updates(tracked_folders=['INBOX'], reconcile_counts=False)

        self.assertEqual(result['changed_folders'], {'INBOX'})
        self.assertEqual([msg['gmail_msgid'] for msg in result['new_messages']], ['200'])
        self.assertEqual(result['counts'], {'inbox': 4})

    def test_gmail_background_updates_do_not_notify_unread_toggles(self):
        backend = self.make_backend()
        existing = _message('1', folder='INBOX', gmail_msgid='100')
        backend._cached_inbox_messages = [existing]
        backend._inbox_history_id = 'history-1'
        toggled = dict(existing)
        toggled['is_read'] = False
        backend._probe_cached_folder_messages = lambda folder, label_id: {
            'status': 'changed',
            'history_id': 'history-2',
            'refresh_map': {'100': 'api-100'},
            'remove_ids': set(),
            'new_ids': set(),
        } if folder == 'INBOX' else None
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: [toggled]
        backend.get_unread_count = mock.Mock(side_effect=lambda folder='INBOX': 1 if folder == 'INBOX' else 0)

        result = backend.check_background_updates(tracked_folders=['INBOX'], reconcile_counts=False)

        self.assertEqual(result['changed_folders'], {'INBOX'})
        self.assertEqual(result['new_messages'], [])
        self.assertEqual(result['counts'], {'inbox': 1})


class BackendDatetimeTests(unittest.TestCase):
    def test_aware_utc_datetime_promotes_naive_values(self):
        naive = datetime(2026, 4, 7, 8, 30)

        aware = _aware_utc_datetime(naive)

        self.assertEqual(aware.tzinfo, timezone.utc)
        self.assertEqual(aware.hour, 8)


class MicrosoftBackendSyncTests(unittest.TestCase):
    def test_microsoft_prunes_stale_custom_folder_state(self):
        backend = object.__new__(MicrosoftBackend)
        backend.identity = 'test@example.com'
        backend.provider = 'microsoft'
        backend._sync_lock = threading.Lock()
        backend._folder_sync = {
            'inbox': {
                'messages': [{'uid': 'inbox'}],
                'delta_link': 'delta-inbox',
                'bootstrap_inflight': False,
                'last_accessed_at': '2000-01-01T00:00:00+00:00',
            },
            'custom-stale': {
                'messages': [{'uid': 'stale'}],
                'delta_link': 'delta-stale',
                'bootstrap_inflight': False,
                'last_accessed_at': (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
            },
        }
        for idx in range(_GRAPH_SYNC_CUSTOM_FOLDER_LIMIT + 5):
            folder = f'custom-{idx:02d}'
            backend._folder_sync[folder] = {
                'messages': [{'uid': folder}],
                'delta_link': f'delta-{folder}',
                'bootstrap_inflight': False,
                'last_accessed_at': (datetime.now(timezone.utc) - timedelta(hours=idx)).isoformat(),
            }

        with mock.patch.object(backends_module, 'set_account_state') as set_state:
            backend._persist_sync_state()

        self.assertNotIn('custom-stale', backend._folder_sync)
        self.assertIn('inbox', backend._folder_sync)
        custom_kept = [folder for folder in backend._folder_sync if folder.startswith('custom-')]
        self.assertEqual(len(custom_kept), _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT)
        saved_folders = set_state.call_args.args[2]['folders']
        self.assertEqual(len(saved_folders), 1 + _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT)

    def test_microsoft_fetch_messages_large_limit_bypasses_delta_cache(self):
        backend = object.__new__(MicrosoftBackend)
        backend.identity = 'test@example.com'
        backend.provider = 'microsoft'
        backend._sync_lock = threading.Lock()
        backend._folder_sync = {
            'inbox': {
                'messages': [{'uid': 'cached'}],
                'delta_link': 'delta-inbox',
                'bootstrap_inflight': False,
                'last_accessed_at': '',
            },
        }
        backend._fetch_messages_full = lambda folder, limit: [{'uid': str(idx)} for idx in range(limit)]
        backend._run_folder_delta = lambda *args, **kwargs: self.fail('delta path should not run')
        backend._ensure_folder_delta_bootstrap_async = lambda folder: None
        backend._update_folder_sync_state = lambda folder, messages=None, delta_link=None: None

        messages = backend.fetch_messages('inbox', limit=150)

        self.assertEqual(len(messages), 150)

    def test_microsoft_background_updates_report_new_inbox_messages(self):
        backend = object.__new__(MicrosoftBackend)
        backend.identity = 'test@example.com'
        backend.provider = 'microsoft'
        backend._sync_lock = threading.Lock()
        existing = {
            'uid': 'old',
            'subject': 'Old',
            'sender_name': 'Tester',
            'sender_email': 'tester@example.com',
            'to_addrs': [],
            'cc_addrs': [],
            'date': datetime.now(timezone.utc),
            'is_read': True,
            'has_attachments': False,
            'snippet': '',
            'folder': 'inbox',
            'backend': 'microsoft',
            'account': 'test@example.com',
            'backend_obj': None,
            'thread_id': '',
            'thread_source': 'microsoft-graph',
            'message_id': '<old@example.com>',
        }
        new_message = dict(existing)
        new_message['uid'] = 'new'
        new_message['is_read'] = False
        backend._folder_sync = {
            'inbox': {
                'messages': [existing],
                'delta_link': 'delta-1',
                'bootstrap_inflight': False,
                'last_accessed_at': '',
            },
        }
        backend._run_folder_delta = lambda folder, delta_link, return_delta_info=False: (
            [new_message, existing],
            'delta-2',
            {'added_ids': {'new'}, 'removed_ids': set(), 'touched_ids': {'new'}},
        )
        backend._update_folder_sync_state = mock.Mock()
        backend.get_unread_count = mock.Mock(side_effect=lambda folder='inbox': 3 if folder == 'inbox' else 0)

        result = backend.check_background_updates(tracked_folders=['inbox'], reconcile_counts=False)

        self.assertEqual(result['changed_folders'], {'inbox'})
        self.assertEqual([msg['uid'] for msg in result['new_messages']], ['new'])
        self.assertEqual(result['counts'], {'inbox': 3})


if __name__ == '__main__':
    unittest.main()

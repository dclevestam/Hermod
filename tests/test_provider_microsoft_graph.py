import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers.microsoft_graph as microsoft_module
from providers.microsoft_graph import MicrosoftBackend, _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT


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

        with mock.patch.object(microsoft_module, 'set_account_state') as set_state:
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

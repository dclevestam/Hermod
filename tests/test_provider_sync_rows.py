import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.sync_rows import deserialize_sync_messages, serialize_sync_messages


class SyncRowHelperTests(unittest.TestCase):
    def test_serialize_sync_messages_applies_defaults_and_extra_keys(self):
        source_date = datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc)
        rows = [
            {
                'uid': '42',
                'subject': 'Subject',
                'sender_name': 'Tester',
                'sender_email': 'tester@example.com',
                'to_addrs': ['friend@example.com'],
                'cc_addrs': [],
                'date': source_date,
                'is_read': False,
                'has_attachments': True,
                'snippet': 'Preview',
                'message_id': '<42@example.com>',
                'gmail_msgid': 'abc123',
            }
        ]

        serial = serialize_sync_messages(
            rows,
            limit=10,
            default_folder='INBOX',
            default_thread_source='gmail-imap',
            extra_keys=('gmail_msgid',),
        )

        self.assertEqual(len(serial), 1)
        self.assertEqual(serial[0]['folder'], 'INBOX')
        self.assertEqual(serial[0]['thread_source'], 'gmail-imap')
        self.assertEqual(serial[0]['date'], '2026-04-07T08:30:00+00:00')
        self.assertEqual(serial[0]['gmail_msgid'], 'abc123')

    def test_deserialize_sync_messages_restores_backend_context_and_sorts(self):
        backend_obj = object()
        restored = deserialize_sync_messages(
            [
                {
                    'uid': 'older',
                    'subject': 'Older',
                    'date': '2026-04-07T08:30:00',
                    'gmail_msgid': '100',
                },
                {
                    'uid': 'newer',
                    'subject': 'Newer',
                    'date': '2026-04-07T09:30:00+00:00',
                    'folder': 'Sent',
                    'thread_source': 'gmail-api',
                    'gmail_msgid': '101',
                },
            ],
            limit=10,
            default_folder='INBOX',
            provider_name='gmail',
            identity='test@gmail.com',
            backend_obj=backend_obj,
            default_thread_source='gmail-imap',
            extra_keys=('gmail_msgid',),
        )

        self.assertEqual([row['uid'] for row in restored], ['newer', 'older'])
        self.assertEqual(restored[0]['backend'], 'gmail')
        self.assertEqual(restored[0]['account'], 'test@gmail.com')
        self.assertIs(restored[0]['backend_obj'], backend_obj)
        self.assertEqual(restored[0]['folder'], 'Sent')
        self.assertEqual(restored[1]['folder'], 'INBOX')
        self.assertEqual(restored[0]['thread_source'], 'gmail-api')
        self.assertEqual(restored[1]['thread_source'], 'gmail-imap')
        self.assertEqual(restored[0]['gmail_msgid'], '101')
        self.assertEqual(restored[1]['date'].tzinfo, timezone.utc)

    def test_deserialize_sync_messages_limits_results(self):
        rows = deserialize_sync_messages(
            [
                {'uid': '1', 'date': '2026-04-07T07:00:00+00:00'},
                {'uid': '2', 'date': '2026-04-07T08:00:00+00:00'},
                {'uid': '3', 'date': '2026-04-07T09:00:00+00:00'},
            ],
            limit=2,
            default_folder='INBOX',
            provider_name='gmail',
            identity='test@gmail.com',
            backend_obj=None,
            default_thread_source='gmail-imap',
        )

        self.assertEqual([row['uid'] for row in rows], ['3', '2'])


if __name__ == '__main__':
    unittest.main()

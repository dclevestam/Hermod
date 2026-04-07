import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import snapshot_cache as snapshot_cache_module


def _message(uid='1', folder='INBOX'):
    return {
        'uid': uid,
        'subject': 'Subject',
        'sender_name': 'Tester',
        'sender_email': 'tester@example.com',
        'to_addrs': ['dest@example.com'],
        'cc_addrs': [],
        'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
        'is_read': False,
        'has_attachments': True,
        'snippet': 'Snippet',
        'folder': folder,
        'backend': 'gmail',
        'account': 'test@example.com',
        'thread_id': 'thread-1',
        'thread_source': 'gmail-imap',
    }


class SnapshotCacheTests(unittest.TestCase):
    def test_build_snapshot_payload_normalizes_messages(self):
        payload = snapshot_cache_module.build_snapshot_payload(
            'scope',
            ['test@example.com'],
            [_message()],
            'INBOX',
        )

        self.assertEqual(payload['scope'], 'scope')
        self.assertEqual(payload['accounts'], ['test@example.com'])
        self.assertEqual(len(payload['messages']), 1)
        self.assertEqual(payload['messages'][0]['date'], '2026-04-07T08:30:00+00:00')
        self.assertEqual(payload['messages'][0]['folder'], 'INBOX')

    def test_snapshot_result_applicable_rejects_stale_or_replaced_results(self):
        self.assertTrue(snapshot_cache_module.snapshot_result_applicable(2, 2, 1))
        self.assertFalse(snapshot_cache_module.snapshot_result_applicable(1, 2, 0))
        self.assertFalse(snapshot_cache_module.snapshot_result_applicable(2, 2, 2))

    def test_store_and_load_snapshot_payload_round_trip(self):
        payload = snapshot_cache_module.build_snapshot_payload(
            'scope',
            ['test@example.com'],
            [_message()],
            'INBOX',
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'snapshot.json.gz'
            with mock.patch.object(snapshot_cache_module, '_snapshot_path', return_value=path):
                snapshot_cache_module.store_snapshot_payload('scope', payload)
                loaded = snapshot_cache_module.load_snapshot_payload('scope')

        self.assertEqual(loaded, payload)

    def test_snapshot_save_queue_coalesces_pending_writes_per_scope(self):
        writes = []
        first_write_started = threading.Event()
        release_first_write = threading.Event()

        def writer(scope, payload):
            writes.append((scope, payload['version']))
            if payload['version'] == 1:
                first_write_started.set()
                self.assertTrue(release_first_write.wait(1.0))

        queue = snapshot_cache_module.SnapshotSaveQueue(writer=writer)
        queue.enqueue('scope', {'version': 1})
        self.assertTrue(first_write_started.wait(1.0))
        queue.enqueue('scope', {'version': 2})
        queue.enqueue('scope', {'version': 3})
        release_first_write.set()

        deadline = time.time() + 1.0
        while time.time() < deadline:
            if writes == [('scope', 1), ('scope', 3)] and not queue._worker_running:
                break
            time.sleep(0.01)

        self.assertEqual(writes, [('scope', 1), ('scope', 3)])
        self.assertFalse(queue._worker_running)


if __name__ == '__main__':
    unittest.main()

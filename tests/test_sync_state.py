import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sync_state


class SyncStateTests(unittest.TestCase):
    def test_prune_account_states_removes_stale_accounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            state_file = tmpdir / 'sync-state.json'
            with mock.patch.object(sync_state, '_SYNC_STATE_DIR', tmpdir), \
                 mock.patch.object(sync_state, '_SYNC_STATE_FILE', state_file):
                sync_state.set_account_state('gmail', 'keep@example.com', {'folders': {'INBOX': {}}})
                sync_state.set_account_state('gmail', 'remove@example.com', {'folders': {'INBOX': {}}})

                removed = sync_state.prune_account_states('gmail', ['keep@example.com'])

                self.assertEqual(removed, ['remove@example.com'])
                self.assertEqual(
                    sync_state.list_account_states('gmail'),
                    {'keep@example.com': {'folders': {'INBOX': {}}}},
                )


if __name__ == '__main__':
    unittest.main()

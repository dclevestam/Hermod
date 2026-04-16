import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.account_prefs import (
    AccountPreferenceRecord,
    get_account_preference_record,
    merge_account_preference,
    prune_account_preferences,
    remove_account_preference,
    upsert_account_preference,
)
from accounts.descriptors import AccountDescriptor
from accounts.native_store import (
    NativeAccountRecord,
    get_native_account_descriptors,
    get_native_account_record,
    remove_native_account,
    upsert_native_account_with_prefs,
)


class AccountPreferenceTests(unittest.TestCase):
    def test_merge_account_preference_applies_alias_and_color(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            prefs_file = tmpdir / 'account-prefs.json'
            with mock.patch('accounts.account_prefs._PREFS_FILE', prefs_file):
                upsert_account_preference(AccountPreferenceRecord(
                    source='native',
                    provider_kind='gmail',
                    identity='user@example.com',
                    alias='Work',
                    accent_color='#3366ff',
                    enabled=True,
                ))
                descriptor = AccountDescriptor(
                    source='native',
                    provider_kind='gmail',
                    identity='user@example.com',
                    presentation_name='Original',
                    metadata={'accent_color': '#aaaaaa'},
                )
                merged = merge_account_preference(descriptor, default_source='native')

                self.assertIsNotNone(merged)
                self.assertEqual(merged.presentation_name, 'Work')
                self.assertEqual(merged.metadata['accent_color'], '#3366ff')

    def test_merge_account_preference_respects_disabled_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            prefs_file = tmpdir / 'account-prefs.json'
            with mock.patch('accounts.account_prefs._PREFS_FILE', prefs_file):
                upsert_account_preference(AccountPreferenceRecord(
                    source='native',
                    provider_kind='gmail',
                    identity='user@example.com',
                    alias='Hidden',
                    accent_color='',
                    enabled=False,
                ))
                descriptor = AccountDescriptor(
                    source='native',
                    provider_kind='gmail',
                    identity='user@example.com',
                )

                self.assertIsNone(merge_account_preference(descriptor, default_source='native'))

    def test_native_account_persistence_and_removal_clears_prefs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            accounts_file = tmpdir / 'native-accounts.json'
            prefs_file = tmpdir / 'account-prefs.json'
            record = NativeAccountRecord(
                id='native-1',
                provider_kind='imap-smtp',
                identity='user@example.com',
                presentation_name='Work',
                alias='Work',
                accent_color='#112233',
                config={
                    'imap_host': 'imap.example.com',
                    'smtp_host': 'smtp.example.com',
                },
                enabled=True,
            )
            with mock.patch('accounts.native_store._NATIVE_ACCOUNTS_FILE', accounts_file), \
                 mock.patch('accounts.account_prefs._PREFS_FILE', prefs_file), \
                 mock.patch('accounts.native_store.clear_native_password', return_value=True):
                upsert_native_account_with_prefs(record)

                descriptors = get_native_account_descriptors()
                self.assertEqual(len(descriptors), 1)
                self.assertEqual(descriptors[0].presentation_name, 'Work')

                self.assertIsNotNone(get_native_account_record('native-1'))
                self.assertIsNotNone(get_account_preference_record('native', 'imap-smtp', 'user@example.com'))

                removed = remove_native_account('native-1')
                self.assertTrue(removed)
                self.assertIsNone(get_native_account_record('native-1'))
                self.assertIsNone(get_account_preference_record('native', 'imap-smtp', 'user@example.com'))
                self.assertFalse(remove_account_preference('native', 'imap-smtp', 'user@example.com'))

    def test_prune_account_preferences_keeps_active_hidden_accounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            prefs_file = tmpdir / 'account-prefs.json'
            with mock.patch('accounts.account_prefs._PREFS_FILE', prefs_file):
                upsert_account_preference(AccountPreferenceRecord(
                    source='native',
                    provider_kind='gmail',
                    identity='keep@example.com',
                    alias='Keep',
                    accent_color='#123456',
                    enabled=False,
                ))
                upsert_account_preference(AccountPreferenceRecord(
                    source='native',
                    provider_kind='gmail',
                    identity='drop@example.com',
                    alias='Drop',
                    accent_color='#654321',
                    enabled=True,
                ))
                removed = prune_account_preferences([
                    ('native', 'gmail', 'keep@example.com'),
                ])

                self.assertEqual(
                    [(row.source, row.provider_kind, row.identity) for row in removed],
                    [('native', 'gmail', 'drop@example.com')],
                )
                self.assertIsNotNone(get_account_preference_record('native', 'gmail', 'keep@example.com'))
                self.assertIsNone(get_account_preference_record('native', 'gmail', 'drop@example.com'))


if __name__ == '__main__':
    unittest.main()

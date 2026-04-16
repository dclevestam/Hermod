import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backends
from accounts.descriptors import AccountDescriptor


class _NativeRecord:
    def __init__(self, provider_kind, identity, enabled=True):
        self.provider_kind = provider_kind
        self.identity = identity
        self.enabled = enabled


class BackendsTests(unittest.TestCase):
    def test_reconcile_account_inventory_prunes_stale_sync_state(self):
        descriptors = [
            AccountDescriptor(
                source='native',
                provider_kind='gmail',
                identity='keep@example.com',
            ),
            AccountDescriptor(
                source='native',
                provider_kind='imap-smtp',
                identity='imap@example.com',
            ),
        ]

        prune_calls = []
        inventory = [
            _NativeRecord('gmail', 'keep@example.com'),
            _NativeRecord('imap-smtp', 'imap@example.com'),
        ]
        with mock.patch.object(backends, 'list_native_account_records', return_value=inventory), \
             mock.patch.object(backends, 'get_native_account_descriptors', return_value=descriptors), \
             mock.patch.object(backends, 'prune_account_states', side_effect=lambda provider, active: (
                 prune_calls.append((provider, tuple(sorted(active)))) or (['stale@example.com'] if provider == 'gmail' else [])
             )), \
             mock.patch.object(backends, 'log_event') as log_event:
            result_descriptors, removed = backends.reconcile_account_inventory()

        self.assertEqual(result_descriptors, descriptors)
        self.assertEqual(prune_calls, [
            ('gmail', ('keep@example.com',)),
            ('microsoft', ()),
            ('imap', ('imap@example.com',)),
        ])
        self.assertEqual(removed, {'gmail': ['stale@example.com']})
        self.assertTrue(log_event.called)

    def test_reconcile_account_inventory_prunes_missing_provider_state(self):
        descriptors = [
            AccountDescriptor(
                source='native',
                provider_kind='imap-smtp',
                identity='imap@example.com',
            ),
        ]

        prune_calls = []
        inventory = [
            _NativeRecord('imap-smtp', 'imap@example.com'),
        ]
        with mock.patch.object(backends, 'list_native_account_records', return_value=inventory), \
             mock.patch.object(backends, 'get_native_account_descriptors', return_value=descriptors), \
             mock.patch.object(backends, 'prune_account_states', side_effect=lambda provider, active: (
                 prune_calls.append((provider, tuple(sorted(active)))) or (['stale@example.com'] if provider == 'gmail' else [])
             )), \
             mock.patch.object(backends, 'log_event') as log_event:
            result_descriptors, removed = backends.reconcile_account_inventory()

        self.assertEqual(result_descriptors, descriptors)
        self.assertEqual(prune_calls, [
            ('gmail', ()),
            ('microsoft', ()),
            ('imap', ('imap@example.com',)),
        ])
        self.assertEqual(removed, {'gmail': ['stale@example.com']})
        self.assertTrue(log_event.called)

    def test_get_backends_reconciles_before_creating(self):
        descriptors = [AccountDescriptor(source='native', provider_kind='gmail', identity='keep@example.com')]
        with mock.patch.object(backends, 'reconcile_account_inventory', return_value=(descriptors, {})) as reconcile, \
             mock.patch.object(backends._PROVIDER_REGISTRY, 'create_backends', return_value=['backend']) as create_backends:
            result = backends.get_backends()

        self.assertEqual(result, ['backend'])
        reconcile.assert_called_once()
        create_backends.assert_called_once_with(descriptors)

    def test_provider_registry_creates_lazy_backends(self):
        registry = backends.ProviderRegistry()
        factory_calls = []

        class _Backend:
            FOLDERS = [('INBOX', 'Inbox', 'mail-inbox-symbolic')]

            def __init__(self, descriptor):
                factory_calls.append(descriptor.identity)
                self.identity = descriptor.identity
                self.provider = 'gmail'
                self.FOLDERS = self.FOLDERS

            def ping(self):
                return 'pong'

        registry.register('gmail', _Backend)
        descriptor = AccountDescriptor(
            source='native',
            provider_kind='gmail',
            identity='lazy@example.com',
        )

        backend = registry.create_backends([descriptor])[0]

        self.assertEqual(factory_calls, [])
        self.assertFalse(backend.is_loaded)
        self.assertEqual(backend.identity, 'lazy@example.com')
        self.assertEqual(backend.FOLDERS, [('INBOX', 'Inbox', 'mail-inbox-symbolic')])
        self.assertEqual(backend.ping(), 'pong')
        self.assertTrue(backend.is_loaded)
        self.assertEqual(factory_calls, ['lazy@example.com'])


if __name__ == '__main__':
    unittest.main()

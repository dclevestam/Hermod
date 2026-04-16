import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.descriptors import AccountDescriptor
from accounts.registry import ProviderRegistry
from backends import describe_sync_policies


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_creates_backend_from_descriptor(self):
        registry = ProviderRegistry()
        descriptor = AccountDescriptor(
            source='test',
            provider_kind='gmail',
            identity='user@example.com',
        )

        registry.register('gmail', lambda account: ('backend', account.identity))

        self.assertEqual(registry.create_backend(descriptor), ('backend', 'user@example.com'))

    def test_lazy_registry_maps_imap_smtp_provider_name(self):
        registry = ProviderRegistry()
        descriptor = AccountDescriptor(
            source='test',
            provider_kind='imap-smtp',
            identity='user@example.com',
        )

        registry.register('imap-smtp', lambda account: type('Backend', (), {
            'identity': account.identity,
            'provider': 'imap',
            'FOLDERS': [('INBOX', 'Inbox', 'mail-inbox-symbolic')],
        })())

        backend = registry.create_backends([descriptor])[0]

        self.assertEqual(backend.provider, 'imap')
        self.assertEqual(backend.identity, 'user@example.com')

    def test_registry_rejects_duplicate_provider_kind(self):
        registry = ProviderRegistry()
        registry.register('gmail', lambda account: account)

        with self.assertRaises(ValueError):
            registry.register('gmail', lambda account: account)

    def test_describe_sync_policies_uses_backend_contract(self):
        class _Backend:
            identity = 'user@example.com'
            provider = 'gmail'

            def get_sync_policy(self):
                return {
                    'provider': 'gmail',
                    'primary': 'api',
                    'fallback': 'imap',
                    'reconcile': 'history',
                }

            def get_unread_count_policy(self, folder='inbox', force_primary=False):
                return {
                    'provider': 'gmail',
                    'primary': 'api',
                    'fallback': 'imap',
                    'reconcile': 'history',
                    'route': 'fallback',
                    'source': 'imap-unseen',
                }

        policies = describe_sync_policies([_Backend()])

        self.assertEqual(policies, [{
            'account': 'user@example.com',
            'provider': 'gmail',
            'policy': {
                'provider': 'gmail',
                'primary': 'api',
                'fallback': 'imap',
                'reconcile': 'history',
            },
            'count_policy': {
                'provider': 'gmail',
                'primary': 'api',
                'fallback': 'imap',
                'reconcile': 'history',
                'route': 'fallback',
                'source': 'imap-unseen',
            },
        }])


if __name__ == '__main__':
    unittest.main()

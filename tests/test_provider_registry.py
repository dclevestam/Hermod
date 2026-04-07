import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.descriptors import AccountDescriptor
from accounts.registry import ProviderRegistry


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

    def test_registry_rejects_duplicate_provider_kind(self):
        registry = ProviderRegistry()
        registry.register('gmail', lambda account: account)

        with self.assertRaises(ValueError):
            registry.register('gmail', lambda account: account)


if __name__ == '__main__':
    unittest.main()

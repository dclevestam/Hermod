import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.sources.goa import descriptor_from_goa_object


class GoaDescriptorTests(unittest.TestCase):
    def make_goa_object(self, provider_type='google', mail_disabled=False, oauth2=True, identity='user@example.com'):
        class _Props:
            pass

        class _Account:
            def __init__(self):
                self.props = _Props()
                self.props.provider_type = provider_type
                self.props.mail_disabled = mail_disabled
                self.props.presentation_identity = identity

        class _GoaObject:
            def get_account(self):
                return _Account()

            def get_oauth2_based(self):
                return object() if oauth2 else None

        return _GoaObject()

    def test_descriptor_from_goa_object_maps_google_to_gmail(self):
        descriptor = descriptor_from_goa_object(self.make_goa_object(provider_type='google'))

        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.provider_kind, 'gmail')
        self.assertEqual(descriptor.auth_kind, 'goa-oauth2')
        self.assertEqual(descriptor.source, 'goa')

    def test_descriptor_from_goa_object_skips_unsupported_accounts(self):
        self.assertIsNone(descriptor_from_goa_object(self.make_goa_object(provider_type='imap')))
        self.assertIsNone(descriptor_from_goa_object(self.make_goa_object(mail_disabled=True)))
        self.assertIsNone(descriptor_from_goa_object(self.make_goa_object(oauth2=False)))


if __name__ == '__main__':
    unittest.main()

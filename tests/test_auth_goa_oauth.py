import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.auth.goa_oauth import get_goa_access_token


class GoaOAuthTests(unittest.TestCase):
    def test_get_goa_access_token_returns_token(self):
        class _OAuthProxy:
            def call_get_access_token_sync(self, _cancellable):
                return ('token-123', None, None)

        class _Account:
            def call_ensure_credentials_sync(self, _cancellable):
                return None

        class _GoaObject:
            def get_oauth2_based(self):
                return _OAuthProxy()

        token = get_goa_access_token(_GoaObject(), _Account(), network_ready_fn=lambda: True)

        self.assertEqual(token, 'token-123')


if __name__ == '__main__':
    unittest.main()

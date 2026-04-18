import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.auth import google_native


class GoogleNativeTests(unittest.TestCase):
    def test_exchange_google_auth_code_includes_client_secret_when_present(self):
        with mock.patch.object(
            google_native, "_json_request", return_value={"access_token": "token"}
        ) as json_request:
            with mock.patch.object(
                google_native,
                "_token_bundle_from_response",
                return_value={"access_token": "token"},
            ):
                google_native.exchange_google_auth_code(
                    "client-id",
                    "code",
                    "verifier",
                    "http://127.0.0.1/callback",
                    client_secret="super-secret",
                )

        payload = json_request.call_args.kwargs["data"]
        self.assertEqual(payload["client_secret"], "super-secret")

    def test_refresh_google_access_token_omits_client_secret_when_blank(self):
        with mock.patch.object(
            google_native, "_json_request", return_value={"access_token": "token"}
        ) as json_request:
            with mock.patch.object(
                google_native,
                "_token_bundle_from_response",
                return_value={"access_token": "token"},
            ):
                google_native.refresh_google_access_token("client-id", "refresh-token")

        payload = json_request.call_args.kwargs["data"]
        self.assertNotIn("client_secret", payload)


if __name__ == "__main__":
    unittest.main()

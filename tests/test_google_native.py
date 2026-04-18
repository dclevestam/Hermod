import sys
import unittest
from pathlib import Path
import urllib.parse
import urllib.request
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

    def test_oauth_callback_ignores_followup_favicon_request(self):
        def fake_open_browser(url):
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            redirect_uri = query["redirect_uri"][0]
            state = query["state"][0]

            callback_url = redirect_uri + "?" + urllib.parse.urlencode(
                {"code": "auth-code", "state": state}
            )
            with urllib.request.urlopen(callback_url) as response:
                response.read()

            redirect_parts = urllib.parse.urlparse(redirect_uri)
            favicon_url = (
                f"{redirect_parts.scheme}://{redirect_parts.netloc}/favicon.ico"
            )
            with urllib.request.urlopen(favicon_url) as response:
                response.read()
            return True

        with mock.patch.object(
            google_native, "_open_browser", side_effect=fake_open_browser
        ), mock.patch.object(
            google_native,
            "exchange_google_auth_code",
            return_value={"access_token": "token"},
        ) as exchange_code, mock.patch.object(
            google_native, "_gmail_profile", return_value="user@gmail.com"
        ):
            bundle = google_native.run_google_native_oauth_authorization(
                "client-id",
                timeout_seconds=2,
            )

        self.assertEqual(bundle["identity"], "user@gmail.com")
        exchange_code.assert_called_once()
        self.assertEqual(exchange_code.call_args.args[1], "auth-code")


if __name__ == "__main__":
    unittest.main()

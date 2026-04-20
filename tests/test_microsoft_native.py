import base64
import json
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.auth import microsoft_native


def _id_token_with_email(email):
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class MicrosoftNativeTests(unittest.TestCase):
    def test_exchange_ms_auth_code_sends_pkce_and_scope(self):
        with mock.patch.object(
            microsoft_native,
            "_json_request",
            return_value={"access_token": "token", "expires_in": 3600},
        ) as json_request:
            microsoft_native.exchange_ms_auth_code(
                "client-id",
                "code",
                "verifier",
                "http://localhost:1234/oauth2/callback",
            )

        payload = json_request.call_args.kwargs["data"]
        self.assertEqual(payload["grant_type"], "authorization_code")
        self.assertEqual(payload["code_verifier"], "verifier")
        self.assertIn("offline_access", payload["scope"])
        self.assertNotIn("client_secret", payload)

    def test_refresh_ms_access_token_preserves_refresh_token(self):
        with mock.patch.object(
            microsoft_native,
            "_json_request",
            return_value={"access_token": "new", "expires_in": 3600},
        ):
            bundle = microsoft_native.refresh_ms_access_token(
                "client-id", "old-refresh"
            )

        self.assertEqual(bundle["refresh_token"], "old-refresh")
        self.assertEqual(bundle["provider"], "microsoft")

    def test_graph_profile_falls_back_to_id_token_email(self):
        fake_response = mock.MagicMock()
        fake_response.__enter__.return_value = fake_response
        fake_response.read.return_value = json.dumps(
            {"mail": None, "userPrincipalName": "principal"}
        ).encode()

        with mock.patch.object(
            urllib.request, "urlopen", return_value=fake_response
        ):
            identity = microsoft_native._graph_profile(
                "access-token",
                id_token=_id_token_with_email("user@live.se"),
            )

        self.assertEqual(identity, "user@live.se")

    def test_oauth_callback_drives_browser_flow(self):
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
            return True

        with mock.patch.object(
            microsoft_native, "_open_browser", side_effect=fake_open_browser
        ), mock.patch.object(
            microsoft_native,
            "exchange_ms_auth_code",
            return_value={"access_token": "token", "id_token": ""},
        ) as exchange_code, mock.patch.object(
            microsoft_native, "_graph_profile", return_value="user@outlook.com"
        ):
            bundle = microsoft_native.run_ms_native_oauth_authorization(
                "client-id",
                timeout_seconds=2,
            )

        self.assertEqual(bundle["identity"], "user@outlook.com")
        exchange_code.assert_called_once()
        self.assertEqual(exchange_code.call_args.args[1], "auth-code")


if __name__ == "__main__":
    unittest.main()

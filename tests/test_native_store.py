import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts.native_store import (
    NativeAccountRecord,
    NativeOAuthAccountSource,
    native_descriptor_from_record,
)


class NativeStoreTests(unittest.TestCase):
    def test_native_gmail_record_builds_oauth_descriptor(self):
        record = NativeAccountRecord(
            id="acct-1",
            provider_kind="gmail",
            identity="user@gmail.com",
            presentation_name="user@gmail.com",
            alias="User",
            accent_color="#4c7fff",
            config={
                "oauth_provider": "google",
                "oauth_client_id": "client-id.apps.googleusercontent.com",
                "send_via_api": True,
            },
            enabled=True,
        )

        descriptor = native_descriptor_from_record(record)

        self.assertEqual(descriptor.auth_kind, "native-oauth2")
        self.assertIsInstance(descriptor.source_obj, NativeOAuthAccountSource)

    def test_native_google_oauth_source_reuses_cached_access_token(self):
        record = NativeAccountRecord(
            id="acct-1",
            provider_kind="gmail",
            identity="user@gmail.com",
            presentation_name="user@gmail.com",
            alias="User",
            accent_color="#4c7fff",
            config={
                "oauth_provider": "google",
                "oauth_client_id": "client-id.apps.googleusercontent.com",
            },
            enabled=True,
        )
        source = NativeOAuthAccountSource(record)
        bundle = {
            "access_token": "cached-token",
            "refresh_token": "refresh-token",
            "expires_at": 9999999999,
        }

        with (
            mock.patch(
                "accounts.native_store.load_native_oauth_token_bundle",
                return_value=bundle,
            ),
            mock.patch(
                "accounts.native_store.refresh_google_access_token"
            ) as refresh_token,
        ):
            token = source.get_access_token(network_ready_fn=lambda: True)

        self.assertEqual(token, "cached-token")
        refresh_token.assert_not_called()

    def test_native_google_oauth_source_refreshes_when_cached_token_is_expired(self):
        record = NativeAccountRecord(
            id="acct-1",
            provider_kind="gmail",
            identity="user@gmail.com",
            presentation_name="user@gmail.com",
            alias="User",
            accent_color="#4c7fff",
            config={
                "oauth_provider": "google",
                "oauth_client_id": "client-id.apps.googleusercontent.com",
            },
            enabled=True,
        )
        source = NativeOAuthAccountSource(record)
        stored = {
            "refresh_token": "refresh-token",
            "expires_at": 0,
        }
        refreshed = {
            "access_token": "fresh-token",
            "refresh_token": "refresh-token",
            "expires_at": 9999999999,
        }

        with (
            mock.patch(
                "accounts.native_store.load_native_oauth_token_bundle",
                return_value=stored,
            ),
            mock.patch(
                "accounts.native_store.refresh_google_access_token",
                return_value=refreshed,
            ) as refresh_token,
            mock.patch(
                "accounts.native_store.store_native_oauth_token_bundle"
            ) as store_bundle,
        ):
            token = source.get_access_token(network_ready_fn=lambda: True)

        self.assertEqual(token, "fresh-token")
        refresh_token.assert_called_once_with(
            "client-id.apps.googleusercontent.com",
            "refresh-token",
            client_secret="",
        )
        store_bundle.assert_called_once()

    def test_native_google_oauth_source_refreshes_with_client_secret_when_present(self):
        record = NativeAccountRecord(
            id="acct-1",
            provider_kind="gmail",
            identity="user@gmail.com",
            presentation_name="user@gmail.com",
            alias="User",
            accent_color="#4c7fff",
            config={
                "oauth_provider": "google",
                "oauth_client_id": "client-id.apps.googleusercontent.com",
                "oauth_client_secret": "super-secret",
            },
            enabled=True,
        )
        source = NativeOAuthAccountSource(record)
        stored = {
            "refresh_token": "refresh-token",
            "expires_at": 0,
        }
        refreshed = {
            "access_token": "fresh-token",
            "refresh_token": "refresh-token",
            "expires_at": 9999999999,
        }

        with (
            mock.patch(
                "accounts.native_store.load_native_oauth_token_bundle",
                return_value=stored,
            ),
            mock.patch(
                "accounts.native_store.refresh_google_access_token",
                return_value=refreshed,
            ) as refresh_token,
            mock.patch("accounts.native_store.store_native_oauth_token_bundle"),
        ):
            token = source.get_access_token(network_ready_fn=lambda: True)

        self.assertEqual(token, "fresh-token")
        refresh_token.assert_called_once_with(
            "client-id.apps.googleusercontent.com",
            "refresh-token",
            client_secret="super-secret",
        )

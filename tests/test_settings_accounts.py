import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings_accounts


class AccountSettingsControllerTests(unittest.TestCase):
    def _controller(self):
        controller = settings_accounts.AccountSettingsController.__new__(
            settings_accounts.AccountSettingsController
        )
        controller._save_native_google_record = mock.Mock()
        controller._refresh_runtime = mock.Mock()
        controller._render_accounts = mock.Mock()
        controller._finish_editor = mock.Mock()
        controller._toast = mock.Mock()
        controller._update_google_status = mock.Mock()
        return controller

    def _form(self):
        return {
            "save_btn": mock.Mock(),
            "cancel_btn": mock.Mock(),
        }

    def test_finish_native_google_auth_success_persists_and_finishes(self):
        controller = self._controller()
        form = self._form()
        bundle = {"access_token": "token"}

        with mock.patch.object(
            settings_accounts, "store_native_oauth_token_bundle"
        ) as store_bundle:
            result = controller._finish_native_google_auth_success(
                account_id="acct-1",
                bundle=bundle,
                identity_value="user@gmail.com",
                alias="User",
                color="#4c7fff",
                enabled=True,
                client_id="client-id",
                form=form,
            )

        self.assertFalse(result)
        store_bundle.assert_called_once_with("acct-1", bundle)
        controller._save_native_google_record.assert_called_once_with(
            "acct-1",
            "user@gmail.com",
            "User",
            "#4c7fff",
            True,
            "client-id",
            "",
        )
        controller._refresh_runtime.assert_called_once()
        controller._render_accounts.assert_called_once()
        controller._finish_editor.assert_called_once()
        controller._toast.assert_called_once_with(
            "Added Gmail account for user@gmail.com"
        )
        controller._update_google_status.assert_not_called()

    def test_finish_native_google_auth_success_surfaces_save_failure(self):
        controller = self._controller()
        form = self._form()

        with mock.patch.object(
            settings_accounts,
            "store_native_oauth_token_bundle",
            side_effect=RuntimeError("keyring unavailable"),
        ):
            result = controller._finish_native_google_auth_success(
                account_id="acct-1",
                bundle={"access_token": "token"},
                identity_value="user@gmail.com",
                alias="User",
                color="#4c7fff",
                enabled=True,
                client_id="client-id",
                form=form,
            )

        self.assertFalse(result)
        controller._save_native_google_record.assert_not_called()
        controller._refresh_runtime.assert_not_called()
        controller._render_accounts.assert_not_called()
        controller._finish_editor.assert_not_called()
        controller._update_google_status.assert_called_once_with(
            form,
            "Google sign-in completed, but Hermod could not save the account: keyring unavailable",
        )
        form["save_btn"].set_sensitive.assert_called_once_with(True)
        form["cancel_btn"].set_sensitive.assert_called_once_with(True)
        controller._toast.assert_called_once_with(
            "Google sign-in completed, but Hermod could not save the account: keyring unavailable"
        )

    def test_finish_native_google_auth_error_surfaces_message(self):
        controller = self._controller()
        form = self._form()

        result = controller._finish_native_google_auth_error(
            form=form,
            message="Google sign-in timed out before approval completed",
        )

        self.assertFalse(result)
        controller._update_google_status.assert_called_once_with(
            form,
            "Google sign-in timed out before approval completed",
        )
        form["save_btn"].set_sensitive.assert_called_once_with(True)
        form["cancel_btn"].set_sensitive.assert_called_once_with(True)
        controller._toast.assert_called_once_with(
            "Google sign-in timed out before approval completed"
        )

    def test_google_progress_callback_updates_status_on_main_loop(self):
        controller = self._controller()
        form = self._form()

        with mock.patch.object(
            settings_accounts.GLib,
            "idle_add",
            side_effect=lambda callback: callback(),
        ) as idle_add:
            progress = controller._google_progress_callback(form)
            progress("Browser approval received. Finishing sign-in...")

        idle_add.assert_called_once()
        controller._update_google_status.assert_called_once_with(
            form,
            "Browser approval received. Finishing sign-in...",
        )


if __name__ == "__main__":
    unittest.main()

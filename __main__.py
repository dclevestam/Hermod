import sys
import threading
import time
import traceback

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Notify', '0.7')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Notify, GLib, Gio

from .backends import get_backends, is_transient_network_error, network_ready
from .window import LarkWindow
from .settings import get_settings


def _log_exception(prefix, exc):
    if get_settings().get('debug_logging'):
        print(f'{prefix}: {exc}', file=sys.stderr)
        traceback.print_exc()


class LarkApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='io.github.lark.Lark')
        self.window = None
        self.backends = []
        self._notif_thread = None
        self._last_counts = {}
        self._poll_stop = threading.Event()
        self._poll_wake = threading.Event()
        self._poll_suspended = False
        self._next_poll_at = 0.0
        self._network_monitor = Gio.NetworkMonitor.get_default()
        self._transient_poll_failures = 0
        self.connect('activate', self._on_activate)
        self.connect('shutdown', self._on_shutdown)

    def _folder_id_for_name(self, backend, display_name):
        return next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == display_name), None)

    def _on_activate(self, _):
        if self.window:
            self.window.present()
            return

        Notify.init('Lark')

        try:
            self.backends = get_backends()
        except Exception as e:
            _log_exception('Failed to load accounts', e)
            self.backends = []

        self.window = LarkWindow(self, self.backends)
        self.window.connect('close-request', self._on_close)
        self.window.connect('notify::suspended', self._on_window_suspended)
        self._network_monitor.connect('network-changed', self._on_network_changed)
        self.window.set_network_offline(not network_ready())
        self.window.present()

        if self.backends:
            self._notif_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._notif_thread.start()

    def _on_close(self, _):
        return False

    def _on_shutdown(self, _):
        self._poll_stop.set()
        self._poll_wake.set()

    def _on_window_suspended(self, window, _pspec):
        self._poll_suspended = window.props.suspended
        if self._poll_suspended:
            # Pause background polling while the app is suspended.
            self._poll_wake.set()
            return

        # On resume, give the network a moment to stabilise before polling.
        # The network-changed signal will pull this forward once connectivity
        # is truly restored, so we won't wait longer than necessary.
        self._next_poll_at = time.monotonic() + 5
        self._poll_wake.set()

    def _on_network_changed(self, _monitor, available):
        if available:
            self._next_poll_at = min(self._next_poll_at, time.monotonic())
            if self.window is not None:
                GLib.idle_add(self.window.set_network_offline, False)
            if self.window is not None:
                GLib.idle_add(self.window.refresh_visible_mail)
        elif self.window is not None:
            GLib.idle_add(self.window.set_network_offline, True)
        self._poll_wake.set()

    def _poll_loop(self):
        settings = get_settings()
        self._next_poll_at = time.monotonic()
        while True:
            if self._poll_stop.is_set():
                return
            if self._poll_suspended:
                self._poll_wake.wait(1)
                self._poll_wake.clear()
                continue

            wait_for = max(0.0, self._next_poll_at - time.monotonic())
            if self._poll_wake.wait(wait_for):
                self._poll_wake.clear()
                continue

            if self._poll_stop.is_set() or self._poll_suspended:
                continue

            if not network_ready():
                GLib.idle_add(self.window.set_network_offline, True)
                self._next_poll_at = time.monotonic() + 30
                continue

            GLib.idle_add(self.window.set_network_offline, False)
            GLib.idle_add(self.window.set_syncing, True)
            total_new = 0
            transient_error = False
            successful_poll = False
            for backend in self.backends:
                try:
                    inbox_id = self._folder_id_for_name(backend, 'Inbox') or backend.FOLDERS[0][0]
                    trash_id = self._folder_id_for_name(backend, 'Trash')
                    spam_id = self._folder_id_for_name(backend, 'Spam')

                    inbox_count = backend.get_unread_count(inbox_id)
                    trash_count = backend.get_unread_count(trash_id) if trash_id else 0
                    spam_count = backend.get_unread_count(spam_id) if spam_id else 0
                    successful_poll = True

                    prev = self._last_counts.get(backend.identity)
                    if prev is not None and inbox_count > prev:
                        new = inbox_count - prev
                        total_new += new
                        self._notify(backend.identity, new)
                    GLib.idle_add(
                        self.window.update_account_counts,
                        backend.identity,
                        inbox_count,
                        trash_count,
                        spam_count,
                    )
                    self._last_counts[backend.identity] = inbox_count
                except Exception as e:
                    if is_transient_network_error(e):
                        transient_error = True
                    _log_exception(f'Poll error ({backend.identity})', e)
            GLib.idle_add(self.window.on_poll_complete, total_new)
            if successful_poll:
                self._transient_poll_failures = 0
            elif transient_error:
                self._transient_poll_failures = min(self._transient_poll_failures + 1, 5)
                GLib.idle_add(self.window.set_network_offline, not network_ready())
            poll_secs = max(60, settings.get('poll_interval') * 60)
            if transient_error and not successful_poll:
                poll_secs = min(poll_secs, 30 * (2 ** self._transient_poll_failures))
            self._next_poll_at = time.monotonic() + poll_secs

    def _notify(self, account, count):
        n = Notify.Notification.new(
            account,
            f'{count} new message{"s" if count != 1 else ""}',
            'mail-unread-symbolic',
        )
        try:
            n.show()
        except Exception as e:
            _log_exception('Notification error', e)


def main():
    app = LarkApp()
    sys.exit(app.run(sys.argv))


main()

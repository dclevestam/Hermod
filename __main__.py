import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Notify', '0.7')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Notify, GLib, Gio

try:
    from .backends import get_backends, is_transient_network_error, network_ready
    from .diagnostics.logger import log_network_change, log_startup_summary
    from .window import HermodWindow
    from .settings import get_settings
    from .utils import _log_exception, _perf_counter, _log_perf
except ImportError:
    from backends import get_backends, is_transient_network_error, network_ready
    from diagnostics.logger import log_network_change, log_startup_summary
    from window import HermodWindow
    from settings import get_settings
    from utils import _log_exception, _perf_counter, _log_perf


class HermodApp(Adw.Application):
    def __init__(self, dump_ui_path=None, dump_ui_delay_ms=1500, dump_ui_max_attempts=20):
        super().__init__(application_id='io.github.hermod.Hermod')
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.window = None
        self.backends = []
        self._notif_thread = None
        self._poll_stop = threading.Event()
        self._poll_wake = threading.Event()
        self._poll_suspended = False
        self._next_poll_at = 0.0
        self._next_reconcile_at = 0.0
        self._force_reconcile = True
        self._network_monitor = Gio.NetworkMonitor.get_default()
        self._transient_poll_failures = 0
        self._dump_ui_path = Path(dump_ui_path) if dump_ui_path else None
        self._dump_ui_delay_ms = max(250, int(dump_ui_delay_ms))
        self._dump_ui_max_attempts = max(1, int(dump_ui_max_attempts))
        self._dump_ui_attempts = 0
        self._dump_ui_done = False
        self.connect('activate', self._on_activate)
        self.connect('shutdown', self._on_shutdown)

    def _folder_id_for_name(self, backend, display_name):
        return next((folder_id for folder_id, name, _icon in backend.FOLDERS if name == display_name), None)

    def wake_background_updates(self, reconcile=False):
        self._next_poll_at = min(self._next_poll_at, time.monotonic())
        if reconcile:
            self._force_reconcile = True
        self._poll_wake.set()

    def _on_activate(self, _):
        if self.window:
            self.window.present()
            return
        startup_started = _perf_counter()

        Notify.init('Hermod')

        accounts_started = _perf_counter()
        try:
            self.backends = get_backends()
        except Exception as e:
            _log_exception('Failed to load accounts', e)
            self.backends = []
        log_startup_summary(self.backends)
        _log_perf('activate accounts', f'{len(self.backends)} backends', started=accounts_started)

        window_started = _perf_counter()
        self.window = HermodWindow(self, self.backends)
        self.window.connect('notify::suspended', self._on_window_suspended)
        self._network_monitor.connect('network-changed', self._on_network_changed)
        self.window.set_network_offline(not network_ready())
        self.window.present()
        _log_perf('activate window', f'{len(self.backends)} backends', started=window_started)
        GLib.idle_add(self._log_startup_ready, startup_started, len(self.backends))

        if self._dump_ui_path is not None:
            GLib.timeout_add(self._dump_ui_delay_ms, self._dump_ui_once)

        if self.backends:
            self._notif_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._notif_thread.start()

    def _log_startup_ready(self, started, backend_count):
        _log_perf('activate startup', f'{backend_count} backends', started=started)
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
        log_network_change(available)
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
        reconcile_secs = max(60, settings.get('reconcile_interval') * 60)
        poll_secs = max(60, settings.get('poll_interval') * 60)
        startup_grace_secs = min(15, poll_secs)
        self._next_poll_at = time.monotonic() + startup_grace_secs
        self._next_reconcile_at = time.monotonic() + reconcile_secs
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

            if self.window is not None and getattr(self.window, '_startup_status_active', False):
                # Startup already performs the first mailbox load, so keep the
                # background poll from racing it or immediately repeating it.
                self._next_poll_at = time.monotonic() + startup_grace_secs
                continue

            if self.window is not None and getattr(self.window, '_sync_in_flight', False):
                self._next_poll_at = time.monotonic() + 5
                continue

            GLib.idle_add(self.window.set_network_offline, False)
            GLib.idle_add(self.window.set_syncing, True)
            reconcile_due = self._force_reconcile or time.monotonic() >= self._next_reconcile_at
            results = []
            total_new = 0
            transient_error = False
            successful_poll = False
            poll_jobs = []
            for backend in self.backends:
                tracked_folders = [folder_id for folder_id, _name, _icon in backend.FOLDERS]
                if self.window is not None:
                    current_backend = getattr(self.window, 'current_backend', None)
                    current_folder = getattr(self.window, 'current_folder', None)
                    if current_backend is backend and current_folder:
                        tracked_folders.append(current_folder)
                poll_jobs.append((backend, tracked_folders))
            max_workers = max(1, min(4, len(poll_jobs)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_backend = {
                    executor.submit(
                        backend.check_background_updates,
                        tracked_folders=tracked_folders,
                        reconcile_counts=reconcile_due,
                    ): backend
                    for backend, tracked_folders in poll_jobs
                }
                for future in as_completed(future_to_backend):
                    backend = future_to_backend[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        if is_transient_network_error(e):
                            transient_error = True
                        _log_exception(f'Poll error ({backend.identity})', e)
                        notice = None
                        if hasattr(backend, 'consume_sync_notice'):
                            try:
                                notice = backend.consume_sync_notice()
                            except Exception:
                                notice = None
                        result = {
                            'account': backend.identity,
                            'provider': getattr(backend, 'provider', ''),
                            'changed_folders': set(),
                            'new_messages': [],
                            'counts': {},
                            'notice': notice or {'kind': 'error', 'detail': 'Sync issue'},
                        }
                    else:
                        successful_poll = True
                    results.append(result)
                    new = len(result.get('new_messages', []))
                    if new > 0:
                        total_new += new
                        self._notify(backend.identity, new)
            GLib.idle_add(self.window.on_background_update, results, total_new)
            if successful_poll:
                self._transient_poll_failures = 0
                self._force_reconcile = False
                if reconcile_due:
                    reconcile_secs = max(60, settings.get('reconcile_interval') * 60)
                    self._next_reconcile_at = time.monotonic() + reconcile_secs
            elif transient_error:
                self._transient_poll_failures = min(self._transient_poll_failures + 1, 5)
                GLib.idle_add(self.window.set_network_offline, not network_ready())
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

    def _dump_ui_once(self):
        if self._dump_ui_done:
            return False
        self._dump_ui_attempts += 1
        if self._dump_ui_attempts > self._dump_ui_max_attempts:
            print(
                f'dump-ui: giving up after {self._dump_ui_max_attempts} attempts',
                file=sys.stderr,
            )
            self.quit()
            return False
        try:
            if self.window is None:
                print('dump-ui: waiting for window', file=sys.stderr)
                return True

            native = self.window.get_native()
            if native is None:
                print('dump-ui: waiting for native surface', file=sys.stderr)
                return True

            renderer = native.get_renderer()
            if renderer is None or not renderer.is_realized():
                print('dump-ui: waiting for renderer', file=sys.stderr)
                return True

            width = max(1, self.window.get_width())
            height = max(1, self.window.get_height())
            paintable = Gtk.WidgetPaintable.new(self.window)
            current = paintable.get_current_image()
            snapshot = Gtk.Snapshot.new()
            current.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is None:
                print('dump-ui: waiting for snapshot node', file=sys.stderr)
                return True

            texture = renderer.render_texture(node, None)
            if texture is None:
                print('dump-ui: waiting for texture', file=sys.stderr)
                return True

            self._dump_ui_path.parent.mkdir(parents=True, exist_ok=True)
            texture.save_to_png(str(self._dump_ui_path))
            print(f'dump-ui: wrote {self._dump_ui_path}', file=sys.stderr)
            self._dump_ui_done = True
            self.quit()
            return False
        except Exception as e:
            _log_exception('dump-ui failed', e)
            self.quit()
            return False


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--dump-ui', metavar='PATH')
    parser.add_argument('--dump-ui-delay-ms', type=int, default=1500)
    parser.add_argument('--dump-ui-max-attempts', type=int, default=20)
    args, remaining = parser.parse_known_args(sys.argv[1:])
    app = HermodApp(
        dump_ui_path=args.dump_ui,
        dump_ui_delay_ms=args.dump_ui_delay_ms,
        dump_ui_max_attempts=args.dump_ui_max_attempts,
    )
    sys.exit(app.run([sys.argv[0], *remaining]))

if __name__ == '__main__':
    main()

import argparse
import atexit
import errno
import faulthandler
import fcntl
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Dump every thread's Python stack to stderr on SIGUSR1 or any fatal
# signal. Makes diagnosing a 100%-CPU spin trivial: `kill -USR1 <pid>`
# prints exactly which callback/loop is burning.
faulthandler.enable()
try:
    faulthandler.register(signal.SIGUSR1, all_threads=True)
except (AttributeError, ValueError):
    pass

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Notify', '0.7')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Notify, GLib, Gio

try:
    from .backends import get_backends, is_transient_network_error, network_ready
    from .diagnostics.logger import log_network_change, log_startup_summary
    from .fonts import register_bundled_fonts
    from .window import HermodWindow
    from .settings import get_settings
    from .utils import _log_exception, _perf_counter, _log_perf
except ImportError:
    from backends import get_backends, is_transient_network_error, network_ready
    from diagnostics.logger import log_network_change, log_startup_summary
    from fonts import register_bundled_fonts
    from window import HermodWindow
    from settings import get_settings
    from utils import _log_exception, _perf_counter, _log_perf

# Register bundled Geist fonts before Pango initialises so that named lookups
# of "Geist" / "Geist Mono" in CSS resolve without requiring a system install.
register_bundled_fonts()


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

    def _start_main_loop_watchdog(self, stall_threshold_s=15.0):
        """Detect a wedged GLib main loop and force-exit before the
        process lingers at 100% CPU. A GLib timer updates a heartbeat
        timestamp every 2s; a daemon thread checks that the heartbeat
        is fresh. If the main thread stops servicing GLib timers for
        longer than stall_threshold_s, dump every thread's Python
        stack to stderr and abort."""
        self._watchdog_last_beat = time.monotonic()
        self._watchdog_threshold = float(stall_threshold_s)

        def _heartbeat():
            self._watchdog_last_beat = time.monotonic()
            return True

        GLib.timeout_add_seconds(2, _heartbeat)

        def _monitor():
            while not self._poll_stop.wait(2.0):
                idle = time.monotonic() - self._watchdog_last_beat
                if idle > self._watchdog_threshold:
                    sys.stderr.write(
                        f'[hermod-watchdog] Main loop stalled for {idle:.1f}s. '
                        f'Dumping stacks and force-exiting.\n'
                    )
                    sys.stderr.flush()
                    try:
                        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                    except Exception:
                        pass
                    os._exit(2)

        threading.Thread(target=_monitor, daemon=True).start()

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
        self._start_main_loop_watchdog()
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
        # Give the background poll thread a moment to observe the stop
        # flag and return, so the process can exit cleanly. Previously
        # the daemon thread was expected to die with the interpreter,
        # but if it was mid-HTTP-call it could keep the process alive
        # long enough to look stuck.
        thread = self._notif_thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=2.0)
            except RuntimeError:
                pass
        for backend in self.backends or []:
            if getattr(backend, '_persist_timer', None) is None:
                continue
            try:
                backend._persist_sync_state(immediate=True)
            except Exception:
                pass

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
            # Manual executor + per-backend timeout so a single stuck provider
            # (silent TCP stall, slow Graph endpoint) can't block the whole
            # poll loop or prevent graceful shutdown. `with ThreadPoolExecutor`
            # would block on implicit shutdown(wait=True) during _poll_stop.
            executor = ThreadPoolExecutor(max_workers=max_workers)
            try:
                future_to_backend = {
                    executor.submit(
                        backend.check_background_updates,
                        tracked_folders=tracked_folders,
                        reconcile_counts=reconcile_due,
                    ): backend
                    for backend, tracked_folders in poll_jobs
                }
                poll_deadline_s = 30.0
                for future in as_completed(
                    future_to_backend, timeout=poll_deadline_s * len(poll_jobs)
                ):
                    if self._poll_stop.is_set():
                        for pending in future_to_backend:
                            pending.cancel()
                        break
                    backend = future_to_backend[future]
                    try:
                        result = future.result(timeout=poll_deadline_s)
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
                    if not isinstance(result, dict):
                        # Defensive: a misbehaving provider returning non-dict
                        # would crash .get() below and take the poll thread
                        # down. Normalise and surface the issue.
                        _log_exception(
                            f'Poll: backend {getattr(backend, "identity", "?")} '
                            f'returned non-dict: {type(result).__name__}',
                            None,
                        )
                        result = {
                            'account': getattr(backend, 'identity', ''),
                            'provider': getattr(backend, 'provider', ''),
                            'changed_folders': set(),
                            'new_messages': [],
                            'counts': {},
                            'notice': {'kind': 'error', 'detail': 'Sync issue'},
                        }
                    results.append(result)
                    new = len(result.get('new_messages', []))
                    if new > 0:
                        total_new += new
                        self._notify(backend.identity, new)
            except Exception as e:
                _log_exception('Poll cycle failed', e)
            finally:
                # Don't wait for stuck futures — they're daemon threads
                # and will die with the process. `cancel_futures=True`
                # drops queued-but-not-started work immediately.
                executor.shutdown(wait=False, cancel_futures=True)
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
        n.set_timeout(6000)  # 6s; avoids the daemon's default-forever path
        try:
            n.show()
        except Exception as e:
            _log_exception('Notification error', e)
            try:
                n.close()
            except Exception:
                pass

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


# ── Single-instance fallback ──────────────────────────────────────────────────
# Adw.Application normally enforces single-instance through DBus (the
# `application_id` becomes a well-known bus name; a second launch sees
# get_is_remote() == True and activates the primary). That doesn't work
# when the user runs `DBUS_SESSION_BUS_ADDRESS=disabled:` (common for
# headless dev / screenshots) or when the session bus isn't reachable —
# every launch becomes primary, so the user ends up with a fleet of
# 99%-CPU instances fighting for the Graph API.
#
# The fcntl lock below catches those cases. When DBus is available we
# stay out of the way and let GApplication handle it; otherwise we
# acquire an exclusive lock on ~/.cache/hermod/hermod.lock and write
# our PID. A stale lock (holder died without releasing) is detected via
# `os.kill(pid, 0)` and broken automatically on the next launch.

_LOCK_PATH = Path(GLib.get_user_cache_dir()) / 'hermod' / 'hermod.lock'
_lock_fh = None


def _dbus_session_available():
    addr = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '').strip()
    return bool(addr) and not addr.startswith('disabled:')


def _acquire_single_instance_lock():
    global _lock_fh
    try:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return True  # Can't create lock dir — best to let the launch proceed.
    try:
        _lock_fh = open(_LOCK_PATH, 'a+')
    except OSError:
        return True
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            # Filesystem doesn't support flock; don't block the launch.
            _lock_fh.close()
            _lock_fh = None
            return True
        # Another process holds the lock. Check whether it's still alive.
        try:
            _lock_fh.seek(0)
            held_pid = int((_lock_fh.read() or '0').strip() or '0')
        except Exception:
            held_pid = 0
        if held_pid > 0 and held_pid != os.getpid():
            try:
                os.kill(held_pid, 0)
                _lock_fh.close()
                _lock_fh = None
                return False  # Primary is alive.
            except ProcessLookupError:
                pass  # Stale — fall through to reclaim.
            except PermissionError:
                # Process exists but belongs to someone else; treat as alive.
                _lock_fh.close()
                _lock_fh = None
                return False
        # Try to reclaim the stale lock by re-opening and flocking again.
        try:
            _lock_fh.close()
        except Exception:
            pass
        try:
            _lock_fh = open(_LOCK_PATH, 'w+')
            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _lock_fh = None
            return False
    try:
        _lock_fh.seek(0)
        _lock_fh.truncate()
        _lock_fh.write(f'{os.getpid()}\n')
        _lock_fh.flush()
    except Exception:
        pass
    return True


def _release_single_instance_lock():
    global _lock_fh
    if _lock_fh is None:
        return
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _lock_fh.close()
    except Exception:
        pass
    _lock_fh = None
    try:
        _LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


atexit.register(_release_single_instance_lock)


def _install_signal_handlers(app):
    def _handle_term(signum, _frame):
        # Hop onto the main loop so GApplication's shutdown cascade runs
        # (windows destroyed, _on_shutdown fired, poll thread joined).
        GLib.idle_add(lambda: (app.quit(), False)[1])

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_term)
        except (ValueError, OSError):
            pass  # Not on the main thread or signal unavailable.


def _set_process_name(name='Hermod'):
    """Make `ps`, `top`, `htop`, systemd journal show this process as
    `Hermod` instead of `python3 /path/to/__main__.py`.

    Tries `setproctitle` (preferred — rewrites argv so full `ps` cmdline
    shows the friendly name). Falls back to a direct `prctl(PR_SET_NAME)`
    syscall on Linux, which at minimum updates the short comm field
    (`/proc/<pid>/comm`, which is what `htop`, `top`, and the systemd
    journal display) with no extra dependency."""
    try:
        import setproctitle  # type: ignore
        setproctitle.setproctitle(name)
        return
    except ImportError:
        pass
    try:
        import ctypes
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        PR_SET_NAME = 15  # <linux/prctl.h>
        # Linux caps comm to 15 bytes + NUL; truncate if longer.
        buf = ctypes.create_string_buffer(name.encode('utf-8')[:15] + b'\0')
        libc.prctl(PR_SET_NAME, buf, 0, 0, 0)
    except Exception:
        pass
    try:
        GLib.set_prgname(name)
    except Exception:
        pass


def main():
    _set_process_name('Hermod')
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--dump-ui', metavar='PATH')
    parser.add_argument('--dump-ui-delay-ms', type=int, default=1500)
    parser.add_argument('--dump-ui-max-attempts', type=int, default=20)
    parser.add_argument(
        '--replace',
        action='store_true',
        help='Kill any running Hermod instance before starting this one.',
    )
    args, remaining = parser.parse_known_args(sys.argv[1:])

    # Honour --replace up-front so callers can recover from a stuck
    # process without hunting for a PID.
    if args.replace:
        _kill_existing_instance()

    # File lock is the authoritative single-instance mechanism. Adw.Application's
    # DBus-based uniqueness is nice when it works (second launch activates the
    # primary window) but can silently fail under dev shells, containerised
    # sessions, or DBUS_SESSION_BUS_ADDRESS=disabled:. The file lock catches
    # every case; when DBus is working, the primary still receives the second
    # process's `activate` via atexit → DBus round-trip (GApplication's own
    # machinery runs before our atexit release).
    if not _acquire_single_instance_lock():
        print(
            'Hermod is already running. Use `--replace` to forcibly restart, '
            'or bring the existing window forward.',
            file=sys.stderr,
        )
        return 0

    app = HermodApp(
        dump_ui_path=args.dump_ui,
        dump_ui_delay_ms=args.dump_ui_delay_ms,
        dump_ui_max_attempts=args.dump_ui_max_attempts,
    )
    _install_signal_handlers(app)
    try:
        exit_code = app.run([sys.argv[0], *remaining])
    finally:
        _release_single_instance_lock()
    # Hard-exit guard: if any non-daemon thread or GLib source keeps the
    # interpreter alive past this point, force the process down so we
    # don't leave a 100%-CPU orphan behind.
    _schedule_hard_exit(exit_code, delay_s=3.0)
    sys.exit(exit_code)


def _schedule_hard_exit(exit_code, delay_s=3.0):
    def _force():
        time.sleep(max(0.1, delay_s))
        os._exit(int(exit_code or 0))

    threading.Thread(target=_force, daemon=True).start()


def _kill_existing_instance():
    """Best-effort shutdown of any PID recorded in the lock file."""
    try:
        with open(_LOCK_PATH) as fh:
            pid = int((fh.read() or '0').strip() or '0')
    except (OSError, ValueError):
        return
    if pid <= 0 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        print(f'Cannot signal PID {pid} (permission denied).', file=sys.stderr)
        return
    # Give SIGTERM a moment to drain, then SIGKILL if still alive.
    for _ in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


if __name__ == '__main__':
    main()

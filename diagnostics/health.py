"""Environment and health snapshot helpers for diagnostics export."""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

try:
    from ..accounts.native_store import get_native_account_descriptors
    from ..settings import get_settings
except ImportError:
    from accounts.native_store import get_native_account_descriptors
    from settings import get_settings


_EXPORTABLE_SETTINGS = (
    'poll_interval',
    'reconcile_interval',
    'load_images',
    'mark_read_on_open',
    'close_minimizes',
    'show_unified_trash',
    'show_unified_spam',
    'debug_logging',
    'disk_cache_budget_mb',
)


def build_health_snapshot():
    settings = get_settings()
    account_summary = {}
    try:
        for descriptor in get_native_account_descriptors():
            key = f'native:{descriptor.provider_kind}'
            account_summary[key] = account_summary.get(key, 0) + 1
    except Exception:
        account_summary = {}
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'python_version': sys.version.split()[0],
        'platform': platform.platform(),
        'gtk_version': f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}',
        'adw_version': f'{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}',
        'account_summary': account_summary,
        'settings': {key: settings.get(key) for key in _EXPORTABLE_SETTINGS},
    }

"""Shared helpers for provider implementations."""

import email as email_parser
from datetime import datetime, timezone
from email.header import decode_header as _decode_header_raw

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio

try:
    from ..accounts.descriptors import AccountDescriptor
    from ..accounts.sources.goa import descriptor_from_goa_object
except ImportError:
    from accounts.descriptors import AccountDescriptor
    from accounts.sources.goa import descriptor_from_goa_object


def _decode_str(value):
    if not value:
        return ''
    parts = _decode_header_raw(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)


def _parse_addrs(header_val):
    if not header_val:
        return []
    return [{'name': n or e, 'email': e}
            for n, e in email_parser.utils.getaddresses([header_val]) if e]


def _normalize_recipients(value):
    if not value:
        return []
    if isinstance(value, str):
        return [addr['email'] for addr in _parse_addrs(value)]
    return [addr['email'] for addr in value if addr.get('email')]


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _aware_utc_datetime(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def network_ready():
    try:
        monitor = Gio.NetworkMonitor.get_default()
        if not monitor.get_network_available():
            return False
        connectivity = monitor.get_connectivity()
        return connectivity != Gio.NetworkConnectivity.LOCAL
    except Exception:
        return True


def ensure_network_ready():
    if not network_ready():
        raise RuntimeError('network not ready')


def is_transient_network_error(exc):
    text = str(exc).lower()
    return any(token in text for token in (
        'status 0',
        '((null))',
        'expected status 200 when requesting access token',
        'temporary failure in name resolution',
        'name resolution',
        'network is unreachable',
        'connection reset',
        'timed out',
        'temporarily unavailable',
        'could not connect',
    ))


def coerce_account_descriptor(source, provider_kind):
    if isinstance(source, AccountDescriptor):
        descriptor = source
    else:
        descriptor = descriptor_from_goa_object(source)
    if descriptor is None:
        raise ValueError('GOA mail account descriptor unavailable')
    if descriptor.provider_kind != provider_kind:
        raise ValueError(f'Expected provider {provider_kind}, got {descriptor.provider_kind}')
    return descriptor

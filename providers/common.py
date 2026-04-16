"""Shared helpers for provider implementations."""

import email as email_parser
import time
from datetime import datetime, timezone
from email.header import decode_header as _decode_header_raw
from dataclasses import dataclass, field
from typing import Any

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio

try:
    from ..accounts.descriptors import AccountDescriptor
except ImportError:
    from accounts.descriptors import AccountDescriptor


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
    recipients = []
    for item in value:
        if isinstance(item, str):
            recipients.extend(addr['email'] for addr in _parse_addrs(item))
            continue
        if isinstance(item, dict) and item.get('email'):
            recipients.append(str(item['email']).strip())
    return [addr for addr in recipients if addr]


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


class BodyFetchError(RuntimeError):
    """Raised when a provider cannot produce a message body payload."""


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


def build_sync_notice(kind, detail, **fields):
    notice = {
        'kind': str(kind or '').strip().lower(),
        'detail': str(detail or '').strip(),
    }
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        notice[str(key)] = value
    return notice


@dataclass(frozen=True)
class SyncPolicy:
    provider: str
    primary: str
    fallback: str
    reconcile: str
    notes: str = ''

    def as_dict(self):
        return {
            'provider': self.provider,
            'primary': self.primary,
            'fallback': self.fallback,
            'reconcile': self.reconcile,
            'notes': self.notes,
        }


@dataclass(frozen=True)
class CountPolicy:
    provider: str
    primary: str
    fallback: str
    reconcile: str
    route: str = 'primary'
    source: str = ''
    notes: str = ''

    def as_dict(self):
        return {
            'provider': self.provider,
            'primary': self.primary,
            'fallback': self.fallback,
            'reconcile': self.reconcile,
            'route': self.route,
            'source': self.source,
            'notes': self.notes,
        }


@dataclass(frozen=True)
class SyncDiagnostic:
    kind: str
    detail: str
    code: str = ''
    retryable: bool = False
    folder: str = ''
    provider: str = ''
    account: str = ''
    context: dict[str, Any] = field(default_factory=dict)

    def as_notice(self):
        notice = build_sync_notice(self.kind, self.detail)
        if self.code:
            notice['code'] = str(self.code)
        if self.retryable is not None:
            notice['retryable'] = bool(self.retryable)
        if self.folder:
            notice['folder'] = str(self.folder)
        if self.provider:
            notice['provider'] = str(self.provider)
        if self.account:
            notice['account'] = str(self.account)
        if self.context:
            notice['context'] = dict(self.context)
        return notice


@dataclass
class SyncHealthState:
    provider: str = ''
    account: str = ''
    route: str = 'primary'
    state: str = 'ready'
    detail: str = ''
    tooltip: str = ''
    code: str = ''
    retryable: bool = False
    retry_after_at: float = 0.0
    retry_after_seconds: int = 0
    primary_label: str = 'primary'
    fallback_label: str = 'fallback'
    updated_at: str = ''
    context: dict[str, Any] = field(default_factory=dict)

    def _touch(self):
        self.updated_at = _utcnow_iso()

    def mark_ready(self, detail='Ready'):
        self.route = 'primary'
        self.state = 'ready'
        self.detail = str(detail or 'Ready').strip() or 'Ready'
        self.tooltip = ''
        self.code = ''
        self.retryable = False
        self.retry_after_at = 0.0
        self.retry_after_seconds = 0
        self.context = {}
        self._touch()

    def mark_warning(
        self,
        detail,
        *,
        tooltip='',
        code='',
        retryable=True,
        retry_after_seconds=None,
        route='fallback',
        context=None,
    ):
        self.route = str(route or 'fallback').strip() or 'fallback'
        self.state = 'warning'
        self.detail = str(detail or '').strip() or 'Using fallback'
        self.tooltip = str(tooltip or '').strip()
        self.code = str(code or '').strip()
        self.retryable = bool(retryable)
        self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        self.retry_after_at = (time.monotonic() + self.retry_after_seconds) if self.retry_after_seconds else 0.0
        self.context = dict(context or {})
        self._touch()

    def mark_error(
        self,
        detail,
        *,
        tooltip='',
        code='',
        retryable=False,
        retry_after_seconds=None,
        route='fallback',
        context=None,
    ):
        self.route = str(route or 'fallback').strip() or 'fallback'
        self.state = 'error'
        self.detail = str(detail or '').strip() or 'Sync issue'
        self.tooltip = str(tooltip or '').strip()
        self.code = str(code or '').strip()
        self.retryable = bool(retryable)
        self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        self.retry_after_at = (time.monotonic() + self.retry_after_seconds) if self.retry_after_seconds else 0.0
        self.context = dict(context or {})
        self._touch()

    def should_probe_primary(self, now=None):
        if self.route == 'primary':
            return True
        if not self.retryable:
            return False
        now = time.monotonic() if now is None else now
        return self.retry_after_at <= now

    def remaining_retry_seconds(self, now=None):
        if self.retry_after_at <= 0:
            return 0
        now = time.monotonic() if now is None else now
        return max(0, int(self.retry_after_at - now))

    def is_degraded(self):
        return self.state in {'warning', 'error'} or self.route != 'primary'

    def _format_remaining(self, seconds):
        seconds = max(0, int(seconds or 0))
        if seconds <= 0:
            return 'now'
        minutes, remainder = divmod(seconds, 60)
        if minutes <= 0:
            return f'{seconds}s'
        if minutes < 60:
            if remainder:
                return f'{minutes}m {remainder}s'
            return f'{minutes}m'
        hours, minutes = divmod(minutes, 60)
        if minutes:
            return f'{hours}h {minutes}m'
        return f'{hours}h'

    def sidebar_tooltip(self):
        parts = [self.detail or 'Needs attention']
        remaining = self.remaining_retry_seconds()
        if self.retryable and remaining > 0:
            parts.append(f'Retrying {self.primary_label or "primary route"} in {self._format_remaining(remaining)}.')
        elif self.retryable and self.route != 'primary':
            parts.append(f'Retrying {self.primary_label or "primary route"} shortly.')
        if self.tooltip:
            parts.append(self.tooltip)
        return ' '.join(part for part in parts if part).strip()

    def as_sidebar_status(self):
        if not self.is_degraded():
            return None
        return {
            'provider': self.provider,
            'account': self.account,
            'state': self.state,
            'route': self.route,
            'detail': self.detail,
            'tooltip': self.sidebar_tooltip(),
            'code': self.code,
            'retryable': self.retryable,
            'retry_after_at': self.retry_after_at,
            'retry_after_seconds': self.retry_after_seconds,
            'updated_at': self.updated_at,
            'context': dict(self.context),
        }


def classify_http_error(exc, *, fallback_kind='error', fallback_detail='Sync issue', folder=''):
    code = getattr(exc, 'code', None)
    folder_text = str(folder or '').strip()
    folder_suffix = f' for {folder_text}' if folder_text else ''
    if code in (401, 403):
        return SyncDiagnostic('warning', f'Sign-in needs attention{folder_suffix}', code=str(code), retryable=False, folder=folder_text).as_notice()
    if code == 404:
        return SyncDiagnostic('warning', f'Mailbox unavailable{folder_suffix}', code=str(code), retryable=False, folder=folder_text).as_notice()
    if code == 429:
        return SyncDiagnostic('warning', f'Temporarily rate limited{folder_suffix}', code=str(code), retryable=True, folder=folder_text).as_notice()
    if code in (500, 502, 503, 504):
        return SyncDiagnostic('warning', f'Mail service is temporarily unavailable{folder_suffix}', code=str(code), retryable=True, folder=folder_text).as_notice()
    return SyncDiagnostic(fallback_kind, fallback_detail, code=str(code or ''), retryable=True, folder=folder_text).as_notice()


def classify_oauth_token_error(exc, *, fallback_detail='Sign-in needs attention', folder=''):
    folder_text = str(folder or '').strip()
    folder_suffix = f' for {folder_text}' if folder_text else ''
    stage = str(getattr(exc, 'stage', '') or '').strip()
    retryable = bool(getattr(exc, 'retryable', False))
    detail_text = str(getattr(exc, 'detail', '') or '').strip() or fallback_detail
    context = {
        'source': str(getattr(exc, 'source', '') or 'oauth').strip().lower() or 'oauth',
        'stage': stage,
        'reason': detail_text,
        'retryable': retryable,
    }
    if stage:
        context['stage'] = stage
    return SyncDiagnostic(
        'warning',
        f'{fallback_detail}{folder_suffix}',
        retryable=retryable,
        folder=folder_text,
        context=context,
    ).as_notice()


def build_sync_policy(provider, primary, fallback, reconcile, notes=''):
    return SyncPolicy(
        provider=str(provider or '').strip().lower(),
        primary=str(primary or '').strip(),
        fallback=str(fallback or '').strip(),
        reconcile=str(reconcile or '').strip(),
        notes=str(notes or '').strip(),
    ).as_dict()


def build_count_policy(provider, primary, fallback, reconcile, *, route='primary', source='', notes=''):
    return CountPolicy(
        provider=str(provider or '').strip().lower(),
        primary=str(primary or '').strip(),
        fallback=str(fallback or '').strip(),
        reconcile=str(reconcile or '').strip(),
        route=str(route or 'primary').strip().lower() or 'primary',
        source=str(source or '').strip(),
        notes=str(notes or '').strip(),
    ).as_dict()


def messages_changed(previous_messages, refreshed_messages):
    previous = {
        (
            str(msg.get('uid', '') or ''),
            bool(msg.get('is_read', True)),
        )
        for msg in (previous_messages or [])
        if str(msg.get('uid', '') or '')
    }
    refreshed = {
        (
            str(msg.get('uid', '') or ''),
            bool(msg.get('is_read', True)),
        )
        for msg in (refreshed_messages or [])
        if str(msg.get('uid', '') or '')
    }
    return previous != refreshed


def retry_delay_for_http_error(exc, default=60, maximum=900):
    try:
        retry_after = int(getattr(exc, 'headers', {}).get('Retry-After', '0') or 0)
    except Exception:
        retry_after = 0
    if retry_after > 0:
        return max(1, min(int(retry_after), int(maximum)))
    code = getattr(exc, 'code', None)
    if code == 429:
        return min(max(1, int(default)), int(maximum))
    if code in (401, 403):
        return min(max(300, int(default)), int(maximum))
    if code in (500, 502, 503, 504):
        return min(max(30, int(default)), int(maximum))
    return min(max(1, int(default)), int(maximum))


def coerce_account_descriptor(source, provider_kind):
    if isinstance(source, AccountDescriptor):
        descriptor = source
    else:
        descriptor = getattr(source, 'account_descriptor', None)
    if descriptor is None:
        raise ValueError('mail account descriptor unavailable')
    if descriptor.provider_kind != provider_kind:
        raise ValueError(f'Expected provider {provider_kind}, got {descriptor.provider_kind}')
    return descriptor

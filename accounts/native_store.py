"""Persistent native account storage and wrappers for manual mail accounts."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gi
gi.require_version('GLib', '2.0')
gi.require_version('Secret', '1')
from gi.repository import GLib, Secret

try:
    from ..accounts.descriptors import AccountDescriptor
    from ..accounts.account_prefs import (
        AccountPreferenceRecord,
        merge_account_preference,
        remove_account_preference,
        upsert_account_preference,
    )
    from ..accounts.auth.google_native import refresh_google_access_token, revoke_google_token
    from ..accounts.auth.oauth_common import OAuthTokenAcquisitionError
except ImportError:
    from accounts.descriptors import AccountDescriptor
    from accounts.account_prefs import (
        AccountPreferenceRecord,
        merge_account_preference,
        remove_account_preference,
        upsert_account_preference,
    )
    from accounts.auth.google_native import refresh_google_access_token, revoke_google_token
    from accounts.auth.oauth_common import OAuthTokenAcquisitionError


_NATIVE_ACCOUNTS_FILE = Path(GLib.get_user_config_dir()) / 'hermod' / 'native-accounts.json'
_native_lock = threading.RLock()
_SECRET_SCHEMA = Secret.Schema.new(
    'io.github.hermod.Mail',
    Secret.SchemaFlags.NONE,
    {
        'account_id': Secret.SchemaAttributeType.STRING,
        'password_id': Secret.SchemaAttributeType.STRING,
    },
)


@dataclass(frozen=True, slots=True)
class NativeAccountRecord:
    id: str
    provider_kind: str
    identity: str
    presentation_name: str
    alias: str
    accent_color: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


def _native_account_defaults():
    return {
        'provider_kind': 'imap-smtp',
        'identity': '',
        'presentation_name': '',
        'alias': '',
        'accent_color': '',
        'config': {},
        'enabled': True,
    }


def _load_native_accounts_raw():
    try:
        with open(_NATIVE_ACCOUNTS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return []
    records = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        merged = _native_account_defaults()
        merged.update(row)
        merged['config'] = dict(merged.get('config') or {})
        merged['id'] = str(merged.get('id') or '').strip() or uuid.uuid4().hex
        merged['provider_kind'] = str(merged.get('provider_kind') or 'imap-smtp').strip().lower() or 'imap-smtp'
        merged['identity'] = str(merged.get('identity') or '').strip()
        merged['presentation_name'] = str(merged.get('presentation_name') or merged['identity'] or '').strip()
        merged['alias'] = str(merged.get('alias') or '').strip()
        merged['accent_color'] = str(merged.get('accent_color') or '').strip()
        merged['enabled'] = bool(merged.get('enabled', True))
        records.append(NativeAccountRecord(**merged))
    return records


def _save_native_accounts_raw(records):
    _NATIVE_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    serial = []
    for record in records:
        serial.append({
            'id': record.id,
            'provider_kind': record.provider_kind,
            'identity': record.identity,
            'presentation_name': record.presentation_name,
            'alias': record.alias,
            'accent_color': record.accent_color,
            'config': dict(record.config or {}),
            'enabled': bool(record.enabled),
        })
    tmp_path = _NATIVE_ACCOUNTS_FILE.with_suffix('.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(serial, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
        handle.flush()
    tmp_path.replace(_NATIVE_ACCOUNTS_FILE)


def list_native_account_records():
    with _native_lock:
        return _load_native_accounts_raw()


def get_native_account_record(account_id):
    account_id = str(account_id or '').strip()
    if not account_id:
        return None
    for record in list_native_account_records():
        if record.id == account_id:
            return record
    return None


def upsert_native_account(record):
    if not isinstance(record, NativeAccountRecord):
        raise TypeError('record must be NativeAccountRecord')
    with _native_lock:
        records = [row for row in _load_native_accounts_raw() if row.id != record.id]
        records.append(record)
        records.sort(key=lambda row: (row.alias or row.presentation_name or row.identity).lower())
        _save_native_accounts_raw(records)
    return record


def remove_native_account(account_id):
    account_id = str(account_id or '').strip()
    if not account_id:
        return False
    with _native_lock:
        records = _load_native_accounts_raw()
        removed_record = next((row for row in records if row.id == account_id), None)
        kept = [row for row in records if row.id != account_id]
        if len(kept) == len(records):
            return False
        _save_native_accounts_raw(kept)
    token_bundle = load_native_oauth_token_bundle(account_id)
    oauth_provider = str(((removed_record.config if removed_record is not None else {}) or {}).get('oauth_provider') or '').strip().lower()
    revoke_token = str(token_bundle.get('refresh_token') or token_bundle.get('access_token') or '').strip()
    if oauth_provider == 'google' and revoke_token:
        revoke_google_token(revoke_token)
    clear_native_password(account_id, 'imap-password')
    clear_native_password(account_id, 'smtp-password')
    clear_native_oauth_token_bundle(account_id)
    if removed_record is not None:
        remove_account_preference('native', removed_record.provider_kind, removed_record.identity)
    return True


def upsert_native_account_with_prefs(record):
    upsert_native_account(record)
    pref = AccountPreferenceRecord(
        source='native',
        provider_kind=record.provider_kind,
        identity=record.identity,
        alias=record.alias,
        accent_color=record.accent_color,
        enabled=bool(record.enabled),
    )
    upsert_account_preference(pref)
    return record


def store_native_secret(account_id, secret_id, value):
    account_id = str(account_id or '').strip()
    secret_id = str(secret_id or '').strip()
    if not account_id or not secret_id:
        raise ValueError('account_id and secret_id are required')
    return Secret.password_store_sync(
        _SECRET_SCHEMA,
        {
            'account_id': account_id,
            'password_id': secret_id,
        },
        Secret.COLLECTION_DEFAULT,
        f'Hermod {secret_id} for {account_id}',
        str(value or ''),
        None,
    )


def lookup_native_secret(account_id, secret_id):
    account_id = str(account_id or '').strip()
    secret_id = str(secret_id or '').strip()
    if not account_id or not secret_id:
        return ''
    try:
        return Secret.password_lookup_sync(
            _SECRET_SCHEMA,
            {
                'account_id': account_id,
                'password_id': secret_id,
            },
            None,
        ) or ''
    except Exception:
        return ''


def clear_native_secret(account_id, secret_id):
    account_id = str(account_id or '').strip()
    secret_id = str(secret_id or '').strip()
    if not account_id or not secret_id:
        return False
    try:
        return bool(Secret.password_clear_sync(
            _SECRET_SCHEMA,
            {
                'account_id': account_id,
                'password_id': secret_id,
            },
            None,
        ))
    except Exception:
        return False


def store_native_password(account_id, password_id, password):
    return store_native_secret(account_id, password_id, password)


def lookup_native_password(account_id, password_id):
    return lookup_native_secret(account_id, password_id)


def clear_native_password(account_id, password_id):
    return clear_native_secret(account_id, password_id)


_NATIVE_OAUTH_SECRET_ID = 'oauth-tokens'


def load_native_oauth_token_bundle(account_id):
    raw = lookup_native_secret(account_id, _NATIVE_OAUTH_SECRET_ID)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def store_native_oauth_token_bundle(account_id, bundle):
    payload = json.dumps(dict(bundle or {}), ensure_ascii=False, sort_keys=True)
    return store_native_secret(account_id, _NATIVE_OAUTH_SECRET_ID, payload)


def clear_native_oauth_token_bundle(account_id):
    return clear_native_secret(account_id, _NATIVE_OAUTH_SECRET_ID)


class _NativeAccountProxy:
    def __init__(self, record: NativeAccountRecord):
        provider_type = {
            'gmail': 'google',
            'microsoft-graph': 'ms_graph',
            'imap-smtp': 'imap_smtp',
        }.get(str(record.provider_kind or '').strip().lower(), 'imap_smtp')
        self._record = record
        self.props = SimpleNamespace(
            id=record.id,
            identity=record.identity,
            presentation_identity=record.presentation_name or record.identity,
            provider_type=provider_type,
            mail_disabled=False,
        )

    def call_ensure_credentials_sync(self, _cancellable=None):
        return True


class _NativePasswordProxy:
    def __init__(self, record: NativeAccountRecord):
        self._record = record

    def call_get_password_sync(self, password_id, _cancellable=None):
        account_id = self._record.id
        password = lookup_native_password(account_id, password_id)
        if not password and password_id != 'imap-password':
            password = lookup_native_password(account_id, 'imap-password')
        if not password and password_id != 'smtp-password':
            password = lookup_native_password(account_id, 'smtp-password')
        return True, password


class _NativeMailProxy:
    def __init__(self, record: NativeAccountRecord):
        cfg = dict(record.config or {})
        self.props = SimpleNamespace(
            imap_host=cfg.get('imap_host', ''),
            imap_user_name=cfg.get('imap_user_name') or record.identity,
            imap_use_ssl=bool(cfg.get('imap_use_ssl', True)),
            imap_use_tls=bool(cfg.get('imap_use_tls', False)),
            imap_accept_ssl_errors=bool(cfg.get('imap_accept_ssl_errors', False)),
            imap_supported=True,
            smtp_host=cfg.get('smtp_host', ''),
            smtp_user_name=cfg.get('smtp_user_name') or record.identity,
            smtp_use_ssl=bool(cfg.get('smtp_use_ssl', True)),
            smtp_use_tls=bool(cfg.get('smtp_use_tls', False)),
            smtp_accept_ssl_errors=bool(cfg.get('smtp_accept_ssl_errors', False)),
            smtp_use_auth=bool(cfg.get('smtp_use_auth', True)),
            smtp_auth_login=bool(cfg.get('smtp_auth_login', False)),
            smtp_auth_plain=bool(cfg.get('smtp_auth_plain', True)),
            smtp_auth_xoauth2=bool(cfg.get('smtp_auth_xoauth2', False)),
            smtp_supported=True,
        )


class NativeMailAccountSource:
    def __init__(self, record: NativeAccountRecord):
        self.record = record
        self._account = _NativeAccountProxy(record)
        self._mail = _NativeMailProxy(record)
        self._password = _NativePasswordProxy(record)

    def get_account(self):
        return self._account

    def get_mail(self):
        return self._mail

    def get_password_based(self):
        return self._password


class NativeOAuthAccountSource:
    def __init__(self, record: NativeAccountRecord):
        self.record = record
        self._account = _NativeAccountProxy(record)
        self._mail = _NativeMailProxy(record) if dict(record.config or {}).get('imap_host') else None

    def get_account(self):
        return self._account

    def get_mail(self):
        return self._mail

    def _oauth_config(self):
        return dict(self.record.config or {})

    def get_access_token(self, network_ready_fn=None):
        network_ready_fn = network_ready_fn or (lambda: True)
        if not network_ready_fn():
            raise OAuthTokenAcquisitionError(
                'OAuth token unavailable: network not ready',
                stage='network preflight',
                retryable=True,
                source='google',
            )
        cfg = self._oauth_config()
        provider = str(cfg.get('oauth_provider') or '').strip().lower()
        if provider != 'google':
            raise OAuthTokenAcquisitionError(
                'Native OAuth provider is not supported',
                stage='provider lookup',
                retryable=False,
                source=provider or 'oauth',
            )
        client_id = str(cfg.get('oauth_client_id') or '').strip()
        if not client_id:
            raise OAuthTokenAcquisitionError(
                'Google OAuth client ID is missing',
                stage='client setup',
                retryable=False,
                source='google',
            )
        bundle = load_native_oauth_token_bundle(self.record.id)
        access_token = str(bundle.get('access_token') or '').strip()
        expires_at = float(bundle.get('expires_at') or 0.0)
        now = time.time()
        if access_token and expires_at - 60 > now:
            return access_token
        refresh_token = str(bundle.get('refresh_token') or '').strip()
        if not refresh_token:
            raise OAuthTokenAcquisitionError(
                'Google sign-in is missing a refresh token',
                stage='refresh token',
                retryable=False,
                source='google',
            )
        refreshed = refresh_google_access_token(client_id, refresh_token)
        merged = dict(bundle)
        merged.update(refreshed)
        merged.setdefault('provider', 'google')
        merged['refresh_token'] = refresh_token
        store_native_oauth_token_bundle(self.record.id, merged)
        return str(merged.get('access_token') or '').strip()

    def invalidate_access_token(self):
        bundle = load_native_oauth_token_bundle(self.record.id)
        if not bundle:
            return
        bundle.pop('access_token', None)
        bundle['expires_at'] = 0.0
        store_native_oauth_token_bundle(self.record.id, bundle)


def native_descriptor_from_record(record: NativeAccountRecord):
    if record is None:
        return None
    config = dict(record.config or {})
    oauth_provider = str(config.get('oauth_provider') or '').strip().lower()
    auth_kind = 'native-password'
    source_obj = NativeMailAccountSource(record)
    if oauth_provider:
        auth_kind = 'native-oauth2'
        source_obj = NativeOAuthAccountSource(record)
    descriptor = AccountDescriptor(
        source='native',
        provider_kind=record.provider_kind,
        identity=record.identity,
        presentation_name=record.presentation_name or record.alias or record.identity,
        auth_kind=auth_kind,
        metadata={
            'native_account_id': record.id,
            'alias': record.alias,
            'accent_color': record.accent_color,
            'config': config,
        },
        source_obj=source_obj,
    )
    return merge_account_preference(descriptor, default_source='native')


def get_native_account_descriptors():
    descriptors = []
    for record in list_native_account_records():
        descriptor = native_descriptor_from_record(record)
        if descriptor is not None:
            descriptors.append(descriptor)
    descriptors.sort(key=lambda descriptor: (str(getattr(descriptor, 'presentation_name', '') or '').lower(), descriptor.identity.lower()))
    return descriptors

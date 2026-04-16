"""Local presentation preferences for mail accounts.

This store keeps UI-facing metadata separate from connection credentials:
- alias / display label
- accent color
- enabled / hidden state

It overlays account descriptors without depending on the underlying auth
implementation.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from .descriptors import AccountDescriptor
except ImportError:
    from accounts.descriptors import AccountDescriptor


_PREFS_FILE = Path(GLib.get_user_config_dir()) / 'hermod' / 'account-prefs.json'
_prefs_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class AccountPreferenceRecord:
    source: str
    provider_kind: str
    identity: str
    alias: str = ''
    accent_color: str = ''
    enabled: bool = True


def _prefs_defaults():
    return {
        'source': '',
        'provider_kind': '',
        'identity': '',
        'alias': '',
        'accent_color': '',
        'enabled': True,
    }


def _normalize_record(data):
    merged = _prefs_defaults()
    merged.update(data or {})
    merged['source'] = str(merged.get('source') or '').strip().lower()
    merged['provider_kind'] = str(merged.get('provider_kind') or '').strip().lower()
    merged['identity'] = str(merged.get('identity') or '').strip()
    merged['alias'] = str(merged.get('alias') or '').strip()
    merged['accent_color'] = str(merged.get('accent_color') or '').strip()
    merged['enabled'] = bool(merged.get('enabled', True))
    if not merged['identity']:
        return None
    return AccountPreferenceRecord(**merged)


def _load_raw():
    try:
        with open(_PREFS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return []
    records = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        record = _normalize_record(row)
        if record is not None:
            records.append(record)
    return records


def _save_raw(records):
    _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    serial = [
        {
            'source': record.source,
            'provider_kind': record.provider_kind,
            'identity': record.identity,
            'alias': record.alias,
            'accent_color': record.accent_color,
            'enabled': bool(record.enabled),
        }
        for record in records
    ]
    tmp_path = _PREFS_FILE.with_suffix('.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(serial, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
        handle.flush()
    tmp_path.replace(_PREFS_FILE)


def _record_key(source, provider_kind, identity):
    return (
        str(source or '').strip().lower(),
        str(provider_kind or '').strip().lower(),
        str(identity or '').strip(),
    )


def list_account_preference_records():
    with _prefs_lock:
        return _load_raw()


def get_account_preference_record(source, provider_kind, identity):
    key = _record_key(source, provider_kind, identity)
    if not key[2]:
        return None
    for record in list_account_preference_records():
        if _record_key(record.source, record.provider_kind, record.identity) == key:
            return record
    return None


def upsert_account_preference(record):
    if not isinstance(record, AccountPreferenceRecord):
        raise TypeError('record must be AccountPreferenceRecord')
    key = _record_key(record.source, record.provider_kind, record.identity)
    if not key[2]:
        raise ValueError('identity is required')
    with _prefs_lock:
        records = [
            row for row in _load_raw()
            if _record_key(row.source, row.provider_kind, row.identity) != key
        ]
        records.append(record)
        records.sort(key=lambda row: (row.alias or row.identity).lower())
        _save_raw(records)
    return record


def remove_account_preference(source, provider_kind, identity):
    key = _record_key(source, provider_kind, identity)
    if not key[2]:
        return False
    with _prefs_lock:
        records = _load_raw()
        kept = [
            row for row in records
            if _record_key(row.source, row.provider_kind, row.identity) != key
        ]
        if len(kept) == len(records):
            return False
        _save_raw(kept)
    return True


def prune_account_preferences(active_accounts):
    active = {
        _record_key(source, provider_kind, identity)
        for source, provider_kind, identity in (active_accounts or [])
        if _record_key(source, provider_kind, identity)[2]
    }
    removed = []
    with _prefs_lock:
        records = _load_raw()
        kept = []
        for record in records:
            key = _record_key(record.source, record.provider_kind, record.identity)
            if key in active:
                kept.append(record)
                continue
            removed.append(record)
        if len(kept) != len(records):
            _save_raw(kept)
    return removed


def merge_account_preference(descriptor: AccountDescriptor, default_source=''):
    if descriptor is None:
        return None
    source = getattr(descriptor, 'source', default_source) or default_source
    record = get_account_preference_record(source, descriptor.provider_kind, descriptor.identity)
    if record is None:
        return descriptor
    if not record.enabled:
        return None
    metadata = dict(getattr(descriptor, 'metadata', {}) or {})
    metadata['alias'] = record.alias
    metadata['accent_color'] = record.accent_color
    return AccountDescriptor(
        source=descriptor.source,
        provider_kind=descriptor.provider_kind,
        identity=descriptor.identity,
        presentation_name=record.alias or descriptor.presentation_name or descriptor.identity,
        auth_kind=descriptor.auth_kind,
        metadata=metadata,
        source_obj=descriptor.source_obj,
    )


def account_display_name(source, provider_kind, identity, fallback=''):
    record = get_account_preference_record(source, provider_kind, identity)
    if record is not None and record.alias:
        return record.alias
    return fallback or identity

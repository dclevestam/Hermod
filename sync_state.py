"""Persistent sync cursors and recent-message caches."""

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib


_SYNC_STATE_DIR = Path(GLib.get_user_cache_dir()) / 'hermod'
_SYNC_STATE_FILE = _SYNC_STATE_DIR / 'sync-state.json'
_SYNC_STATE_LOCK = threading.RLock()


def _default_state():
    return {
        'version': 1,
        'providers': {},
    }


def _load_all():
    try:
        with open(_SYNC_STATE_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault('version', 1)
            data.setdefault('providers', {})
            return data
    except Exception:
        pass
    return _default_state()


def _store_all(data):
    _SYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix='sync-state.',
        suffix='.tmp',
        dir=_SYNC_STATE_DIR,
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _SYNC_STATE_FILE)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def get_account_state(provider, account):
    with _SYNC_STATE_LOCK:
        data = _load_all()
        return copy.deepcopy(
            data.get('providers', {}).get(provider, {}).get(account, {})
        )


def set_account_state(provider, account, state):
    with _SYNC_STATE_LOCK:
        data = _load_all()
        providers = data.setdefault('providers', {})
        bucket = providers.setdefault(provider, {})
        if state:
            bucket[account] = copy.deepcopy(state)
        else:
            bucket.pop(account, None)
            if not bucket:
                providers.pop(provider, None)
        _store_all(data)


def list_account_states(provider):
    with _SYNC_STATE_LOCK:
        data = _load_all()
        provider_state = data.get('providers', {}).get(provider, {})
        return copy.deepcopy(provider_state)


def prune_account_states(provider, active_accounts):
    active = {str(account or '').strip() for account in (active_accounts or []) if str(account or '').strip()}
    removed = []
    with _SYNC_STATE_LOCK:
        data = _load_all()
        providers = data.setdefault('providers', {})
        bucket = providers.get(provider, {})
        for account in list(bucket.keys()):
            if account not in active:
                removed.append(account)
                bucket.pop(account, None)
        if not bucket:
            providers.pop(provider, None)
        _store_all(data)
    return removed

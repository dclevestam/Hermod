"""Low-overhead structured diagnostics event logger."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from ..settings import get_settings
    from .redact import redact_text, redact_value
except ImportError:
    from settings import get_settings
    from diagnostics.redact import redact_text, redact_value


_DIAGNOSTICS_DIR = Path(GLib.get_user_cache_dir()) / 'lark' / 'diagnostics'
_EVENTS_FILE = _DIAGNOSTICS_DIR / 'events.jsonl'
_MAX_EVENT_BYTES = 256 * 1024
_MAX_EVENT_LINES = 400


def diagnostics_dir():
    return _DIAGNOSTICS_DIR


def events_file():
    return _EVENTS_FILE


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _trim_events_file(path):
    try:
        if not path.exists() or path.stat().st_size <= _MAX_EVENT_BYTES:
            return
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        kept = lines[-_MAX_EVENT_LINES:]
        text = '\n'.join(kept)
        if text:
            text += '\n'
        fd, tmp_path = tempfile.mkstemp(prefix='events.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
    except Exception:
        return


def log_event(kind, *, level='info', message='', context=None, persist=True):
    event = {
        'ts': _utcnow_iso(),
        'level': str(level or 'info'),
        'kind': str(kind or 'event'),
        'message': redact_text(message),
        'context': redact_value(context or {}),
    }
    if not persist:
        return event
    try:
        _DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_EVENTS_FILE, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=True, separators=(',', ':')) + '\n')
        _trim_events_file(_EVENTS_FILE)
    except Exception:
        pass
    return event


def log_exception(prefix, exc, *, context=None):
    event_context = {
        'exception_type': exc.__class__.__name__,
    }
    if context:
        event_context.update(context)
    return log_event(
        'exception',
        level='error',
        message=f'{prefix}: {exc}',
        context=event_context,
        persist=True,
    )


def recent_events(limit=_MAX_EVENT_LINES):
    try:
        lines = _EVENTS_FILE.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return []
    events = []
    for line in lines[-max(0, int(limit)):]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def log_startup_summary(backends):
    if not backends:
        provider_counts = {}
    else:
        provider_counts = {}
        for backend in backends:
            provider = str(getattr(backend, 'provider', 'unknown') or 'unknown')
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
    return log_event(
        'startup',
        level='info',
        message='Application started',
        context={'backend_count': len(backends or []), 'providers': provider_counts},
        persist=True,
    )


def log_network_change(available):
    return log_event(
        'network',
        level='info',
        message='Network availability changed',
        context={'available': bool(available)},
        persist=True,
    )


def should_print_debug_tracebacks():
    try:
        return bool(get_settings().get('debug_logging'))
    except Exception:
        return False

"""Diagnostics export helpers."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    from .health import build_health_snapshot
    from .logger import events_file, log_event, recent_perf_events
except ImportError:
    from diagnostics.health import build_health_snapshot
    from diagnostics.logger import events_file, log_event, recent_perf_events


def default_export_path():
    downloads = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or Path.home())
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return downloads / f'lark-diagnostics-{stamp}.zip'


def export_diagnostics_bundle(target_path=None):
    target = Path(target_path) if target_path else default_export_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_health_snapshot()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=True) + '\n')
        path = events_file()
        if path.exists():
            archive.write(path, arcname='events.jsonl')
        else:
            archive.writestr('events.jsonl', '')
        archive.writestr('perf.json', json.dumps(recent_perf_events(), indent=2, ensure_ascii=True) + '\n')
    log_event(
        'diagnostics-export',
        level='info',
        message='Diagnostics bundle exported',
        context={'filename': target.name},
        persist=True,
    )
    return target

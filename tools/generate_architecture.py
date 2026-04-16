#!/usr/bin/env python3
"""Generate Hermod's coarse architecture graph."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'ARCHITECTURE.json'


def _iter_source_files():
    for path in ROOT.rglob('*.py'):
        if 'tests' in path.parts or 'tools' in path.parts:
            continue
        if path.name == '__init__.py' or path.parent == ROOT or path.parent.name in {'accounts', 'diagnostics', 'providers'}:
            yield path


def _module_name_for_path(path):
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix('').parts)
    if not parts:
        return ''
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def _known_modules():
    modules = {}
    for path in _iter_source_files():
        name = _module_name_for_path(path)
        if name:
            modules[name] = path
    return modules


def _resolve_module_name(name, known_modules):
    text = str(name or '').strip()
    if not text:
        return ''
    candidates = text.split('.')
    for end in range(len(candidates), 0, -1):
        candidate = '.'.join(candidates[:end])
        if candidate in known_modules:
            return candidate
    return ''


def _resolve_relative_module(current_module, module, level):
    base_parts = current_module.split('.')[:-1]
    if level <= 0:
        return module or ''
    if not base_parts:
        return module or ''
    up = max(0, len(base_parts) - (level - 1))
    prefix = base_parts[:up]
    if module:
        prefix.extend(module.split('.'))
    return '.'.join(prefix)


def _discover_import_edges(known_modules):
    edges = []
    for module_name, path in sorted(known_modules.items()):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = _resolve_module_name(alias.name, known_modules)
                    if target and target != module_name:
                        edges.append({'from': module_name, 'to': target, 'type': 'import'})
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_relative_module(module_name, node.module, node.level or 0)
                target = _resolve_module_name(resolved, known_modules)
                if target and target != module_name:
                    edges.append({'from': module_name, 'to': target, 'type': 'import'})
    deduped = []
    seen = set()
    for edge in edges:
        key = (edge['from'], edge['to'], edge['type'])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _discover_module_index(known_modules):
    module_index = []
    for module_name, path in sorted(known_modules.items()):
        module_index.append({
            'name': module_name,
            'path': str(path.relative_to(ROOT)),
            'package': path.name == '__init__.py',
        })
    return module_index


def build_graph():
    known_modules = _known_modules()
    return {
        "project": {
            "name": "Hermod",
            "root": str(ROOT),
            "generated_by": "tools/generate_architecture.py",
            "query_tool": "tools/query_architecture.py",
            "context_file": ".codex/project_context.json",
            "purpose": "AI-readable dependency and ownership map for safe development",
        },
        "settings": {
            "light_poll_interval_minutes": 1,
            "full_reconcile_interval_minutes": 10,
            "source_of_truth": "provider-backed counts and cached provider state",
        },
        "sources_of_truth": [
            {
                "name": "Gmail API",
                "owned_by": "providers/gmail.py",
                "truth": [
                    "message list",
                    "thread refresh",
                    "unread counts while primary route is healthy",
                ],
                "fallback": "IMAP",
                "reconcile": "Gmail history refresh plus IMAP unread recount",
                "counting": "Gmail API unread label count while healthy, IMAP UNSEEN while degraded",
            },
            {
                "name": "IMAP/SMTP",
                "owned_by": "providers/imap_smtp.py",
                "truth": [
                    "message list",
                    "message read state",
                    "unread counts via UNSEEN",
                ],
                "fallback": "cached rows plus retry",
                "reconcile": "IMAP folder refresh plus server-side unread recount",
                "counting": "IMAP UNSEEN as the count source",
            },
        ],
        "sync_policies": [
            {
                "provider": "gmail",
                "primary": "Gmail API history and message fetch",
                "fallback": "IMAP fallback with timed API recovery",
                "reconcile": "Gmail history refresh plus IMAP unread recount",
                "counting": "API label count while healthy; IMAP UNSEEN while degraded",
            },
            {
                "provider": "imap",
                "primary": "IMAP fetch and UNSEEN recount",
                "fallback": "Cached rows plus retry on IMAP or SMTP errors",
                "reconcile": "IMAP folder refresh plus server-side unread recount",
                "counting": "IMAP UNSEEN",
            },
        ],
        "contracts": [
            {
                "name": "providers_are_ui_free",
                "scope": ["providers.*"],
                "deny": ["__main__", "compose", "settings", "widgets", "window", "window_*", "styles"],
                "purpose": "Provider code should not depend on UI entrypoints or presentation modules.",
            },
            {
                "name": "core_is_ui_free",
                "scope": [
                    "backends",
                    "unified_refresh",
                    "sync_state",
                    "body_cache",
                    "snapshot_cache",
                    "diagnostics.*",
                    "utils",
                    "accounts.*",
                ],
                "deny": ["__main__", "compose", "widgets", "window", "window_*", "styles"],
                "purpose": "Core/account logic should not depend on presentation modules.",
            },
            {
                "name": "ui_uses_concrete_providers_through_backends",
                "scope": ["compose", "settings", "widgets", "window", "window_*"],
                "deny": ["providers.common", "providers.gmail", "providers.imap_smtp"],
                "purpose": "UI modules should speak to provider state through orchestration layers, not provider internals.",
            },
        ],
        "module_index": _discover_module_index(known_modules),
        "modules": [
            {"name": "__main__.py", "role": "Application entrypoint and poll loop", "owns": ["HermodApp", "background polling cadence", "poll fan-out"]},
            {"name": "window.py", "role": "Main window orchestration and layout", "owns": ["window state machine", "folder navigation", "account chrome"]},
            {"name": "window_mailbox_controller.py", "role": "Mailbox orchestration and provider reconciliation", "owns": ["background update handling", "mailbox load orchestration", "unread count reconciliation", "provider health rendering"]},
            {"name": "window_message_cache.py", "role": "Message list snapshot and prefetch controller", "owns": ["snapshot load/apply", "snapshot save", "body prefetch warmup", "message list cache context"]},
            {"name": "window_message_list.py", "role": "Message list view and selection", "owns": ["message rows", "selection state", "paging", "local read/unread/delete actions"]},
            {"name": "window_reader_controller.py", "role": "Reader fetch and cache controller", "owns": ["body fetch orchestration", "thread fetch orchestration", "reader body cache"]},
            {"name": "window_reader.py", "role": "Reader pane and message body rendering", "owns": ["thread view rendering", "reader display state", "original message display"]},
            {"name": "compose.py", "role": "Inline compose editor", "owns": ["draft composition", "send flow", "reply/reply-all behavior"]},
            {"name": "settings_accounts.py", "role": "Account settings controller", "owns": ["account add/remove", "native Gmail OAuth UI", "manual IMAP/SMTP editor"]},
            {"name": "settings.py", "role": "Settings model and preferences UI", "owns": ["poll cadence", "reconcile cadence", "debug toggles", "settings page composition"]},
            {"name": "backends.py", "role": "Provider registry and backend construction", "owns": ["provider creation", "startup reconciliation", "sync policy description"]},
            {"name": "providers/gmail.py", "role": "Gmail provider", "owns": ["primary Gmail API sync", "IMAP fallback", "timed primary recovery"]},
            {"name": "providers/imap_smtp.py", "role": "Native IMAP/SMTP provider", "owns": ["server fetch", "UNSEEN counts", "message state mutation"]},
            {"name": "providers/common.py", "role": "Shared provider helpers", "owns": ["diagnostic objects", "health state", "retry helpers", "sync policy builder"]},
            {"name": "unified_refresh.py", "role": "Bounded parallel message collection", "owns": ["unified inbox fan-out", "progress callback", "parallel fetch aggregation"]},
            {"name": "widgets.py", "role": "Reusable GTK widgets", "owns": ["account rows", "message rows", "startup status widgets"]},
            {"name": "styles.py", "role": "CSS and color generation", "owns": ["account palette", "sidebar classes", "reader styling"]},
        ],
        "settings_keys": [
            {"key": "poll_interval", "reads": ["__main__.py", "settings.py"], "writes": ["settings.py"], "meaning": "light background check cadence"},
            {"key": "reconcile_interval", "reads": ["__main__.py", "settings.py", "diagnostics/health.py"], "writes": ["settings.py"], "meaning": "full unread/count reconciliation cadence"},
        ],
        "state_flows": [
            {"name": "startup_sync", "path": ["__main__.py::_on_activate", "backends.py::get_backends", "window_mailbox_controller.py::_load_messages", "providers/*::check_background_updates", "window_mailbox_controller.py::on_background_update"]},
            {"name": "local_read_unread_action", "path": ["window_message_list.py::_commit_email_selection", "window_mailbox_controller.py::_refresh_provider_counts_for_message", "providers/*::get_unread_count", "window_mailbox_controller.py::update_account_counts", "window_mailbox_controller.py::refresh_visible_mail"]},
            {"name": "poll_cycle", "path": ["__main__.py::_poll_loop", "providers/*::check_background_updates", "window_mailbox_controller.py::on_background_update"]},
        ],
        "fallbacks": [
            {"provider": "gmail", "primary": "Gmail API", "fallback": "IMAP", "recovery": "timed primary probe"},
            {"provider": "imap", "primary": "server fetch", "fallback": "cached rows", "recovery": "next server poll"},
        ],
        "edges": [
            {"from": "__main__.py", "to": "backends.py", "type": "calls"},
            {"from": "__main__.py", "to": "window.py", "type": "owns"},
            {"from": "__main__.py", "to": "window_mailbox_controller.py", "type": "polls"},
            {"from": "window.py", "to": "window_mailbox_controller.py", "type": "mixes"},
            {"from": "window.py", "to": "window_message_cache.py", "type": "mixes"},
            {"from": "window.py", "to": "window_message_list.py", "type": "renders"},
            {"from": "window.py", "to": "window_reader_controller.py", "type": "mixes"},
            {"from": "window.py", "to": "window_reader.py", "type": "renders"},
            {"from": "window.py", "to": "compose.py", "type": "opens"},
            {"from": "settings.py", "to": "settings_accounts.py", "type": "composes"},
            {"from": "settings.py", "to": "window.py", "type": "writes"},
            {"from": "settings.py", "to": "backends.py", "type": "writes"},
            {"from": "backends.py", "to": "providers/gmail.py", "type": "creates"},
            {"from": "backends.py", "to": "providers/imap_smtp.py", "type": "creates"},
            {"from": "providers/gmail.py", "to": "providers/common.py", "type": "reads"},
            {"from": "providers/imap_smtp.py", "to": "providers/common.py", "type": "reads"},
            {"from": "providers/gmail.py", "to": "window_mailbox_controller.py", "type": "refreshes"},
            {"from": "providers/imap_smtp.py", "to": "window_mailbox_controller.py", "type": "refreshes"},
            {"from": "unified_refresh.py", "to": "providers/*", "type": "calls"},
            {"from": "widgets.py", "to": "styles.py", "type": "reads"},
            {"from": "window_message_list.py", "to": "widgets.py", "type": "renders"},
            {"from": "window_reader.py", "to": "widgets.py", "type": "renders"},
        ],
        "import_edges": _discover_import_edges(known_modules),
        "notes": [
            "Keep this graph coarse and semantic.",
            "Prefer provider ownership over UI inference.",
            "Refresh this file when modules or sync ownership change.",
        ],
    }


def _write_graph(graph):
    OUTPUT.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _load_existing_graph():
    if not OUTPUT.exists():
        return None
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Generate or verify Hermod's architecture graph.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that ARCHITECTURE.json is current without rewriting it",
    )
    args = parser.parse_args()

    graph = build_graph()
    if args.check:
        existing = _load_existing_graph()
        if existing != graph:
            print("ARCHITECTURE.json is stale; run tools/update_architecture.sh", file=sys.stderr)
            return 1
        return 0

    _write_graph(graph)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

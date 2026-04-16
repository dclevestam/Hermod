#!/usr/bin/env python3
"""Generate persistent AI project context for Hermod."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / 'ARCHITECTURE.json'
OUTPUT = ROOT / '.codex' / 'project_context.json'


def _load_graph():
    return json.loads(GRAPH.read_text(encoding='utf-8'))


def build_context():
    graph = _load_graph()
    return {
        'project': graph.get('project', {}),
        'graph_file': 'ARCHITECTURE.json',
        'query_tool': graph.get('project', {}).get('query_tool', 'tools/query_architecture.py'),
        'update_tool': 'tools/update_architecture.sh',
        'contracts_tool': 'tools/check_architecture_contracts.py',
        'navigation_order': [
            '__main__',
            'backends',
            'window',
            'window_mailbox_controller',
            'window_message_cache',
            'window_message_list',
            'window_reader_controller',
            'window_reader',
            'settings_accounts',
            'providers.gmail',
            'providers.imap_smtp',
        ],
        'stable_anchors': {
            'sources_of_truth': [item['name'] for item in graph.get('sources_of_truth', [])],
            'sync_policies': [item['provider'] for item in graph.get('sync_policies', [])],
            'hot_modules': [
                '__main__.py',
                'window.py',
                'window_mailbox_controller.py',
                'window_message_cache.py',
                'window_reader_controller.py',
                'settings_accounts.py',
                'providers/gmail.py',
                'providers/imap_smtp.py',
            ],
            'safety_rules': [
                'provider truth stays in provider code',
                'UI should render, not infer, diagnostics',
                'graph and contracts should be regenerated after architecture changes',
            ],
        },
        'current_focus': {
            'mail_truth': 'provider-backed unread counts and mailbox refresh',
            'architecture': 'coarse graph plus contracts plus query helper',
            'sync_policy': 'primary route first, fallback only when degraded',
        },
    }


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build_context(), indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

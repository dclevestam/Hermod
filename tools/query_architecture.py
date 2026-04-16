#!/usr/bin/env python3
"""Query Hermod's architecture graph for AI-oriented navigation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / 'ARCHITECTURE.json'
PROJECT_CONTEXT = ROOT / '.codex' / 'project_context.json'

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.architecture_lib import ArchitectureGraph


def _ask(arch, question):
    q = question.lower()
    if 'unread' in q or 'count' in q or 'badge' in q:
        return {
            'topic': 'unread-counts',
            'sources_of_truth': arch.graph.get('sources_of_truth', []),
            'sync_policies': arch.graph.get('sync_policies', []),
            'count_policies': arch.graph.get('sync_policies', []),
            'owners': arch.owner_hits('unread'),
        }
    if 'fallback' in q or 'recovery' in q or 'retry' in q:
        return {
            'topic': 'fallbacks',
            'sync_policies': arch.graph.get('sync_policies', []),
            'fallbacks': arch.graph.get('fallbacks', []),
            'owners': arch.owner_hits('fallback'),
        }
    if 'startup' in q or 'status' in q or 'loading' in q:
        return {
            'topic': 'startup',
            'owners': arch.owner_hits('startup'),
            'modules': [arch.summarize_module(name) for name in ['__main__', 'window_message_list', 'widgets'] if arch.summarize_module(name)],
        }
    if 'settings' in q or 'interval' in q:
        return {
            'topic': 'settings',
            'settings': arch.graph.get('settings', {}),
            'settings_keys': arch.graph.get('settings_keys', []),
            'owners': arch.owner_hits('settings'),
        }
    if 'gmail' in q:
        return {
            'topic': 'gmail',
            'source': arch.lookup_source('gmail'),
            'module': arch.summarize_module('providers.gmail'),
            'owners': arch.owner_hits('gmail'),
        }
    if 'microsoft' in q or 'graph' in q:
        return {
            'topic': 'microsoft',
            'supported': False,
            'detail': 'Microsoft mail is not in the active native-only runtime yet.',
        }
    if 'imap' in q:
        return {
            'topic': 'imap',
            'source': arch.lookup_source('imap'),
            'module': arch.summarize_module('providers.imap_smtp'),
            'owners': arch.owner_hits('imap'),
        }
    return {
        'topic': 'overview',
        'project': arch.graph.get('project', {}),
        'settings': arch.graph.get('settings', {}),
        'sources_of_truth': arch.graph.get('sources_of_truth', []),
        'sync_policies': arch.graph.get('sync_policies', []),
    }


def main():
    parser = argparse.ArgumentParser(description='Query Hermod architecture graph.')
    parser.add_argument('--graph', type=Path, default=GRAPH, help='path to ARCHITECTURE.json or equivalent')
    parser.add_argument('--context', type=Path, default=PROJECT_CONTEXT, help='path to persistent AI project context')
    parser.add_argument('--module', action='append', default=[], help='summarize one or more modules')
    parser.add_argument('--depends-on', dest='depends_on', action='append', default=[], help='show modules a target imports')
    parser.add_argument('--used-by', action='append', default=[], help='show modules that import a target')
    parser.add_argument('--owner', action='append', default=[], help='find owners by semantic term')
    parser.add_argument('--source', action='append', default=[], help='lookup sources of truth or sync policies')
    parser.add_argument('--contract', action='append', default=[], help='lookup contracts by name or term')
    parser.add_argument('--impact', action='append', default=[], help='summarize what a module or topic affects')
    parser.add_argument('--blast-radius', action='append', default=[], help='show transitive dependency impact')
    parser.add_argument('--path', nargs=2, metavar=('SOURCE', 'TARGET'), action='append', default=[], help='show a dependency path between two modules')
    parser.add_argument('--risk', action='append', default=[], help='assess edit risk for a module or topic')
    parser.add_argument('--change-plan', action='append', default=[], help='suggest edit order for a target')
    parser.add_argument('--stable-anchors', action='store_true', help='print the persistent project anchors')
    parser.add_argument('--depth', type=int, default=2, help='blast-radius traversal depth')
    parser.add_argument('--ask', help='lightweight AI-oriented question query')
    parser.add_argument('--format', choices=['json', 'text'], default='json', help='output format')
    args = parser.parse_args()

    arch = ArchitectureGraph.from_files(args.graph, args.context)
    result = {}

    if arch.context:
        result['project_context'] = arch.context
    if args.ask:
        result['ask'] = _ask(arch, args.ask)
    if args.module:
        result['modules'] = [arch.summarize_module(name) for value in args.module for name in arch.module_candidates(value)]
    if args.depends_on:
        result['depends_on'] = [arch.summarize_module(name) for value in args.depends_on for name in arch.module_candidates(value)]
    if args.used_by:
        used_by = {}
        for value in args.used_by:
            targets = arch.module_candidates(value)
            importers = sorted({importer for target in targets for importer in arch.index['reverse_imports'].get(target, [])})
            used_by[value] = [arch.summarize_module(name) for name in importers]
        result['used_by'] = used_by
    if args.owner:
        result['owners'] = [arch.owner_hits(value) for value in args.owner]
    if args.source:
        result['sources'] = [arch.lookup_source(value) for value in args.source]
    if args.contract:
        result['contracts'] = [arch.contract_hits(value) for value in args.contract]
    if args.impact:
        result['impact'] = [arch.impact_report(value) for value in args.impact]
    if args.blast_radius:
        result['blast_radius'] = [arch.blast_radius(value, max(1, args.depth)) for value in args.blast_radius]
    if args.path:
        result['path'] = [arch.path_between(source, target) for source, target in args.path]
    if args.risk:
        result['risk'] = [arch.risk_report(value) for value in args.risk]
    if args.change_plan:
        result['change_plan'] = [arch.change_plan(value) for value in args.change_plan]
    if args.stable_anchors:
        result['stable_anchors'] = arch.stable_anchors()

    if not result:
        result = {
            'project': arch.graph.get('project', {}),
            'settings': arch.graph.get('settings', {}),
            'module_count': len(arch.graph.get('module_index', [])),
            'source_count': len(arch.graph.get('sources_of_truth', [])),
            'contract_count': len(arch.graph.get('contracts', [])),
        }

    if args.format == 'json':
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write('\n')
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

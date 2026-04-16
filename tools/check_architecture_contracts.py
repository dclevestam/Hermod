#!/usr/bin/env python3
"""Check Hermod's architecture contracts against the generated graph."""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / 'ARCHITECTURE.json'


def _load_graph():
    return json.loads(GRAPH.read_text(encoding='utf-8'))


def _matches(pattern, value):
    return fnmatch.fnmatchcase(value, pattern)


def _check_contract(contract, edges):
    violations = []
    scope = contract.get('scope', [])
    deny = contract.get('deny', [])
    allow = contract.get('allow', [])
    for edge in edges:
        if not any(_matches(pattern, edge['from']) for pattern in scope):
            continue
        if allow and any(_matches(pattern, edge['to']) for pattern in allow):
            continue
        if any(_matches(pattern, edge['to']) for pattern in deny):
            violations.append({
                'contract': contract['name'],
                'from': edge['from'],
                'to': edge['to'],
                'type': edge['type'],
                'purpose': contract.get('purpose', ''),
            })
    return violations


def main():
    graph = _load_graph()
    contracts = graph.get('contracts', [])
    edges = graph.get('import_edges', [])
    violations = []
    for contract in contracts:
        violations.extend(_check_contract(contract, edges))
    if violations:
        print('Architecture contract violations detected:', file=sys.stderr)
        for item in violations:
            print(
                f"- {item['contract']}: {item['from']} -> {item['to']} ({item['type']})",
                file=sys.stderr,
            )
            if item['purpose']:
                print(f"  {item['purpose']}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

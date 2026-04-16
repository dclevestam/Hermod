"""Architecture graph helpers for AI-oriented navigation."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def normalize_module_name(value):
    return value.replace('.py', '') if value.endswith('.py') else value


def build_index(graph):
    modules = {item['name']: item for item in graph.get('modules', [])}
    module_index = {item['name']: item for item in graph.get('module_index', [])}
    path_index = {item['path']: item for item in graph.get('module_index', [])}
    owners = defaultdict(list)
    reverse_imports = defaultdict(list)
    forward_imports = defaultdict(list)

    for item in graph.get('modules', []):
        for owned in item.get('owns', []):
            owners[owned].append(item['name'])

    for edge in graph.get('import_edges', []):
        forward_imports[edge['from']].append(edge['to'])
        reverse_imports[edge['to']].append(edge['from'])

    return {
        'modules': modules,
        'module_index': module_index,
        'path_index': path_index,
        'owners': owners,
        'forward_imports': forward_imports,
        'reverse_imports': reverse_imports,
    }


class ArchitectureGraph:
    def __init__(self, graph, context=None):
        self.graph = graph
        self.context = context
        self.index = build_index(graph)

    @classmethod
    def from_files(cls, graph_path, context_path=None):
        graph = load_json(graph_path)
        context = load_json(context_path) if context_path and Path(context_path).exists() else None
        return cls(graph, context)

    def module_candidates(self, value):
        value = normalize_module_name(value)
        matches = []
        for name, item in self.index['module_index'].items():
            if value == name or value == item['path'] or value == item['path'].removesuffix('.py'):
                matches.append(name)
        return matches

    def summarize_module(self, name):
        module_entry = self.index['module_index'].get(name)
        if not module_entry:
            for candidate in self.index['module_index'].values():
                if candidate['path'] == name or candidate['path'].removesuffix('.py') == name:
                    module_entry = candidate
                    name = candidate['name']
                    break
        if not module_entry:
            return None
        semantic_key = module_entry['path'] if module_entry['path'].endswith('.py') else module_entry['path'].removesuffix('/__init__.py')
        module = self.index['modules'].get(semantic_key)
        incoming = sorted(set(self.index['reverse_imports'].get(name, [])))
        outgoing = sorted(set(self.index['forward_imports'].get(name, [])))
        return {
            'name': name,
            'path': module_entry['path'],
            'role': module.get('role', '') if module else '',
            'owns': module.get('owns', []) if module else [],
            'imports': outgoing,
            'imported_by': incoming,
        }

    def lookup_source(self, value):
        needle = value.lower()
        for item in self.graph.get('sources_of_truth', []):
            blob = ' '.join([
                item.get('name', ''),
                item.get('owned_by', ''),
                item.get('fallback', ''),
                item.get('reconcile', ''),
                item.get('counting', ''),
            ]).lower()
            if needle in blob:
                return item
        for item in self.graph.get('sync_policies', []):
            blob = ' '.join([
                item.get('provider', ''),
                item.get('primary', ''),
                item.get('fallback', ''),
                item.get('reconcile', ''),
                item.get('counting', ''),
            ]).lower()
            if needle in blob:
                return item
        return None

    def owner_hits(self, value):
        needle = value.lower()
        hits = []
        for module_name, module in self.index['modules'].items():
            blob = ' '.join([module_name, module.get('role', ''), ' '.join(module.get('owns', []))]).lower()
            if needle in blob:
                summary = self.summarize_module(module_name)
                if summary:
                    hits.append(summary)
        return hits

    def contract_hits(self, value):
        needle = value.lower()
        hits = []
        for item in self.graph.get('contracts', []):
            blob = ' '.join([item.get('name', ''), ' '.join(item.get('scope', [])), ' '.join(item.get('deny', [])), item.get('purpose', '')]).lower()
            if needle in blob:
                hits.append(item)
        return hits

    def impact_report(self, value):
        matches = self.module_candidates(value)
        module_summaries = []
        dependents = {}
        related_contracts = []
        related_sources = []
        for name in matches:
            summary = self.summarize_module(name)
            if summary:
                module_summaries.append(summary)
            dependents[name] = self.direct_dependents(name)
            module_entry = self.index['module_index'].get(name)
            if module_entry:
                related_sources.extend([
                    item for item in self.graph.get('sources_of_truth', [])
                    if module_entry['path'] in {item.get('owned_by', ''), item.get('owned_by', '').replace('.py', '')}
                ])
                related_sources.extend([
                    item for item in self.graph.get('sync_policies', [])
                    if name == item.get('provider', '') or module_entry['path'].find(item.get('provider', '')) >= 0
                ])
            related_contracts.extend(self.contract_hits(name))
            related_contracts.extend(self.contract_hits(module_entry['path'] if module_entry else value))
        return {
            'query': value,
            'matches': module_summaries,
            'used_by': dependents,
            'sources': dedupe_dicts(related_sources),
            'contracts': dedupe_dicts(related_contracts),
        }

    def blast_radius(self, value, depth):
        matches = self.module_candidates(value)
        if not matches:
            return {
                'query': value,
                'depth': depth,
                'matches': [],
                'upstream': [],
                'downstream': [],
                'touchpoints': [],
            }

        upstream = self._reachable(matches, self.index['reverse_imports'], depth)
        downstream = self._reachable(matches, self.index['forward_imports'], depth)
        touched_modules = sorted(set(matches + upstream + downstream))
        touchpoints = [self.summarize_module(name) for name in touched_modules if self.summarize_module(name)]
        sources = []
        sync_policies = []
        contracts = []
        for item in self.graph.get('sources_of_truth', []):
            owned_by = item.get('owned_by', '')
            if any(module in {owned_by, owned_by.replace('.py', '')} for module in touched_modules):
                sources.append(item)
        for item in self.graph.get('sync_policies', []):
            provider = item.get('provider', '')
            if any(provider in module for module in touched_modules) or provider in value:
                sync_policies.append(item)
        for item in self.graph.get('contracts', []):
            blob = ' '.join([item.get('name', ''), ' '.join(item.get('scope', [])), item.get('purpose', '')]).lower()
            if value.lower() in blob or any(module.lower() in blob for module in touched_modules):
                contracts.append(item)
        return {
            'query': value,
            'depth': depth,
            'matches': [self.summarize_module(name) for name in matches],
            'upstream': [self.summarize_module(name) for name in upstream],
            'downstream': [self.summarize_module(name) for name in downstream],
            'touchpoints': touchpoints,
            'sources': dedupe_dicts(sources),
            'sync_policies': dedupe_dicts(sync_policies),
            'contracts': dedupe_dicts(contracts),
        }

    def direct_dependents(self, name):
        return [self.summarize_module(importer) for importer in sorted(set(self.index['reverse_imports'].get(name, [])))]

    def direct_dependencies(self, name):
        return [self.summarize_module(dep) for dep in sorted(set(self.index['forward_imports'].get(name, [])))]

    def path_between(self, source, target):
        source_matches = self.module_candidates(source)
        target_matches = set(self.module_candidates(target))
        if not source_matches or not target_matches:
            return None

        adjacency = defaultdict(list)
        for edge in self.graph.get('import_edges', []):
            adjacency[edge['from']].append((edge['to'], 'imports'))
            adjacency[edge['to']].append((edge['from'], 'imported-by'))

        for seed in source_matches:
            queue = deque([(seed, [])])
            seen = {seed}
            while queue:
                node, path = queue.popleft()
                if node in target_matches:
                    return {
                        'source': seed,
                        'target': node,
                        'steps': path,
                    }
                for neighbor, relation in adjacency.get(node, []):
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    queue.append((neighbor, path + [{'from': node, 'to': neighbor, 'relation': relation}]))
        return None

    def risk_report(self, value):
        matches = self.module_candidates(value)
        reports = []
        for name in matches:
            summary = self.summarize_module(name)
            if not summary:
                continue
            imported_by = len(summary['imported_by'])
            imports = len(summary['imports'])
            contract_hits = self.contract_hits(name) + self.contract_hits(summary['path'])
            source_hits = [
                item for item in self.graph.get('sources_of_truth', [])
                if summary['path'] in {item.get('owned_by', ''), item.get('owned_by', '').replace('.py', '')}
            ]
            score = imported_by * 3 + imports * 2 + len(contract_hits) * 2 + len(source_hits) * 2
            if score >= 10:
                level = 'high'
            elif score >= 5:
                level = 'medium'
            else:
                level = 'low'
            reports.append({
                'target': summary,
                'risk_level': level,
                'risk_score': score,
                'reasons': [
                    f"{imported_by} direct dependents",
                    f"{imports} direct dependencies",
                    f"{len(contract_hits)} contract matches",
                    f"{len(source_hits)} source-of-truth matches",
                ],
                'contracts': contract_hits,
                'sources': source_hits,
            })
        return reports

    def change_plan(self, value):
        matches = self.module_candidates(value)
        plans = []
        for name in matches:
            summary = self.summarize_module(name)
            if not summary:
                continue
            direct_dependents = self.direct_dependents(name)
            direct_dependencies = self.direct_dependencies(name)
            contracts = self.contract_hits(name) + self.contract_hits(summary['path'])
            sources = [
                item for item in self.graph.get('sources_of_truth', [])
                if summary['path'] in {item.get('owned_by', ''), item.get('owned_by', '').replace('.py', '')}
            ]
            syncs = [
                item for item in self.graph.get('sync_policies', [])
                if item.get('provider', '') in name or item.get('provider', '') in summary['path']
            ]
            plans.append({
                'target': summary,
                'step_1': 'Review the target module first.',
                'step_2': 'Check direct dependents and adjacent imports.',
                'step_3': 'Update source-of-truth, sync policy, and contract references if the change touches ownership.',
                'direct_dependents': direct_dependents,
                'direct_dependencies': direct_dependencies,
                'related_sources': sources,
                'related_sync_policies': syncs,
                'related_contracts': dedupe_dicts(contracts),
            })
        return {
            'query': value,
            'matches': [self.summarize_module(name) for name in matches],
            'plan': plans,
        }

    def stable_anchors(self):
        anchors = (self.context or {}).get('stable_anchors', {})
        return {
            'project': (self.context or {}).get('project', self.graph.get('project', {})),
            'navigation_order': (self.context or {}).get('navigation_order', []),
            'hot_modules': anchors.get('hot_modules', []),
            'sources_of_truth': anchors.get('sources_of_truth', []),
            'sync_policies': anchors.get('sync_policies', []),
            'safety_rules': anchors.get('safety_rules', []),
            'current_focus': (self.context or {}).get('current_focus', {}),
            'query_tool': (self.context or {}).get('query_tool', self.graph.get('project', {}).get('query_tool', 'tools/query_architecture.py')),
            'graph_file': (self.context or {}).get('graph_file', 'ARCHITECTURE.json'),
        }

    def _reachable(self, seeds, adjacency, depth):
        seen = set(seeds)
        frontier = set(seeds)
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                for neighbor in adjacency.get(node, []):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        seen.difference_update(seeds)
        return sorted(seen)


def dedupe_dicts(items):
    seen = set()
    deduped = []
    for item in items:
        if not item:
            continue
        key = json.dumps(item, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

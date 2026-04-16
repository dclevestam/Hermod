import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.architecture_lib import ArchitectureGraph


class ArchitectureQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = ROOT / 'tools' / 'generate_project_context.py'
        subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _run(self, *args):
        script = ROOT / 'tools' / 'query_architecture.py'
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return json.loads(proc.stdout)

    def test_module_lookup_returns_dependency_summary(self):
        data = self._run('--module', 'providers.gmail')
        module = data['modules'][0]
        self.assertEqual(module['name'], 'providers.gmail')
        self.assertIn('backends', module['imported_by'])

    def test_used_by_returns_reverse_dependency_summary(self):
        data = self._run('--used-by', 'providers.gmail')
        used_by = data['used_by']['providers.gmail']
        self.assertTrue(any(item and item['name'] == 'backends' for item in used_by))

    def test_ask_unread_routes_to_sync_information(self):
        data = self._run('--ask', 'who owns unread counts?')
        self.assertEqual(data['ask']['topic'], 'unread-counts')
        self.assertTrue(data['ask']['sources_of_truth'])

    def test_impact_returns_related_state(self):
        data = self._run('--impact', 'window_message_list')
        impact = data['impact'][0]
        self.assertEqual(impact['matches'][0]['name'], 'window_message_list')
        self.assertTrue(impact['used_by'])

    def test_blast_radius_expands_transitively(self):
        data = self._run('--blast-radius', 'window_message_list', '--depth', '2')
        radius = data['blast_radius'][0]
        self.assertEqual(radius['matches'][0]['name'], 'window_message_list')
        self.assertTrue(radius['touchpoints'])

    def test_project_context_is_loaded(self):
        data = self._run('--module', 'window')
        self.assertIn('project_context', data)
        self.assertIn('navigation_order', data['project_context'])

    def test_external_graph_and_context_are_supported(self):
        graph = {
            'project': {
                'name': 'DemoMail',
                'root': '/tmp/arch-demo',
                'generated_by': 'tools/generate_architecture.py',
                'query_tool': 'tools/query_architecture.py',
                'context_file': '.codex/project_context.json',
                'purpose': 'demo graph',
            },
            'settings': {
                'light_poll_interval_minutes': 1,
                'full_reconcile_interval_minutes': 5,
                'source_of_truth': 'provider counts',
            },
            'sources_of_truth': [
                {'name': 'Demo API', 'owned_by': 'providers/demo.py', 'truth': ['messages', 'unread'], 'fallback': 'IMAP', 'reconcile': 'API then IMAP'},
            ],
            'sync_policies': [
                {'provider': 'demo', 'primary': 'API', 'fallback': 'IMAP', 'reconcile': 'API then IMAP'},
            ],
            'contracts': [
                {'name': 'providers_are_ui_free', 'scope': ['providers.*'], 'deny': ['window'], 'purpose': 'providers do not touch UI'},
            ],
            'module_index': [
                {'name': 'window', 'path': 'window.py', 'package': False},
                {'name': 'window_message_list', 'path': 'window_message_list.py', 'package': False},
                {'name': 'providers.demo', 'path': 'providers/demo.py', 'package': False},
            ],
            'modules': [
                {'name': 'window.py', 'role': 'Main window', 'owns': ['window state', 'sidebar']},
                {'name': 'window_message_list.py', 'role': 'Message list', 'owns': ['message rows', 'unread badges']},
                {'name': 'providers/demo.py', 'role': 'Demo provider', 'owns': ['primary sync', 'fallback', 'health']},
            ],
            'settings_keys': [],
            'state_flows': [],
            'fallbacks': [],
            'edges': [
                {'from': 'window', 'to': 'window_message_list', 'type': 'renders'},
                {'from': 'window_message_list', 'to': 'providers.demo', 'type': 'refreshes'},
            ],
            'import_edges': [
                {'from': 'window', 'to': 'window_message_list', 'type': 'import'},
                {'from': 'window_message_list', 'to': 'providers.demo', 'type': 'import'},
            ],
            'notes': ['demo graph'],
        }
        context = {
            'project': {'name': 'DemoMail'},
            'graph_file': 'ARCHITECTURE.json',
            'query_tool': 'tools/query_architecture.py',
            'update_tool': 'tools/update_architecture.sh',
            'contracts_tool': 'tools/check_architecture_contracts.py',
            'navigation_order': ['window', 'window_message_list', 'providers.demo'],
            'stable_anchors': {'sources_of_truth': ['Demo API'], 'sync_policies': ['demo'], 'hot_modules': ['window.py'], 'safety_rules': ['provider truth stays in provider code']},
            'current_focus': {'mail_truth': 'provider-backed counts', 'architecture': 'graph + context + query helper', 'sync_policy': 'primary first'},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / '.codex').mkdir()
            (tmpdir / 'ARCHITECTURE.json').write_text(json.dumps(graph), encoding='utf-8')
            (tmpdir / '.codex' / 'project_context.json').write_text(json.dumps(context), encoding='utf-8')
            script = ROOT / 'tools' / 'query_architecture.py'
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    '--graph', str(tmpdir / 'ARCHITECTURE.json'),
                    '--context', str(tmpdir / '.codex' / 'project_context.json'),
                    '--module', 'providers.demo',
                    '--blast-radius', 'window',
                    '--path', 'window', 'providers.demo',
                    '--risk', 'window_message_list',
                    '--stable-anchors',
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            data = json.loads(proc.stdout)
            self.assertEqual(data['project_context']['project']['name'], 'DemoMail')
            self.assertEqual(data['modules'][0]['name'], 'providers.demo')
            self.assertEqual(data['blast_radius'][0]['matches'][0]['name'], 'window')
            self.assertEqual(data['path'][0]['source'], 'window')
            self.assertEqual(data['risk'][0][0]['target']['name'], 'window_message_list')
            self.assertEqual(data['stable_anchors']['project']['name'], 'DemoMail')

    def test_library_can_be_used_in_process(self):
        graph = ArchitectureGraph.from_files(ROOT / 'ARCHITECTURE.json', ROOT / '.codex' / 'project_context.json')
        self.assertEqual(graph.graph['project']['name'], 'Hermod')
        self.assertEqual(graph.path_between('window', 'window_message_list')['source'], 'window')

    def test_stable_anchors_are_exposed(self):
        data = self._run('--stable-anchors')
        anchors = data['stable_anchors']
        self.assertIn('navigation_order', anchors)
        self.assertIn('current_focus', anchors)

    def test_change_plan_suggests_edit_order(self):
        data = self._run('--change-plan', 'window_message_list')
        plan = data['change_plan'][0]['plan'][0]
        self.assertEqual(plan['target']['name'], 'window_message_list')
        self.assertEqual(plan['step_1'], 'Review the target module first.')
        self.assertTrue(plan['direct_dependents'])

    def test_path_returns_connection_steps(self):
        data = self._run('--path', 'window', 'window_message_list')
        path = data['path'][0]
        self.assertEqual(path['source'], 'window')
        self.assertEqual(path['target'], 'window_message_list')
        self.assertTrue(path['steps'])

    def test_risk_reports_score_and_level(self):
        data = self._run('--risk', 'window_message_list')
        risk = data['risk'][0][0]
        self.assertIn(risk['risk_level'], {'low', 'medium', 'high'})
        self.assertIn('risk_score', risk)


if __name__ == '__main__':
    unittest.main()

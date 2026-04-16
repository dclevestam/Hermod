import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ArchitectureGraphTests(unittest.TestCase):
    def test_generate_architecture_produces_valid_graph(self):
        script = ROOT / 'tools' / 'generate_architecture.py'
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            env = dict(os.environ)
            env['PYTHONPATH'] = str(ROOT)
            subprocess.run(
                [sys.executable, str(script)],
                cwd=ROOT,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                [sys.executable, str(script), '--check'],
                cwd=ROOT,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            graph = json.loads((ROOT / 'ARCHITECTURE.json').read_text(encoding='utf-8'))

        self.assertEqual(graph['project']['name'], 'Hermod')
        self.assertEqual(graph['project']['query_tool'], 'tools/query_architecture.py')
        self.assertIn('providers.gmail', {item['name'] for item in graph['module_index']})
        self.assertTrue(
            any(item['name'] == 'accounts.auth' and item['package'] for item in graph['module_index'])
        )
        self.assertIn('providers/gmail.py', {node['name'] for node in graph['modules']})
        self.assertEqual(
            {item['name'] for item in graph['contracts']},
            {
                'providers_are_ui_free',
                'core_is_ui_free',
                'ui_uses_concrete_providers_through_backends',
            },
        )
        self.assertIn('reconcile_interval', {item['key'] for item in graph['settings_keys']})
        self.assertEqual(
            {item['provider'] for item in graph['sync_policies']},
            {'gmail', 'imap'},
        )
        self.assertTrue(all(item['primary'] and item['fallback'] and item['reconcile'] for item in graph['sync_policies']))
        self.assertIn({'from': 'window', 'to': 'styles', 'type': 'import'}, graph['import_edges'])
        self.assertTrue(any(edge['type'] == 'refreshes' for edge in graph['edges']))


if __name__ == '__main__':
    unittest.main()

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContextTests(unittest.TestCase):
    def test_project_context_is_generated(self):
        script = ROOT / 'tools' / 'generate_project_context.py'
        subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        context = json.loads((ROOT / '.codex' / 'project_context.json').read_text(encoding='utf-8'))
        self.assertEqual(context['project']['name'], 'Hermod')
        self.assertIn('navigation_order', context)
        self.assertIn('current_focus', context)


if __name__ == '__main__':
    unittest.main()

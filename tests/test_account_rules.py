import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings


class _Backend:
    def __init__(self, identity, presentation_name, accent_color=''):
        self.identity = identity
        self.presentation_name = presentation_name
        self.account_descriptor = type('Descriptor', (), {
            'metadata': {'accent_color': accent_color},
        })()


class AccountRuleTests(unittest.TestCase):
    def test_unique_alias_appends_suffix(self):
        backends = [
            _Backend('one@example.com', 'Work'),
            _Backend('two@example.com', 'Work (2)'),
        ]
        self.assertEqual(
            settings._unique_alias('Work', backends, ignore_identity='new@example.com'),
            'Work (3)',
        )

    def test_auto_account_color_skips_used_palette(self):
        backends = [
            _Backend('one@example.com', 'One', '#4c7fff'),
            _Backend('two@example.com', 'Two', '#16a085'),
        ]
        self.assertEqual(
            settings._auto_account_color(backends, ignore_identity='new@example.com'),
            '#e67e22',
        )

    def test_contrasting_foreground_prefers_light_on_dark(self):
        self.assertEqual(settings.contrasting_foreground('#111111'), '#ffffff')

    def test_contrasting_foreground_prefers_dark_on_light(self):
        self.assertEqual(settings.contrasting_foreground('#f4f4f4'), '#111111')


if __name__ == '__main__':
    unittest.main()

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import styles


class _CssWidget:
    def __init__(self):
        self.classes = []

    def add_css_class(self, name):
        if name not in self.classes:
            self.classes.append(name)

    def remove_css_class(self, name):
        if name in self.classes:
            self.classes.remove(name)


class StylesTests(unittest.TestCase):
    def test_account_class_for_color_picks_nearest_palette_entry(self):
        self.assertEqual(
            styles.account_class_for_color("#70807a"),
            styles.account_class_for_index(0),
        )

    def test_apply_accent_css_class_replaces_previous_class(self):
        widget = _CssWidget()

        first = styles.apply_accent_css_class(widget, "#70807a")
        second = styles.apply_accent_css_class(widget, "#6b7f93")

        self.assertEqual(first, styles.account_class_for_index(0))
        self.assertEqual(second, styles.account_class_for_index(3))
        self.assertEqual(widget.classes, [styles.account_class_for_index(3)])


if __name__ == "__main__":
    unittest.main()

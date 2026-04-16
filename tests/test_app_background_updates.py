import sys
import unittest
import importlib.util
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location('hermod_main', ROOT / '__main__.py')
hermod_main = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(hermod_main)
HermodApp = hermod_main.HermodApp


class HermodAppBackgroundUpdateTests(unittest.TestCase):
    def test_wake_background_updates_can_force_reconcile(self):
        app = HermodApp.__new__(HermodApp)
        app._next_poll_at = 12.0
        app._force_reconcile = False
        app._poll_wake = mock.Mock()

        with mock.patch('time.monotonic', return_value=10.0):
            HermodApp.wake_background_updates(app, reconcile=True)

        self.assertTrue(app._force_reconcile)
        self.assertEqual(app._next_poll_at, 10.0)
        app._poll_wake.set.assert_called_once_with()

    def test_wake_background_updates_without_reconcile_leaves_flag_off(self):
        app = HermodApp.__new__(HermodApp)
        app._next_poll_at = 12.0
        app._force_reconcile = False
        app._poll_wake = mock.Mock()

        with mock.patch('time.monotonic', return_value=10.0):
            HermodApp.wake_background_updates(app, reconcile=False)

        self.assertFalse(app._force_reconcile)
        self.assertEqual(app._next_poll_at, 10.0)
        app._poll_wake.set.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

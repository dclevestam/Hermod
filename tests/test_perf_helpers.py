import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import utils as utils_module


class PerfHelperTests(unittest.TestCase):
    def test_perf_elapsed_ms_uses_milliseconds(self):
        self.assertEqual(utils_module._perf_elapsed_ms(10.0, 10.125), 125.0)

    def test_perf_elapsed_ms_never_negative(self):
        self.assertEqual(utils_module._perf_elapsed_ms(10.0, 9.5), 0.0)

    def test_perf_message_includes_detail(self):
        self.assertEqual(
            utils_module._perf_message('snapshot load', 'inbox 10 msgs', 12.34),
            'Perf: snapshot load 12.3ms (inbox 10 msgs)',
        )

    def test_log_perf_writes_when_debug_enabled(self):
        fake_settings = mock.Mock()
        fake_settings.get.return_value = True
        stderr = io.StringIO()
        with mock.patch.object(utils_module, 'get_settings', return_value=fake_settings):
            with redirect_stderr(stderr):
                utils_module._log_perf('set messages', '5 msgs', elapsed_ms=7.89)
        self.assertEqual(stderr.getvalue().strip(), 'Perf: set messages 7.9ms (5 msgs)')

    def test_log_perf_is_silent_when_debug_disabled(self):
        fake_settings = mock.Mock()
        fake_settings.get.return_value = False
        stderr = io.StringIO()
        with mock.patch.object(utils_module, 'get_settings', return_value=fake_settings):
            with redirect_stderr(stderr):
                result = utils_module._log_perf('set messages', '5 msgs', elapsed_ms=7.89)
        self.assertIsNone(result)
        self.assertEqual(stderr.getvalue(), '')


if __name__ == '__main__':
    unittest.main()

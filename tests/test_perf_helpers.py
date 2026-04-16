import io
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone, timedelta
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

    def test_format_date_uses_local_time_for_same_day(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertEqual(utils_module._format_date(dt), dt.astimezone().strftime('%H:%M'))

    def test_format_date_uses_month_day_time_for_recent_past(self):
        dt = datetime.now(timezone.utc) - timedelta(days=2)
        self.assertEqual(utils_module._format_date(dt), dt.astimezone().strftime('%b %-d %H:%M'))

    def test_format_date_uses_numeric_date_time_for_old_mail(self):
        dt = datetime.now(timezone.utc) - timedelta(days=400)
        self.assertEqual(utils_module._format_date(dt), dt.astimezone().strftime('%m/%d/%y - %H:%M'))

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

    def test_email_surface_hint_only_acts_on_obvious_light_bg(self):
        html = '<html><body style="background-color: #ffffff; color: #111111;">Hello</body></html>'
        hint = utils_module._email_surface_hint(html, '')
        self.assertIsNotNone(hint)
        self.assertEqual(hint['background_rgb'], (255, 255, 255))
        self.assertEqual(hint['foreground_rgb'], (17, 17, 17))

    def test_email_surface_hint_only_acts_on_obvious_light_bg_and_text(self):
        html = '<html><body style="background-color: #f7f7f7; color: #ffffff;">Hello</body></html>'
        hint = utils_module._email_surface_hint(html, '')
        self.assertIsNotNone(hint)
        self.assertEqual(hint['background_rgb'], (247, 247, 247))
        self.assertEqual(hint['foreground_rgb'], (17, 17, 17))

    def test_email_surface_hint_only_acts_on_obvious_dark_bg_and_text(self):
        html = '<html><body style="background-color: #111111; color: #222222;">Hello</body></html>'
        hint = utils_module._email_surface_hint(html, '')
        self.assertIsNotNone(hint)
        self.assertEqual(hint['background_rgb'], (17, 17, 17))
        self.assertEqual(hint['foreground_rgb'], (255, 255, 255))

    def test_email_surface_hint_ignores_ambiguous_surfaces(self):
        html = '<html><body style="background-color: #557799;">Hello</body></html>'
        self.assertIsNone(utils_module._email_surface_hint(html, ''))


if __name__ == '__main__':
    unittest.main()

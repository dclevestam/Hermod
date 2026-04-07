import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.common import _aware_utc_datetime


class ProviderCommonTests(unittest.TestCase):
    def test_aware_utc_datetime_promotes_naive_values(self):
        naive = datetime(2026, 4, 7, 8, 30)

        aware = _aware_utc_datetime(naive)

        self.assertEqual(aware.tzinfo, timezone.utc)
        self.assertEqual(aware.hour, 8)


if __name__ == '__main__':
    unittest.main()

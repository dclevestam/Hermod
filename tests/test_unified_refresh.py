import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unified_refresh import UnifiedFetchSpec, collect_unified_messages, run_bounded_calls


class _TransientError(RuntimeError):
    pass


def _message(uid, offset_seconds=0):
    return {
        'uid': uid,
        'date': datetime(2026, 4, 7, 8, 30, offset_seconds, tzinfo=timezone.utc),
    }


class UnifiedRefreshTests(unittest.TestCase):
    def test_run_bounded_calls_limits_parallelism_and_keeps_result_order(self):
        first_started = threading.Event()
        second_started = threading.Event()
        third_started = threading.Event()
        release = threading.Event()
        results_holder = {}

        def first():
            first_started.set()
            self.assertTrue(release.wait(1.0))
            return 'first'

        def second():
            second_started.set()
            self.assertTrue(release.wait(1.0))
            return 'second'

        def third():
            third_started.set()
            return 'third'

        def invoke():
            results_holder['value'] = run_bounded_calls([first, second, third], max_workers=2)

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        self.assertTrue(first_started.wait(1.0))
        self.assertTrue(second_started.wait(1.0))
        self.assertFalse(third_started.wait(0.05))
        release.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(
            results_holder['value'],
            [('first', None), ('second', None), ('third', None)],
        )

    def test_collect_unified_messages_keeps_partial_transient_success(self):
        specs = [
            UnifiedFetchSpec(identity='ok@example.com', label='ok', fetch=lambda: [_message('a', 1)]),
            UnifiedFetchSpec(identity='transient@example.com', label='transient', fetch=lambda: (_ for _ in ()).throw(_TransientError('offline'))),
        ]
        logged = []

        result = collect_unified_messages(
            specs,
            transient_error_fn=lambda exc: isinstance(exc, _TransientError),
            network_ready_fn=lambda: True,
            error_logger=lambda label, exc: logged.append((label, str(exc))),
            limit=10,
        )

        self.assertTrue(result['had_transient_error'])
        self.assertEqual([msg['uid'] for msg in result['messages']], ['a'])
        self.assertEqual(logged, [])

    def test_collect_unified_messages_preserves_input_order_for_equal_dates(self):
        shared_date = datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc)

        def fetch(uid, delay):
            def run():
                time.sleep(delay)
                return [{'uid': uid, 'date': shared_date}]
            return run

        specs = [
            UnifiedFetchSpec(identity='first@example.com', label='first', fetch=fetch('first', 0.03)),
            UnifiedFetchSpec(identity='second@example.com', label='second', fetch=fetch('second', 0.01)),
            UnifiedFetchSpec(identity='third@example.com', label='third', fetch=fetch('third', 0.0)),
        ]

        result = collect_unified_messages(
            specs,
            transient_error_fn=lambda exc: False,
            network_ready_fn=lambda: True,
            limit=10,
        )

        self.assertEqual([msg['uid'] for msg in result['messages']], ['first', 'second', 'third'])

    def test_collect_unified_messages_reports_progress_callbacks(self):
        events = []

        def fetch():
            return [_message('a')]

        specs = [
            UnifiedFetchSpec(identity='acct@example.com', label='account', fetch=fetch),
        ]

        result = collect_unified_messages(
            specs,
            transient_error_fn=lambda exc: False,
            network_ready_fn=lambda: True,
            progress_callback=lambda spec, phase, **kwargs: events.append((spec.identity, phase, kwargs)),
            limit=10,
        )

        self.assertEqual([msg['uid'] for msg in result['messages']], ['a'])
        self.assertEqual(events[0][0], 'acct@example.com')
        self.assertEqual(events[0][1], 'checking')
        self.assertEqual(events[-1][1], 'ready')


if __name__ == '__main__':
    unittest.main()

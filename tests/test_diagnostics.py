import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diagnostics import export as export_module
from diagnostics import logger as logger_module
from diagnostics import redact as redact_module


class DiagnosticsRedactionTests(unittest.TestCase):
    def test_redact_text_masks_emails_tokens_and_message_ids(self):
        text = (
            'Bearer abc.def.ghi user@example.com '
            'access_token=secret <message@example.com>'
        )

        redacted = redact_module.redact_text(text)

        self.assertNotIn('user@example.com', redacted)
        self.assertNotIn('abc.def.ghi', redacted)
        self.assertNotIn('secret', redacted)
        self.assertNotIn('<message@example.com>', redacted)
        self.assertIn('Bearer <redacted>', redacted)
        self.assertIn('access_token=<redacted>', redacted)
        self.assertIn('<message-id:redacted>', redacted)

    def test_redact_value_masks_recipient_lists(self):
        value = {
            'to_addrs': [{'email': 'user@example.com'}],
            'cc_addrs': [{'email': 'copy@example.com'}],
        }

        redacted = redact_module.redact_value(value)

        self.assertTrue(str(redacted['to_addrs']).startswith('<redacted:'))
        self.assertTrue(str(redacted['cc_addrs']).startswith('<redacted:'))


class DiagnosticsLoggerTests(unittest.TestCase):
    def test_log_exception_persists_redacted_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diag_dir = Path(tmpdir)
            events_path = diag_dir / 'events.jsonl'
            with mock.patch.object(logger_module, '_DIAGNOSTICS_DIR', diag_dir), \
                 mock.patch.object(logger_module, '_EVENTS_FILE', events_path):
                logger_module.log_exception(
                    'Load failed (user@example.com, inbox, AQMkSecret)',
                    RuntimeError('Bearer token123 <msg@example.com>'),
                )

            raw = events_path.read_text(encoding='utf-8')
            self.assertNotIn('user@example.com', raw)
            self.assertNotIn('token123', raw)
            self.assertNotIn('<msg@example.com>', raw)
            event = json.loads(raw.splitlines()[-1])
            self.assertEqual(event['kind'], 'exception')
            self.assertEqual(event['level'], 'error')

    def test_log_event_does_not_persist_when_diagnostics_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diag_dir = Path(tmpdir)
            events_path = diag_dir / 'events.jsonl'
            with mock.patch.object(logger_module, '_DIAGNOSTICS_DIR', diag_dir), \
                 mock.patch.object(logger_module, '_EVENTS_FILE', events_path), \
                 mock.patch.object(logger_module, 'diagnostics_enabled', return_value=False):
                logger_module.log_event('startup', message='Application started')

            self.assertFalse(events_path.exists())


class DiagnosticsExportTests(unittest.TestCase):
    def test_export_bundle_contains_manifest_and_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diag_dir = Path(tmpdir) / 'diag'
            events_path = diag_dir / 'events.jsonl'
            bundle_path = Path(tmpdir) / 'bundle.zip'
            with mock.patch.object(logger_module, '_DIAGNOSTICS_DIR', diag_dir), \
                 mock.patch.object(logger_module, '_EVENTS_FILE', events_path), \
                 mock.patch.object(export_module, 'recent_perf_events', return_value=[{'kind': 'activate', 'elapsed_ms': 12.3}]), \
                 mock.patch.object(export_module, 'build_health_snapshot', return_value={
                     'python_version': '3.14.3',
                     'settings': {'diagnostics_enabled': True},
                     'account_summary': {'native:gmail': 1},
                 }):
                logger_module.log_event(
                    'startup',
                    message='Application started',
                    context={'backend_count': 2},
                )
                path = export_module.export_diagnostics_bundle(bundle_path)

            self.assertEqual(path, bundle_path)
            with zipfile.ZipFile(bundle_path) as archive:
                self.assertIn('manifest.json', archive.namelist())
                self.assertIn('events.jsonl', archive.namelist())
                manifest = json.loads(archive.read('manifest.json'))
                self.assertIn('python_version', manifest)
                self.assertIn('settings', manifest)
                self.assertEqual(manifest['account_summary'], {'native:gmail': 1})
                self.assertNotIn('cache_dir', manifest)
                self.assertNotIn('config_dir', manifest)
                perf = json.loads(archive.read('perf.json'))
                self.assertEqual(perf[0]['kind'], 'activate')

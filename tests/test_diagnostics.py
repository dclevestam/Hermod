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


class DiagnosticsExportTests(unittest.TestCase):
    def test_export_bundle_contains_manifest_and_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diag_dir = Path(tmpdir) / 'diag'
            events_path = diag_dir / 'events.jsonl'
            bundle_path = Path(tmpdir) / 'bundle.zip'
            with mock.patch.object(logger_module, '_DIAGNOSTICS_DIR', diag_dir), \
                 mock.patch.object(logger_module, '_EVENTS_FILE', events_path):
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
                self.assertNotIn('cache_dir', manifest)
                self.assertNotIn('config_dir', manifest)

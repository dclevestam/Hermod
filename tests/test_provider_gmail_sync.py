import base64
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers.gmail as gmail_module
from accounts.descriptors import AccountDescriptor
from accounts.auth.oauth_common import OAuthTokenAcquisitionError
from providers.common import BodyFetchError, SyncHealthState
from providers.gmail import GmailBackend


def _message(uid, folder='INBOX', gmail_msgid='100'):
    return {
        'uid': uid,
        'subject': '(no subject)',
        'sender_name': 'Tester',
        'sender_email': 'tester@example.com',
        'to_addrs': [],
        'cc_addrs': [],
        'date': datetime.now(timezone.utc),
        'is_read': True,
        'has_attachments': False,
        'snippet': '',
        'folder': folder,
        'backend': 'gmail',
        'account': 'test@example.com',
        'backend_obj': None,
        'thread_id': '',
        'thread_source': 'gmail-imap',
        'message_id': f'<{uid}@example.com>',
        'gmail_msgid': gmail_msgid,
    }


class GmailBackendSyncTests(unittest.TestCase):
    def make_backend(self):
        class _NativeSource:
            def get_account(self):
                return object()

            def get_access_token(self, network_ready_fn=None):
                return 'native-token'

            def invalidate_access_token(self):
                return None

        backend = object.__new__(GmailBackend)
        backend.identity = 'test@gmail.com'
        backend.provider = 'gmail'
        backend.source_obj = _NativeSource()
        backend.account = object()
        backend.account_descriptor = AccountDescriptor(
            source='native',
            provider_kind='gmail',
            identity='test@gmail.com',
            auth_kind='native-oauth2',
            source_obj=backend.source_obj,
        )
        backend._sync_lock = threading.Lock()
        backend._lock = threading.Lock()
        backend._imap = None
        backend._imap_host = 'imap.gmail.com'
        backend._imap_user = 'test@gmail.com'
        backend._imap_use_ssl = True
        backend._imap_use_tls = False
        backend._imap_accept_ssl_errors = False
        backend._allow_imap_fallback = True
        backend._use_gmail_api_send = False
        backend._special_folders = {}
        backend._cached_inbox_messages = []
        backend._inbox_history_id = ''
        backend._folder_sync = {}
        backend._gmail_history_supported = True
        backend._gmail_history_seed_inflight = set()
        backend._gmail_labels_by_name = None
        backend._gmail_labels_loaded_at = ''
        backend._gmail_api_available = True
        backend._cached_token = None
        backend._cached_token_expiry = 0.0
        backend._gmail_last_health_event = None
        backend._gmail_service = None
        backend._gmail_service_lock = threading.Lock()
        backend._gmail_list_memo = {}
        backend._gmail_list_memo_lock = threading.Lock()
        backend._persist_timer = None
        backend._persist_timer_lock = threading.Lock()
        return backend

    def test_gmail_label_bridge_handles_localized_special_folders(self):
        backend = self.make_backend()
        backend._special_folders = {'[Gmail]/Sent Mail': '[Google Mail]/Gesendet'}
        backend._gmail_labels_by_name = {
            'Projects/Foo': {'id': 'Label_1', 'type': 'user'},
            'Ärende': {'id': 'Label_2', 'type': 'user'},
        }

        self.assertEqual(backend.gmail_label_for_folder('INBOX')['id'], 'INBOX')
        self.assertEqual(backend.gmail_label_for_folder('[Gmail]/Sent Mail')['id'], 'SENT')
        self.assertEqual(backend.gmail_label_for_folder('[Google Mail]/Gesendet')['id'], 'SENT')
        self.assertEqual(backend.gmail_label_for_folder('Projects/Foo')['id'], 'Label_1')
        self.assertEqual(backend.gmail_label_for_folder('&AMQ-rende')['id'], 'Label_2')
        self.assertIsNone(backend.gmail_label_for_folder('[Gmail]/All Mail'))

    def test_gmail_fetch_messages_reuses_cached_sent_folder_when_unchanged(self):
        backend = self.make_backend()
        cached = [_message('1', folder='[Gmail]/Sent Mail', gmail_msgid='500')]
        backend._folder_sync['[Gmail]/Sent Mail'] = {
            'messages': cached,
            'history_id': 'history-1',
        }
        probe_calls = []
        backend._probe_cached_folder_messages = lambda folder, label_id: (
            probe_calls.append((folder, label_id)) or {'status': 'unchanged', 'messages': cached}
        )
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')

        messages = backend.fetch_messages('[Gmail]/Sent Mail', limit=10)

        self.assertEqual(messages, cached)
        self.assertEqual(probe_calls, [('[Gmail]/Sent Mail', 'SENT')])

    def test_gmail_fetch_messages_reuses_changed_sent_folder_refresh(self):
        backend = self.make_backend()
        refreshed = [_message('2', folder='[Gmail]/Sent Mail', gmail_msgid='600')]
        probe_calls = []
        refresh_calls = []
        backend._probe_cached_folder_messages = lambda folder, label_id: (
            probe_calls.append((folder, label_id)) or {
                'status': 'changed',
                'history_id': 'history-2',
                'refresh_map': {'600': 'api-600'},
                'remove_ids': set(),
            }
        )
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: (
            refresh_calls.append((folder, history_probe['history_id'], limit)) or refreshed
        )
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')

        messages = backend.fetch_messages('[Gmail]/Sent Mail', limit=10)

        self.assertEqual(messages, refreshed)
        self.assertEqual(probe_calls, [('[Gmail]/Sent Mail', 'SENT')])
        self.assertEqual(refresh_calls, [('[Gmail]/Sent Mail', 'history-2', 10)])

    def test_gmail_fetch_messages_large_limit_bypasses_cached_partial_sync(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: self.fail('cached probe should not run')
        backend._refresh_cached_folder_messages = lambda *args, **kwargs: self.fail('cached refresh should not run')
        fetched = [_message(str(idx), folder='INBOX', gmail_msgid=str(idx)) for idx in range(150)]
        backend._gmail_api_fetch_messages = lambda folder, limit: fetched
        backend._ensure_gmail_history_seed_async = lambda folder: None

        messages = backend.fetch_messages('INBOX', limit=150)

        self.assertEqual(len(messages), 150)
        self.assertEqual([msg['uid'] for msg in messages], [str(idx) for idx in range(150)])

    def test_gmail_get_cached_messages_returns_cached_folder_messages(self):
        backend = self.make_backend()
        cached = [_message('1', folder='INBOX', gmail_msgid='100')]
        backend._cached_inbox_messages = cached

        messages = backend.get_cached_messages('INBOX', limit=10)

        self.assertEqual(messages, cached)

    def test_gmail_fetch_messages_uses_live_api_when_history_is_missing(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('cached', folder='INBOX', gmail_msgid='100')]
        backend._probe_cached_folder_messages = lambda *args, **kwargs: None
        seed_calls = []
        update_calls = []
        backend._ensure_gmail_history_seed_async = lambda folder: seed_calls.append(folder)
        backend._gmail_api_fetch_messages = lambda folder, limit: [_message('fresh', folder='INBOX', gmail_msgid='200')]
        backend._update_folder_sync_state = lambda folder, messages=None, history_id=None: update_calls.append(
            (folder, [msg['uid'] for msg in (messages or [])], history_id)
        )

        messages = backend.fetch_messages('INBOX', limit=10)

        self.assertEqual([msg['uid'] for msg in messages], ['fresh'])
        self.assertEqual(seed_calls, ['INBOX'])
        self.assertEqual(update_calls, [('INBOX', ['fresh'], None)])

    def test_gmail_fetch_thread_messages_prefers_api_when_available(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('7', folder='INBOX', gmail_msgid='26')]
        backend._gmail_api_thread_resource = lambda thread_id: {
            'messages': [
                {
                    'id': '1a',
                    'threadId': '2b',
                    'labelIds': ['INBOX'],
                    'snippet': 'hello',
                    'internalDate': '1700000000000',
                    'payload': {
                        'mimeType': 'text/plain',
                        'headers': [
                            {'name': 'From', 'value': 'Tester <tester@example.com>'},
                            {'name': 'To', 'value': 'You <you@example.com>'},
                            {'name': 'Subject', 'value': 'Thread subject'},
                            {'name': 'Date', 'value': 'Tue, 07 Apr 2026 08:30:00 +0000'},
                            {'name': 'Message-ID', 'value': '<api@example.com>'},
                        ],
                        'body': {
                            'data': base64.urlsafe_b64encode(b'hello').decode().rstrip('='),
                        },
                    },
                }
            ]
        }
        backend._get_imap = lambda: self.fail('IMAP fetch should not run')

        messages = backend.fetch_thread_messages('43')

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['uid'], '7')
        self.assertEqual(messages[0]['thread_id'], '43')
        self.assertEqual(messages[0]['thread_source'], 'gmail-api')
        self.assertEqual(messages[0]['subject'], 'Thread subject')

    def test_gmail_fetch_body_uses_api_when_cached_message_can_be_resolved(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('7', folder='INBOX', gmail_msgid='26')]
        backend._gmail_api_message_resource = lambda api_id: {
            'id': api_id,
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'From', 'value': 'Tester <tester@example.com>'},
                    {'name': 'Subject', 'value': 'Body subject'},
                    {'name': 'Date', 'value': 'Tue, 07 Apr 2026 08:30:00 +0000'},
                ],
                'body': {
                    'data': base64.urlsafe_b64encode(b'body text').decode().rstrip('='),
                },
            },
        }
        backend._get_imap = lambda: self.fail('IMAP fetch should not run')

        html, text, attachments = backend.fetch_body('7', 'INBOX')

        self.assertIsNone(html)
        self.assertEqual(text, 'body text')
        self.assertEqual(attachments, [])

    def test_gmail_fetch_messages_uses_api_list_when_available(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: None
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')
        seed_calls = []
        update_calls = []

        def api_request(path, query=None, method='GET', data=None):
            if path == '/users/me/messages' and not query.get('pageToken'):
                return {
                    'messages': [
                        {'id': '1b', 'threadId': '2b'},
                        {'id': '1a', 'threadId': '2a'},
                    ],
                    'nextPageToken': None,
                }
            if path == '/users/me/messages/1b':
                return {
                    'id': '1b',
                    'threadId': '2b',
                    'labelIds': ['INBOX'],
                    'snippet': 'second',
                    'internalDate': '1700000001000',
                    'payload': {
                        'mimeType': 'text/plain',
                        'headers': [
                            {'name': 'From', 'value': 'Tester <tester@example.com>'},
                            {'name': 'Subject', 'value': 'Second'},
                            {'name': 'Date', 'value': 'Tue, 07 Apr 2026 08:31:00 +0000'},
                            {'name': 'Message-ID', 'value': '<second@example.com>'},
                        ],
                    },
                }
            if path == '/users/me/messages/1a':
                return {
                    'id': '1a',
                    'threadId': '2a',
                    'labelIds': ['INBOX'],
                    'snippet': 'first',
                    'internalDate': '1700000000000',
                    'payload': {
                        'mimeType': 'text/plain',
                        'headers': [
                            {'name': 'From', 'value': 'Tester <tester@example.com>'},
                            {'name': 'Subject', 'value': 'First'},
                            {'name': 'Date', 'value': 'Tue, 07 Apr 2026 08:30:00 +0000'},
                            {'name': 'Message-ID', 'value': '<first@example.com>'},
                        ],
                    },
                }
            self.fail(f'unexpected api path {path}')

        backend._gmail_api_request = api_request
        backend._gmail_batch_get_metadata = lambda ids: {
            aid: api_request(f'/users/me/messages/{aid}') for aid in ids
        }
        backend._update_folder_sync_state = lambda folder, messages=None, history_id=None: update_calls.append(
            (folder, [msg['uid'] for msg in (messages or [])], history_id)
        )
        backend._ensure_gmail_history_seed_async = lambda folder: seed_calls.append(folder)

        messages = backend.fetch_messages('INBOX', limit=10)

        self.assertEqual([msg['uid'] for msg in messages], ['1b', '1a'])
        self.assertEqual([msg['subject'] for msg in messages], ['Second', 'First'])
        self.assertEqual(seed_calls, ['INBOX'])
        self.assertEqual(update_calls, [('INBOX', ['1b', '1a'], None)])

    def test_gmail_imap_fetch_header_messages_parses_raw_fetch_rows(self):
        backend = self.make_backend()

        class _FakeIMAP:
            def select(self, folder_name, readonly=True):
                self.select_args = (folder_name, readonly)
                return 'OK', [b'']

            def uid(self, command, uid_set, query):
                self.uid_args = (command, uid_set, query)
                raw = (
                    b'From: Tester <tester@example.com>\r\n'
                    b'To: You <you@example.com>\r\n'
                    b'Subject: Hello\r\n'
                    b'Date: Tue, 07 Apr 2026 08:30:00 +0000\r\n'
                    b'Message-ID: <msg@example.com>\r\n'
                    b'Content-Type: text/plain; charset="utf-8"\r\n'
                    b'\r\n'
                )
                meta = b'123 (UID 123 FLAGS (' + b'\\Seen' + b') X-GM-MSGID 555 BODY[HEADER] {0})'.replace(
                    b'{0}', str(len(raw)).encode('ascii')
                )
                return 'OK', [(meta, raw)]

            def logout(self):
                return 'BYE', [b'logged out']

        fake_imap = _FakeIMAP()

        class _Session:
            def __enter__(self):
                return fake_imap

            def __exit__(self, exc_type, exc, tb):
                return False

        backend._gmail_imap_session = lambda: _Session()

        rows = backend._gmail_imap_fetch_header_messages('INBOX', ['123'])

        self.assertEqual(fake_imap.select_args, ('INBOX', True))
        self.assertEqual(fake_imap.uid_args, ('fetch', '123', '(UID FLAGS X-GM-MSGID BODY.PEEK[HEADER])'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['uid'], '123')
        self.assertEqual(rows[0]['subject'], 'Hello')
        self.assertFalse(rows[0]['is_read'])
        self.assertEqual(rows[0]['gmail_msgid'], '555')

    def test_gmail_fetch_messages_falls_back_to_imap_when_api_is_forbidden(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: None
        backend._gmail_api_fetch_messages = lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError('https://gmail.googleapis.com', 403, 'Forbidden', None, None)
        )
        backend._gmail_imap_fetch_messages = lambda folder, limit: [
            _message('imap-1', folder='INBOX', gmail_msgid='')
        ]
        update_calls = []
        backend._update_folder_sync_state = lambda folder, messages=None, history_id=None: update_calls.append(
            (folder, [msg['uid'] for msg in (messages or [])], history_id)
        )

        messages = backend.fetch_messages('INBOX', limit=10)

        self.assertEqual([msg['uid'] for msg in messages], ['imap-1'])
        self.assertEqual(update_calls, [('INBOX', ['imap-1'], None)])
        self.assertIsNotNone(backend.get_sync_health())
        self.assertEqual(backend.get_sync_health()['state'], 'warning')

    def test_gmail_fetch_messages_records_imap_fallback_notice(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: None
        backend._gmail_api_fetch_messages = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('api down'))
        backend._gmail_imap_fetch_messages = lambda folder, limit: [_message('imap-1', folder='INBOX', gmail_msgid='')]

        messages = backend.fetch_messages('INBOX', limit=10)
        notice = backend.consume_sync_notice()

        self.assertEqual([msg['uid'] for msg in messages], ['imap-1'])
        self.assertIsNotNone(notice)
        self.assertEqual(notice['kind'], 'error')
        self.assertEqual(notice['detail'], 'Could not load Inbox')
        self.assertIsNotNone(backend.get_sync_health())
        self.assertEqual(backend.get_sync_health()['state'], 'warning')
        self.assertTrue(backend.get_sync_health()['retryable'])
        self.assertIn('Gmail API unavailable', backend.get_sync_health()['detail'])

    def test_gmail_imap_background_updates_detect_read_state_changes_without_uid_changes(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('imap-1', folder='INBOX', gmail_msgid='')]
        backend._cached_inbox_messages[0]['is_read'] = True
        backend._folder_sync['INBOX'] = {
            'messages': backend._cached_inbox_messages,
            'history_id': '',
        }
        backend._gmail_imap_fetch_messages = lambda folder, limit: [
            _message('imap-1', folder='INBOX', gmail_msgid='') | {'is_read': False}
        ]
        backend.get_unread_count = lambda folder, force_primary=False: 1
        backend.consume_sync_notices = lambda: []

        result = backend._gmail_imap_check_background_updates(['INBOX'])

        self.assertIn('INBOX', result['changed_folders'])
        self.assertEqual(result['counts']['inbox'], 1)

    def test_gmail_notice_includes_oauth_token_failure_reason(self):
        backend = self.make_backend()
        exc = OAuthTokenAcquisitionError(
            'Google OAuth token failed during access_token: expected status 200 when requesting access token',
            stage='access_token',
            retryable=True,
            source='google',
        )

        notice = backend._gmail_notice_for_exception(exc, 'INBOX')

        self.assertEqual(notice['kind'], 'warning')
        self.assertEqual(notice['detail'], 'Sign-in needs attention for Inbox')
        self.assertIn('Google OAuth token failed', notice['context']['reason'])
        self.assertEqual(notice['context']['stage'], 'access_token')

    def test_gmail_fetch_messages_retries_primary_after_fallback_window(self):
        backend = self.make_backend()
        backend._probe_cached_folder_messages = lambda *args, **kwargs: None
        backend._fetch_messages_imap = lambda *args, **kwargs: self.fail('IMAP fetch should not run')
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account=backend.identity,
            route='fallback',
            state='warning',
            detail='Using IMAP for Inbox',
            tooltip='Using IMAP for Inbox',
            retryable=True,
            retry_after_at=0,
            retry_after_seconds=0,
            primary_label='Gmail API',
            fallback_label='IMAP',
        )
        fetched = [_message('1', folder='INBOX', gmail_msgid='1')]
        backend._gmail_api_fetch_messages = lambda folder, limit: fetched

        messages = backend.fetch_messages('INBOX', limit=10)

        self.assertEqual(messages, fetched)
        self.assertIsNone(backend.get_sync_health())

    def test_gmail_force_primary_probe_clears_retry_gate(self):
        backend = self.make_backend()
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account=backend.identity,
            route='fallback',
            state='warning',
            detail='Gmail API unavailable for Inbox',
            tooltip='Reading through IMAP for now.',
            retryable=False,
            retry_after_at=999999.0,
            retry_after_seconds=999,
            primary_label='Gmail API',
            fallback_label='IMAP',
        )

        health = backend.force_primary_probe()

        self.assertEqual(health.retry_after_at, 0.0)
        self.assertEqual(health.retry_after_seconds, 0)
        self.assertTrue(health.retryable)
        self.assertEqual(backend._gmail_api_available, None)

    def test_gmail_fetch_all_folders_uses_api_labels_when_available(self):
        backend = self.make_backend()
        backend._gmail_labels = lambda force=False: {
            'Projects/Foo': {'id': 'Label_1', 'type': 'user'},
            'INBOX': {'id': 'INBOX', 'type': 'system'},
            'Sent': {'id': 'SENT', 'type': 'system'},
        }
        backend._get_imap = lambda: self.fail('IMAP folder discovery should not run')

        folders = backend.fetch_all_folders()

        self.assertEqual(folders, [('Projects/Foo', 'Projects/Foo', 'folder-symbolic')])

    def test_gmail_labels_seed_special_folder_mappings(self):
        backend = self.make_backend()
        backend._gmail_labels_by_name = {
            'INBOX': {'id': 'INBOX', 'type': 'system'},
            'Gesendet': {'id': 'SENT', 'type': 'system'},
            'Papperskorgen': {'id': 'TRASH', 'type': 'system'},
        }
        backend._seed_special_folders_from_labels(backend._gmail_labels_by_name)

        self.assertEqual(backend._special_folders['INBOX'], 'INBOX')
        self.assertEqual(backend._special_folders['[Gmail]/Sent Mail'], 'Gesendet')
        self.assertEqual(backend._special_folders['[Gmail]/Trash'], 'Papperskorgen')

    def test_gmail_refresh_metadata_uses_cached_uid_without_imap(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('99', folder='INBOX', gmail_msgid='26')]
        backend._gmail_message_metadata = lambda api_id: {
            'id': api_id,
            'labelIds': ['INBOX'],
            'snippet': 'updated',
            'internalDate': '1700000000000',
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'Tester <tester@example.com>'},
                    {'name': 'Subject', 'value': 'Updated'},
                    {'name': 'Date', 'value': 'Tue, 07 Apr 2026 08:30:00 +0000'},
                    {'name': 'Message-ID', 'value': '<updated@example.com>'},
                ],
            },
        }
        backend._gmail_batch_get_metadata = lambda ids: {
            aid: backend._gmail_message_metadata(aid) for aid in ids
        }
        backend._get_imap = lambda: self.fail('IMAP lookup should not run')

        refreshed = backend._fetch_gmail_metadata_messages({'26': '1a'}, 'INBOX')

        self.assertEqual(refreshed['26']['uid'], '99')
        self.assertEqual(refreshed['26']['thread_source'], 'gmail-api')

    def test_gmail_top_up_cached_folder_messages_prefers_api_list(self):
        backend = self.make_backend()
        current = [_message('7', folder='INBOX', gmail_msgid='26')]
        backend._gmail_api_fetch_messages = lambda folder, limit: [
            _message('8', folder='INBOX', gmail_msgid='27'),
            _message('7', folder='INBOX', gmail_msgid='26'),
        ]
        backend._get_imap = lambda: self.fail('IMAP top-up should not run')

        topped = backend._top_up_cached_folder_messages('INBOX', current, 2)

        self.assertEqual([msg['uid'] for msg in topped], ['8', '7'])

    def test_gmail_flag_and_delete_ops_use_api_when_resolved(self):
        backend = self.make_backend()
        backend._cached_inbox_messages = [_message('7', folder='INBOX', gmail_msgid='26')]
        modify_calls = []
        delete_calls = []
        backend._gmail_api_modify_message = lambda api_id, add_label_ids=None, remove_label_ids=None: modify_calls.append(
            (api_id, tuple(add_label_ids or []), tuple(remove_label_ids or []))
        )
        backend._gmail_api_request = lambda path, method='GET', data=None: delete_calls.append((path, method, data)) or {}

        backend.mark_as_read('7', 'INBOX')
        backend.mark_as_unread('7', 'INBOX')
        backend.delete_message('7', 'INBOX')

        self.assertEqual(modify_calls, [
            ('1a', (), ('UNREAD',)),
            ('1a', ('UNREAD',), ()),
        ])
        self.assertEqual(delete_calls, [('/users/me/messages/1a/trash', 'POST', None)])

    def test_gmail_unread_count_uses_api_label_count_when_available(self):
        backend = self.make_backend()
        backend._gmail_api_label_count = lambda label_id: 17 if label_id == 'INBOX' else 0
        backend._get_imap = lambda: self.fail('IMAP unread count should not run')

        self.assertEqual(backend.get_unread_count('INBOX'), 17)

    def test_gmail_unread_count_falls_back_to_imap_when_api_is_forbidden(self):
        backend = self.make_backend()
        backend._gmail_api_label_count = lambda label_id: (_ for _ in ()).throw(
            urllib.error.HTTPError('https://gmail.googleapis.com', 403, 'Forbidden', None, None)
        )
        backend._gmail_imap_unread_count = lambda folder='INBOX': 7

        self.assertEqual(backend.get_unread_count('INBOX'), 7)
        self.assertFalse(backend._gmail_api_available)
        self.assertEqual(backend.get_sync_health()['route'], 'primary')

    def test_gmail_token_is_cached_until_invalidated(self):
        backend = self.make_backend()
        calls = []

        def fake_token(network_ready_fn=None):
            calls.append(True)
            return f'token-{len(calls)}'

        backend.source_obj = mock.Mock(
            get_access_token=mock.Mock(side_effect=fake_token),
            invalidate_access_token=mock.Mock(),
        )
        with mock.patch.object(backend, 'source_obj', backend.source_obj):
            first = backend._token()
            second = backend._token()
            backend._invalidate_token()
            third = backend._token()

        self.assertEqual(first, 'token-1')
        self.assertEqual(second, 'token-1')
        self.assertEqual(third, 'token-2')
        self.assertEqual(len(calls), 2)
        backend.source_obj.invalidate_access_token.assert_called_once()

    def test_gmail_token_uses_native_oauth_source_for_native_accounts(self):
        backend = self.make_backend()
        invalidations = []

        class _NativeSource:
            def get_account(self):
                return object()

            def get_access_token(self, network_ready_fn=None):
                return 'native-token'

            def invalidate_access_token(self):
                invalidations.append(True)

        backend.source_obj = _NativeSource()
        backend.account_descriptor = AccountDescriptor(
            source='native',
            provider_kind='gmail',
            identity='test@gmail.com',
            auth_kind='native-oauth2',
            source_obj=backend.source_obj,
        )

        self.assertEqual(backend._token(), 'native-token')
        backend._invalidate_token()
        self.assertEqual(invalidations, [True])

    def test_gmail_api_only_policy_stays_primary_when_degraded(self):
        backend = self.make_backend()
        backend._imap_host = ''
        backend._allow_imap_fallback = False
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account='test@gmail.com',
            route='primary',
            state='warning',
            detail='Sign-in needs attention',
            retryable=True,
        )

        policy = backend.get_unread_count_policy('INBOX')

        self.assertEqual(policy['route'], 'primary')
        self.assertEqual(policy['fallback'], 'No fallback')

    def test_gmail_send_message_uses_api_for_native_api_only_accounts(self):
        backend = self.make_backend()
        backend._imap_host = ''
        backend._allow_imap_fallback = False
        backend._use_gmail_api_send = True
        backend.source_obj = mock.Mock(get_access_token=mock.Mock(return_value='native-token'))
        backend.account_descriptor = AccountDescriptor(
            source='native',
            provider_kind='gmail',
            identity='test@gmail.com',
            auth_kind='native-oauth2',
            source_obj=backend.source_obj,
        )
        calls = []
        backend._gmail_api_request = lambda path, query=None, method='GET', data=None, headers=None: calls.append((path, method, data)) or {}

        backend.send_message('you@example.com', 'Subject', 'Body text')

        self.assertEqual(len(calls), 1)
        path, method, data = calls[0]
        self.assertEqual(path, '/users/me/messages/send')
        self.assertEqual(method, 'POST')
        self.assertIn('raw', data)

    def test_gmail_api_only_unread_count_returns_zero_when_api_fails(self):
        backend = self.make_backend()
        backend._imap_host = ''
        backend._allow_imap_fallback = False
        backend._gmail_api_label_count = lambda label_id: (_ for _ in ()).throw(
            urllib.error.HTTPError('https://gmail.googleapis.com', 403, 'Forbidden', None, None)
        )

        count = backend.get_unread_count('INBOX')

        self.assertEqual(count, 0)
        self.assertEqual(backend.get_sync_health()['route'], 'primary')

    def test_gmail_apply_history_actions_tracks_folder_membership_and_unread(self):
        backend = self.make_backend()
        actions = {}
        entry = {
            'labelsAdded': [
                {'message': {'id': '0xA'}, 'labelIds': ['UNREAD']},
                {'message': {'id': '0xB'}, 'labelIds': ['SENT']},
            ],
            'labelsRemoved': [
                {'message': {'id': '0xC'}, 'labelIds': ['UNREAD']},
                {'message': {'id': '0xD'}, 'labelIds': ['SENT']},
            ],
            'messagesAdded': [
                {'message': {'id': '0xE'}, 'labelIds': ['SENT']},
            ],
            'messagesDeleted': [
                {'message': {'id': '0xF'}, 'labelIds': ['SENT']},
            ],
        }

        backend._apply_history_actions(actions, entry, label_id='SENT')

        self.assertEqual(actions['10']['action'], 'refresh')
        self.assertEqual(actions['11']['action'], 'refresh')
        self.assertEqual(actions['12']['action'], 'refresh')
        self.assertEqual(actions['13']['action'], 'remove')
        self.assertEqual(actions['14']['action'], 'refresh')
        self.assertEqual(actions['15']['action'], 'remove')

    def test_gmail_non_inbox_sync_state_persists(self):
        backend = self.make_backend()
        sent_messages = [_message('42', folder='[Gmail]/Sent Mail', gmail_msgid='900')]

        with mock.patch.object(gmail_module, 'set_account_state') as set_state:
            backend._update_folder_sync_state('[Gmail]/Sent Mail', messages=sent_messages, history_id='history-sent')
            backend._persist_sync_state(immediate=True)

        provider, identity, state = set_state.call_args.args
        self.assertEqual(provider, 'gmail')
        self.assertEqual(identity, 'test@gmail.com')
        self.assertEqual(state['folders']['[Gmail]/Sent Mail']['history_id'], 'history-sent')
        self.assertEqual(state['folders']['[Gmail]/Sent Mail']['messages'][0]['uid'], '42')

    def test_gmail_deserialize_sync_messages_normalizes_naive_dates(self):
        backend = self.make_backend()

        restored = backend._deserialize_sync_messages([
            {
                'uid': '42',
                'subject': 'Subject',
                'sender_name': 'Tester',
                'sender_email': 'tester@example.com',
                'to_addrs': [],
                'cc_addrs': [],
                'date': '2026-04-07T08:30:00',
                'is_read': True,
                'has_attachments': False,
                'snippet': '',
                'folder': 'INBOX',
                'thread_id': '',
                'thread_source': 'gmail-imap',
                'message_id': '<42@example.com>',
                'gmail_msgid': '42',
            }
        ])

        self.assertEqual(restored[0]['date'].tzinfo, timezone.utc)

    def test_gmail_background_updates_report_new_inbox_messages(self):
        backend = self.make_backend()
        existing = _message('1', folder='INBOX', gmail_msgid='100')
        backend._cached_inbox_messages = [existing]
        backend._inbox_history_id = 'history-1'
        new_message = _message('2', folder='INBOX', gmail_msgid='200')
        new_message['is_read'] = False
        backend._probe_cached_folder_messages = lambda folder, label_id: {
            'status': 'changed',
            'history_id': 'history-2',
            'refresh_map': {'100': 'api-100', '200': 'api-200'},
            'remove_ids': set(),
            'new_ids': {'200'},
        } if folder == 'INBOX' else None
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: [existing, new_message]
        backend.get_unread_count = mock.Mock(side_effect=lambda folder='INBOX', **kwargs: 4 if folder == 'INBOX' else 0)

        result = backend.check_background_updates(tracked_folders=['INBOX'], reconcile_counts=False)

        self.assertEqual(result['changed_folders'], {'INBOX'})
        self.assertEqual([msg['gmail_msgid'] for msg in result['new_messages']], ['200'])
        self.assertEqual(result['counts'], {'inbox': 4})

    def test_gmail_background_updates_do_not_notify_unread_toggles(self):
        backend = self.make_backend()
        existing = _message('1', folder='INBOX', gmail_msgid='100')
        backend._cached_inbox_messages = [existing]
        backend._inbox_history_id = 'history-1'
        toggled = dict(existing)
        toggled['is_read'] = False
        backend._probe_cached_folder_messages = lambda folder, label_id: {
            'status': 'changed',
            'history_id': 'history-2',
            'refresh_map': {'100': 'api-100'},
            'remove_ids': set(),
            'new_ids': set(),
        } if folder == 'INBOX' else None
        backend._refresh_cached_folder_messages = lambda folder, history_probe, limit: [toggled]
        backend.get_unread_count = mock.Mock(side_effect=lambda folder='INBOX', **kwargs: 1 if folder == 'INBOX' else 0)

        result = backend.check_background_updates(tracked_folders=['INBOX'], reconcile_counts=False)

        self.assertEqual(result['changed_folders'], {'INBOX'})
        self.assertEqual(result['new_messages'], [])
        self.assertEqual(result['counts'], {'inbox': 1})

    def test_gmail_unread_count_uses_imap_when_health_is_degraded(self):
        backend = self.make_backend()
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account='test@gmail.com',
            route='primary',
            state='warning',
            detail='Gmail API unavailable for Inbox',
            retryable=True,
        )
        backend._gmail_api_label_id_for_folder = lambda folder: 'INBOX'
        backend._gmail_api_label_count = mock.Mock(return_value=99)
        backend._gmail_imap_unread_count = mock.Mock(return_value=7)

        count = backend.get_unread_count('INBOX')

        self.assertEqual(count, 7)
        backend._gmail_api_label_count.assert_not_called()
        backend._gmail_imap_unread_count.assert_called_once_with('INBOX')

    def test_gmail_unread_count_forced_reconcile_still_probes_api(self):
        backend = self.make_backend()
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account='test@gmail.com',
            route='primary',
            state='warning',
            detail='Gmail API unavailable for Inbox',
            retryable=True,
        )
        backend._gmail_api_label_id_for_folder = lambda folder: 'INBOX'
        backend._gmail_api_label_count = mock.Mock(return_value=11)
        backend._gmail_imap_unread_count = mock.Mock(return_value=7)

        count = backend.get_unread_count('INBOX', force_primary=True)

        self.assertEqual(count, 11)
        backend._gmail_api_label_count.assert_called_once_with('INBOX')
        backend._gmail_imap_unread_count.assert_not_called()

    def test_gmail_unread_count_policy_reflects_fallback_state(self):
        backend = self.make_backend()
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account='test@gmail.com',
            route='fallback',
            state='warning',
            detail='Gmail API unavailable for Inbox',
            retryable=True,
        )

        policy = backend.get_unread_count_policy('INBOX')

        self.assertEqual(policy['route'], 'fallback')
        self.assertEqual(policy['source'], 'imap-unseen')

    def test_gmail_unread_count_policy_forced_reconcile_prefers_primary(self):
        backend = self.make_backend()
        backend._sync_health = SyncHealthState(
            provider='gmail',
            account='test@gmail.com',
            route='fallback',
            state='warning',
            detail='Gmail API unavailable for Inbox',
            retryable=True,
        )

        policy = backend.get_unread_count_policy('INBOX', force_primary=True)

        self.assertEqual(policy['route'], 'primary')
        self.assertEqual(policy['source'], 'gmail-api-label-count')

    def test_gmail_fetch_body_raises_body_fetch_error_when_imap_body_is_missing(self):
        backend = self.make_backend()
        backend._gmail_probe_api_now = lambda: False

        class _FakeIMAP:
            def select(self, folder_name, readonly=True):
                return 'OK', [b'']

            def uid(self, command, uid_value, query):
                return 'OK', [b')']

            def logout(self):
                return 'BYE', [b'logged out']

        fake_imap = _FakeIMAP()

        class _Session:
            def __enter__(self):
                return fake_imap

            def __exit__(self, exc_type, exc, tb):
                return False

        backend._gmail_imap_session = lambda: _Session()

        with self.assertRaises(BodyFetchError):
            backend.fetch_body('7', 'INBOX')


if __name__ == '__main__':
    unittest.main()

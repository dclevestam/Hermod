import sys
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers.imap_smtp as imap_module
from accounts.descriptors import AccountDescriptor
from providers.common import BodyFetchError
from providers.imap_smtp import IMAPSMTPBackend


def _header_bytes(subject, sender, recipient, message_id, date_text, content_type='text/plain; charset=utf-8'):
    return (
        f'From: {sender}\r\n'
        f'To: {recipient}\r\n'
        f'Subject: {subject}\r\n'
        f'Date: {date_text}\r\n'
        f'Message-ID: {message_id}\r\n'
        f'Content-Type: {content_type}\r\n'
        '\r\n'
    ).encode('utf-8')


class _FakeIMAP:
    def __init__(self, search_uids, header_map, body_bytes=None, capabilities=b'IMAP4rev1 STARTTLS'):
        self.search_uids = search_uids
        self.header_map = header_map
        self.body_bytes = body_bytes or b''
        self.capabilities = capabilities
        self.select_calls = []
        self.uid_calls = []
        self.logged_out = False
        self.logged_in = False
        self.starttls_calls = 0

    def select(self, folder, readonly=False):
        self.select_calls.append((folder, readonly))
        return 'OK', [b'']

    def capability(self):
        return 'OK', [self.capabilities]

    def starttls(self, ssl_context=None):
        self.starttls_calls += 1
        return 'OK', [b'']

    def login(self, user, password):
        self.logged_in = True
        self.login_args = (user, password)

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if command == 'search' and args == (None, 'ALL'):
            return 'OK', [self.search_uids]
        if command == 'search' and args[:2] == (None, 'UNSEEN'):
            unseen = b' '.join(uid for uid, is_unread in self.header_map.get('__unseen__', []))
            return 'OK', [unseen]
        if command == 'search' and args[0] == 'HEADER':
            header_name = args[1]
            needle = args[2]
            matches = []
            for uid, raw in self.header_map.items():
                if uid == '__unseen__':
                    continue
                if needle.encode('utf-8') in raw and header_name.encode('utf-8') in raw:
                    matches.append(uid.encode('ascii'))
            return 'OK', [b' '.join(matches)]
        if command == 'fetch':
            uid_set = args[0]
            fetch_spec = args[1]
            if fetch_spec == '(UID FLAGS BODY.PEEK[HEADER])':
                data = []
                for uid in uid_set.split(','):
                    raw = self.header_map.get(uid)
                    if raw is None:
                        continue
                    flags = b'\\Seen' if uid != '6' else b''
                    data.append((f'{uid} (UID {uid} FLAGS ({flags.decode()}) BODY[HEADER] {{1}}'.encode('ascii'), raw))
                    data.append(b')')
                return 'OK', data
            if fetch_spec == '(UID FLAGS RFC822)':
                raw = self.body_bytes
                flags = b'\\Seen'
                return 'OK', [(f'{uid_set} (UID {uid_set} FLAGS ({flags.decode()}) RFC822 {{1}}'.encode('ascii'), raw), b')']
            if fetch_spec == '(RFC822)':
                return 'OK', [(f'{uid_set} (RFC822 {{1}}'.encode('ascii'), self.body_bytes), b')']
        if command == 'store':
            return 'OK', [b'']
        if command == 'move':
            return 'OK', [b'']
        return 'OK', [b'']

    def logout(self):
        self.logged_out = True
        return 'BYE', [b'']

    def shutdown(self):
        self.logged_out = True


class _FakeSMTP:
    def __init__(self, supports_starttls=True):
        self.login_calls = []
        self.sendmail_calls = []
        self.quit_calls = 0
        self.starttls_calls = 0
        self.ehlo_calls = 0
        self._supports_starttls = supports_starttls

    def login(self, user, password):
        self.login_calls.append((user, password))

    def ehlo_or_helo_if_needed(self):
        self.ehlo_calls += 1

    def has_extn(self, name):
        return self._supports_starttls and str(name).lower() == 'starttls'

    def starttls(self, context=None):
        self.starttls_calls += 1
        return (220, b'ready')

    def sendmail(self, from_addr, recipients, message):
        self.sendmail_calls.append((from_addr, tuple(recipients), message))

    def quit(self):
        self.quit_calls += 1


class IMAPSMTPBackendTests(unittest.TestCase):
    def make_backend(self):
        backend = object.__new__(IMAPSMTPBackend)
        backend.identity = 'imap@example.com'
        backend.provider = 'imap'
        backend._sync_lock = threading.Lock()
        backend._refresh_lock = threading.Lock()
        backend._folder_refresh_inflight = set()
        backend._passwords = {'imap-password': 'secret', 'smtp-password': 'secret'}
        backend._imap_host = 'imap.example.com'
        backend._imap_user = 'imap@example.com'
        backend._imap_use_ssl = True
        backend._imap_use_tls = False
        backend._imap_accept_ssl_errors = False
        backend._smtp_host = 'smtp.example.com'
        backend._smtp_user = 'imap@example.com'
        backend._smtp_use_ssl = True
        backend._smtp_use_tls = False
        backend._smtp_accept_ssl_errors = False
        backend._smtp_use_auth = True
        backend._smtp_auth_login = False
        backend._smtp_auth_plain = True
        backend._smtp_auth_xoauth2 = False
        backend._folder_aliases = {
            'inbox': 'INBOX',
            'sent': 'Sent',
            'drafts': 'Drafts',
            'trash': 'Trash',
            'spam': 'Spam',
        }
        backend._folder_sync = {}
        return backend

    def test_init_uses_native_password_source(self):
        class _Account:
            props = type('Props', (), {
                'identity': 'imap@example.com',
                'presentation_identity': 'imap@example.com',
                'provider_type': 'imap_smtp',
                'mail_disabled': False,
            })()

        class _PasswordSource:
            def call_get_password_sync(self, password_id, _cancellable=None):
                return True, 'secret' if password_id == 'imap-password' else ''

        class _Mail:
            props = type('Props', (), {
                'imap_host': 'imap.example.com',
                'imap_user_name': 'imap@example.com',
                'imap_use_ssl': True,
                'imap_use_tls': False,
                'imap_accept_ssl_errors': False,
                'smtp_host': 'smtp.example.com',
                'smtp_user_name': 'imap@example.com',
                'smtp_use_ssl': True,
                'smtp_use_tls': False,
                'smtp_accept_ssl_errors': False,
                'smtp_use_auth': True,
                'smtp_auth_login': False,
                'smtp_auth_plain': True,
                'smtp_auth_xoauth2': False,
            })()

        class _Source:
            def get_account(self):
                return _Account()

            def get_mail(self):
                return _Mail()

            def get_password_based(self):
                return _PasswordSource()

        descriptor = AccountDescriptor(
            source='native',
            provider_kind='imap-smtp',
            identity='imap@example.com',
            auth_kind='native-password',
            source_obj=_Source(),
        )

        backend = IMAPSMTPBackend(descriptor)

        self.assertEqual(backend._ensure_password('imap-password'), 'secret')

    def test_get_cached_messages_returns_sorted_cached_rows(self):
        backend = self.make_backend()
        backend._folder_sync['INBOX'] = {
            'messages': [
                {'uid': '5', 'date': datetime(2026, 4, 7, 7, 30, tzinfo=timezone.utc)},
                {'uid': '6', 'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc)},
            ],
            'last_accessed_at': '',
        }

        messages = backend.get_cached_messages('INBOX', limit=2)

        self.assertEqual([msg['uid'] for msg in messages], ['6', '5'])

    def test_fetch_messages_parses_headers_and_persists_cache(self):
        backend = self.make_backend()
        search_uids = b'5 6'
        header_map = {
            '5': _header_bytes(
                'Older',
                'Alice <alice@example.com>',
                'imap@example.com',
                '<5@example.com>',
                'Wed, 07 Apr 2026 07:30:00 +0000',
            ),
            '6': _header_bytes(
                'Newer',
                'Bob <bob@example.com>',
                'imap@example.com',
                '<6@example.com>',
                'Wed, 07 Apr 2026 08:30:00 +0000',
            ),
        }
        fake_imap = _FakeIMAP(search_uids, header_map)

        @contextmanager
        def _session():
            yield fake_imap

        backend._imap_session = lambda: _session()
        with mock.patch.object(imap_module, 'set_account_state') as set_state:
            messages = backend.fetch_messages('INBOX', limit=2)

        self.assertEqual([msg['uid'] for msg in messages], ['6', '5'])
        self.assertEqual(messages[0]['backend'], 'imap')
        self.assertEqual(messages[0]['thread_source'], 'imap')
        self.assertEqual(messages[0]['message_id'], '<6@example.com>')
        self.assertTrue(set_state.called)

    def test_fetch_messages_uses_live_sync_even_when_cache_exists(self):
        backend = self.make_backend()
        backend._folder_sync['INBOX'] = {
            'messages': [{'uid': 'cached', 'date': datetime(2026, 4, 7, 7, 30, tzinfo=timezone.utc)}],
            'last_accessed_at': '',
        }
        backend._sync_folder_messages = mock.Mock(return_value=[
            {'uid': 'fresh', 'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc)},
        ])

        messages = backend.fetch_messages('INBOX', limit=10)

        self.assertEqual([msg['uid'] for msg in messages], ['fresh'])
        backend._sync_folder_messages.assert_called_once_with('INBOX', 10)

    def test_background_updates_detect_read_state_changes_without_uid_changes(self):
        backend = self.make_backend()
        backend._folder_sync['INBOX'] = {
            'messages': [
                {
                    'uid': '5',
                    'is_read': True,
                    'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
                }
            ],
            'last_accessed_at': '',
        }
        backend._sync_folder_messages = lambda folder, limit: [
            {
                'uid': '5',
                'is_read': False,
                'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
            }
        ]
        backend.get_unread_count = lambda folder: 1
        backend.consume_sync_notices = lambda: []

        result = backend.check_background_updates(['INBOX'])

        self.assertIn('INBOX', result['changed_folders'])
        self.assertEqual(result['counts']['inbox'], 1)

    def test_fetch_body_extracts_text_html_and_attachments(self):
        backend = self.make_backend()
        message = EmailMessage()
        message['From'] = 'Alice <alice@example.com>'
        message['To'] = 'imap@example.com'
        message['Subject'] = 'Hello'
        message['Date'] = 'Wed, 07 Apr 2026 08:30:00 +0000'
        message['Message-ID'] = '<6@example.com>'
        message.set_content('Plain body')
        message.add_alternative('<p>HTML body</p>', subtype='html')
        message.add_attachment(b'attachment-bytes', maintype='application', subtype='octet-stream', filename='file.bin')
        fake_imap = _FakeIMAP(b'6', {}, body_bytes=message.as_bytes())

        @contextmanager
        def _session():
            yield fake_imap

        backend._imap_session = lambda: _session()
        html, text, attachments = backend.fetch_body('6', 'INBOX')

        self.assertIn('HTML body', html)
        self.assertIn('Plain body', text)
        self.assertEqual(attachments[0]['name'], 'file.bin')
        self.assertEqual(attachments[0]['data'], b'attachment-bytes')

    def test_fetch_body_raises_body_fetch_error_when_body_is_missing(self):
        backend = self.make_backend()
        backend._imap_fetch_body_message = lambda folder, uid: None

        with self.assertRaises(BodyFetchError):
            backend.fetch_body('6', 'INBOX')

    def test_send_message_uses_authenticated_smtp(self):
        backend = self.make_backend()
        fake_smtp = _FakeSMTP()

        @contextmanager
        def _session():
            yield fake_smtp

        backend._smtp_session = lambda: _session()

        backend.send_message(
            'to@example.com',
            'Subject',
            'Body',
            cc='copy@example.com',
            bcc='hidden@example.com',
        )

        self.assertEqual(fake_smtp.sendmail_calls[0][0], 'imap@example.com')
        self.assertIn('to@example.com', fake_smtp.sendmail_calls[0][1])
        self.assertIn('copy@example.com', fake_smtp.sendmail_calls[0][1])
        self.assertIn('hidden@example.com', fake_smtp.sendmail_calls[0][1])

    def test_unread_count_policy_is_explicit(self):
        backend = self.make_backend()

        policy = backend.get_unread_count_policy('INBOX')

        self.assertEqual(policy['route'], 'primary')
        self.assertEqual(policy['source'], 'imap-unseen')

    def test_imap_session_auto_upgrades_when_starttls_is_advertised(self):
        backend = self.make_backend()
        backend._imap_use_ssl = False
        backend._imap_use_tls = False
        fake_imap = _FakeIMAP(b'', {}, capabilities=b'IMAP4rev1 STARTTLS')

        backend._ensure_password = lambda *_args, **_kwargs: 'secret'

        with mock.patch.object(imap_module.imaplib, 'IMAP4', return_value=fake_imap), \
             mock.patch.object(imap_module, 'ensure_network_ready', lambda: None):
            with backend._imap_session() as session:
                self.assertIs(session, fake_imap)

        self.assertEqual(fake_imap.starttls_calls, 1)
        self.assertTrue(fake_imap.logged_out)

    def test_smtp_session_auto_upgrades_when_starttls_is_advertised(self):
        backend = self.make_backend()
        backend._smtp_use_ssl = False
        backend._smtp_use_tls = False
        fake_smtp = _FakeSMTP(supports_starttls=True)

        backend._ensure_password = lambda *_args, **_kwargs: 'secret'

        with mock.patch.object(imap_module.smtplib, 'SMTP', return_value=fake_smtp), \
             mock.patch.object(imap_module, 'ensure_network_ready', lambda: None):
            with backend._smtp_session() as session:
                self.assertIs(session, fake_smtp)

        self.assertEqual(fake_smtp.starttls_calls, 1)
        self.assertGreaterEqual(fake_smtp.ehlo_calls, 1)
        self.assertEqual(fake_smtp.login_calls[0], ('imap@example.com', 'secret'))


if __name__ == '__main__':
    unittest.main()

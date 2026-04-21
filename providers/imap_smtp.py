"""Generic IMAP/SMTP provider implementation."""

import base64
import contextlib
import email as email_parser
import imaplib
import re
import smtplib
import ssl
import threading
import urllib.parse
from datetime import datetime, timezone
from email import policy as email_policy
from email.header import decode_header as _decode_header_raw
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser

try:
    from ..sync_state import get_account_state, set_account_state
    from .common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        BodyFetchError,
        build_sync_notice,
        build_count_policy,
        build_sync_policy,
        messages_changed,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from .sync_rows import deserialize_sync_messages, serialize_sync_messages
except ImportError:
    from sync_state import get_account_state, set_account_state
    from providers.common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        BodyFetchError,
        build_sync_notice,
        build_count_policy,
        build_sync_policy,
        messages_changed,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from providers.sync_rows import deserialize_sync_messages, serialize_sync_messages


_IMAP_SMTP_TIMEOUT_SECS = 20
_IMAP_SYNC_RECENT_MESSAGES_LIMIT = 100


def _decode_imap_utf7(s):
    result = []
    i = 0
    while i < len(s):
        if s[i] == '&':
            j = s.find('-', i + 1)
            if j == -1:
                result.append(s[i:])
                break
            encoded = s[i + 1:j]
            if encoded == '':
                result.append('&')
            else:
                b64 = encoded.replace(',', '/')
                pad = (4 - len(b64) % 4) % 4
                decoded = base64.b64decode(b64 + '=' * pad).decode('utf-16-be')
                result.append(decoded)
            i = j + 1
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


def _parse_imap_list_line(line):
    text = bytes(line or b'')
    match = re.match(rb'^\((?P<flags>.*?)\)\s+(?P<delim>nil|"(?:[^"]*)")\s+(?P<name>.*)$', text)
    if not match:
        return set(), '', ''
    raw_flags = match.group('flags') or b''
    flags = {flag.decode('ascii', errors='ignore').lower() for flag in re.findall(rb'\\[A-Za-z]+', raw_flags)}
    raw_name = match.group('name').strip()
    if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
        raw_name = raw_name[1:-1]
    name = raw_name.decode('utf-8', errors='replace')
    name = _decode_imap_utf7(name)
    return flags, match.group('delim').decode('ascii', errors='ignore').lower(), name


def _first_imap_literal_bytes(data):
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b''


def _imap_flags_from_meta(meta):
    return {flag.decode('ascii', errors='ignore').lower() for flag in re.findall(rb'\\[A-Za-z]+', meta or b'')}


def _mailbox_folder_name(folder):
    folder_text = str(folder or '').strip()
    if not folder_text:
        return 'INBOX'
    return folder_text


class IMAPSMTPBackend:
    FOLDERS = [
        ('INBOX', 'Inbox', 'mail-inbox-symbolic'),
        ('Sent', 'Sent', 'mail-send-symbolic'),
        ('Drafts', 'Drafts', 'accessories-text-editor-symbolic'),
        ('Trash', 'Trash', 'user-trash-symbolic'),
        ('Spam', 'Spam', 'mail-mark-junk-symbolic'),
    ]
    _STANDARD_FOLDER_IDS = frozenset(folder_id for folder_id, _name, _icon in FOLDERS)

    def __init__(self, account_source):
        descriptor = coerce_account_descriptor(account_source, 'imap-smtp')
        self.account_descriptor = descriptor
        self.source_obj = descriptor.source_obj
        self.account = getattr(self.source_obj, 'get_account', lambda: None)()
        self.mail = getattr(self.source_obj, 'get_mail', lambda: None)()
        self.mail_props = getattr(self.mail, 'props', None)
        self.identity = descriptor.identity
        self.presentation_name = descriptor.presentation_name or descriptor.identity
        self.accent_color = str((descriptor.metadata or {}).get('accent_color') or '').strip()
        self.provider = 'imap'
        self._sync_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._folder_refresh_inflight = set()
        self._passwords = {}
        self._imap_host = str(getattr(self.mail_props, 'imap_host', '') or '').strip()
        self._imap_user = str(getattr(self.mail_props, 'imap_user_name', self.identity) or self.identity).strip() or self.identity
        self._imap_use_ssl = bool(getattr(self.mail_props, 'imap_use_ssl', True))
        self._imap_use_tls = bool(getattr(self.mail_props, 'imap_use_tls', False))
        self._imap_accept_ssl_errors = bool(getattr(self.mail_props, 'imap_accept_ssl_errors', False))
        self._smtp_host = str(getattr(self.mail_props, 'smtp_host', '') or '').strip()
        self._smtp_user = str(getattr(self.mail_props, 'smtp_user_name', self.identity) or self.identity).strip() or self.identity
        self._smtp_use_ssl = bool(getattr(self.mail_props, 'smtp_use_ssl', True))
        self._smtp_use_tls = bool(getattr(self.mail_props, 'smtp_use_tls', False))
        self._smtp_accept_ssl_errors = bool(getattr(self.mail_props, 'smtp_accept_ssl_errors', False))
        self._smtp_use_auth = bool(getattr(self.mail_props, 'smtp_use_auth', True))
        self._smtp_auth_login = bool(getattr(self.mail_props, 'smtp_auth_login', False))
        self._smtp_auth_plain = bool(getattr(self.mail_props, 'smtp_auth_plain', False))
        self._smtp_auth_xoauth2 = bool(getattr(self.mail_props, 'smtp_auth_xoauth2', False))
        self._sync_notices = []
        self._folder_aliases = {
            'inbox': 'INBOX',
            'sent': 'Sent',
            'sent items': 'Sent',
            'sent mail': 'Sent',
            'drafts': 'Drafts',
            'draft': 'Drafts',
            'trash': 'Trash',
            'deleted items': 'Trash',
            'spam': 'Spam',
            'junk': 'Spam',
        }
        sync_state = get_account_state('imap', self.identity)
        folder_states = sync_state.get('folders', {})
        self._folder_sync = {}
        for folder, folder_state in folder_states.items():
            if not folder:
                continue
            self._folder_sync[folder] = {
                'messages': self._deserialize_sync_messages(folder_state.get('messages', [])),
                'last_accessed_at': folder_state.get('last_accessed_at') or '',
            }

    def _serialize_sync_messages(self, messages):
        return serialize_sync_messages(
            messages,
            limit=_IMAP_SYNC_RECENT_MESSAGES_LIMIT,
            default_folder='INBOX',
            default_thread_source='imap',
        )

    def _deserialize_sync_messages(self, messages):
        return deserialize_sync_messages(
            messages,
            limit=_IMAP_SYNC_RECENT_MESSAGES_LIMIT,
            default_folder='INBOX',
            provider_name='imap',
            identity=self.identity,
            backend_obj=self,
            default_thread_source='imap',
        )

    def _persist_sync_state(self):
        with self._sync_lock:
            folders = {}
            for folder, folder_state in self._folder_sync.items():
                messages = self._serialize_sync_messages(folder_state.get('messages', []))
                last_accessed_at = folder_state.get('last_accessed_at') or ''
                if not messages and not last_accessed_at:
                    continue
                state_row = {'messages': messages}
                if last_accessed_at:
                    state_row['last_accessed_at'] = last_accessed_at
                folders[folder] = state_row
            state = {'folders': folders} if folders else {}
        set_account_state('imap', self.identity, state)

    def _set_sync_notice(self, kind, detail=None):
        if isinstance(kind, dict) and detail is None:
            notice = build_sync_notice(kind.get('kind'), kind.get('detail'))
        else:
            notice = build_sync_notice(kind, detail)
        with self._sync_lock:
            notices = getattr(self, '_sync_notices', None)
            if notices is None:
                notices = []
                self._sync_notices = notices
            notices.append(notice)

    def consume_sync_notices(self):
        with self._sync_lock:
            notices = list(getattr(self, '_sync_notices', []) or [])
            self._sync_notices = []
        return notices

    def consume_sync_notice(self):
        notices = self.consume_sync_notices()
        return notices[0] if notices else None

    def get_sync_policy(self):
        # owns: native IMAP truth for rows and unread counts.
        return build_sync_policy(
            'imap',
            'IMAP fetch and UNSEEN recount',
            'Cached rows plus retry on IMAP or SMTP errors',
            'IMAP folder refresh plus server-side unread recount',
            notes='IMAP remains the truth source for counts and row state when no provider API exists.',
        )

    def get_unread_count_policy(self, folder='INBOX', force_primary=False):
        return build_count_policy(
            'imap',
            'IMAP UNSEEN unread count',
            'IMAP UNSEEN unread count',
            'IMAP UNSEEN unread count',
            route='primary',
            source='imap-unseen',
            notes='IMAP count lookups always use the server-side UNSEEN search.',
        )

    def _folder_sync_state(self, folder):
        folder_key = _mailbox_folder_name(folder)
        return self._folder_sync.setdefault(
            folder_key,
            {
                'messages': [],
                'last_accessed_at': '',
            },
        )

    def _update_folder_sync_state(self, folder, messages=None):
        with self._sync_lock:
            folder_key = _mailbox_folder_name(folder)
            folder_state = self._folder_sync_state(folder_key)
            if messages is not None:
                ordered = sorted(
                    list(messages or []),
                    key=lambda item: item.get('date') or datetime.now(timezone.utc),
                    reverse=True,
                )
                folder_state['messages'] = ordered[:_IMAP_SYNC_RECENT_MESSAGES_LIMIT]
            folder_state['last_accessed_at'] = _utcnow_iso()
        self._persist_sync_state()

    def _resolve_folder(self, folder):
        folder_text = _mailbox_folder_name(folder)
        return self._folder_aliases.get(folder_text.lower(), folder_text)

    def _set_folder_alias(self, logical_name, actual_name):
        logical_text = _mailbox_folder_name(logical_name)
        actual_text = _mailbox_folder_name(actual_name)
        if not logical_text or not actual_text:
            return
        with self._sync_lock:
            previous = self._folder_aliases.get(logical_text.lower())
            self._folder_aliases[logical_text.lower()] = actual_text
            self._folder_aliases[actual_text.lower()] = actual_text
            if previous and previous != actual_text and previous in self._folder_sync and actual_text not in self._folder_sync:
                self._folder_sync[actual_text] = self._folder_sync.pop(previous)
        if previous and previous != actual_text:
            self._persist_sync_state()

    def _folder_cached_messages(self, folder):
        folder_key = self._resolve_folder(folder)
        with self._sync_lock:
            return list(self._folder_sync_state(folder_key).get('messages', []))

    def _ensure_password(self, password_id='imap-password'):
        password_key = str(password_id or '').strip() or 'imap-password'
        if password_key in self._passwords:
            return self._passwords[password_key]
        password_source = getattr(self.source_obj, 'get_password_based', lambda: None)()
        password_getter = getattr(password_source, 'call_get_password_sync', None)
        if not callable(password_getter):
            raise RuntimeError('Password provider unavailable')
        for candidate in (password_key, 'imap-password', 'smtp-password'):
            candidate = str(candidate or '').strip()
            if not candidate:
                continue
            try:
                _ok, password = password_getter(candidate, None)
                password = str(password or '')
                if not password:
                    continue
                self._passwords[password_key] = password
                self._passwords[candidate] = password
                return password
            except Exception:
                continue
        raise RuntimeError('Account password unavailable')

    def _ssl_context(self):
        if self._imap_accept_ssl_errors or self._smtp_accept_ssl_errors:
            return ssl._create_unverified_context()
        return ssl.create_default_context()

    def _imap_supports_starttls(self, imap):
        try:
            status, data = imap.capability()
        except Exception:
            return False
        if status != 'OK':
            return False
        capability_text = ' '.join(
            part.decode('ascii', errors='ignore') if isinstance(part, (bytes, bytearray)) else str(part)
            for part in (data or [])
        ).upper()
        return 'STARTTLS' in capability_text

    def _smtp_supports_starttls(self, smtp):
        try:
            smtp.ehlo_or_helo_if_needed()
        except Exception:
            return False
        try:
            return bool(smtp.has_extn('starttls'))
        except Exception:
            return False

    @contextlib.contextmanager
    def _imap_session(self):
        ensure_network_ready()
        if not self._imap_host:
            raise RuntimeError('IMAP host unavailable')
        password = self._ensure_password('imap-password')
        context = self._ssl_context()
        port = 993 if self._imap_use_ssl else 143
        if self._imap_use_ssl:
            imap = imaplib.IMAP4_SSL(
                self._imap_host,
                port,
                ssl_context=context,
                timeout=_IMAP_SMTP_TIMEOUT_SECS,
            )
        else:
            imap = imaplib.IMAP4(
                self._imap_host, port, timeout=_IMAP_SMTP_TIMEOUT_SECS
            )
            should_starttls = self._imap_use_tls or self._imap_supports_starttls(imap)
            if should_starttls:
                imap.starttls(ssl_context=context)
        imap.login(self._imap_user, password)
        try:
            yield imap
        finally:
            try:
                imap.logout()
            except Exception:
                try:
                    imap.shutdown()
                except Exception:
                    pass

    @contextlib.contextmanager
    def _smtp_session(self):
        ensure_network_ready()
        if not self._smtp_host:
            raise RuntimeError('SMTP host unavailable')
        password = self._ensure_password('smtp-password')
        context = self._ssl_context()
        port = 465 if self._smtp_use_ssl else 587
        if self._smtp_use_ssl:
            smtp = smtplib.SMTP_SSL(self._smtp_host, port, context=context, timeout=_IMAP_SMTP_TIMEOUT_SECS)
        else:
            smtp = smtplib.SMTP(self._smtp_host, port, timeout=_IMAP_SMTP_TIMEOUT_SECS)
            should_starttls = self._smtp_use_tls or self._smtp_supports_starttls(smtp)
            if should_starttls:
                smtp.starttls(context=context)
                try:
                    smtp.ehlo()
                except Exception:
                    pass
        needs_auth = bool(self._smtp_use_auth or self._smtp_auth_login or self._smtp_auth_plain or self._smtp_auth_xoauth2)
        if needs_auth:
            smtp.login(self._smtp_user, password)
        try:
            yield smtp
        finally:
            try:
                smtp.quit()
            except Exception:
                pass

    def _decode_message(self, raw_bytes):
        return BytesParser(policy=email_policy.default).parsebytes(raw_bytes or b'')

    def _message_headers(self, message):
        headers = {}
        for name in ('from', 'to', 'cc', 'subject', 'date', 'message-id', 'content-type'):
            headers[name] = _decode_str(message.get(name, ''))
        return headers

    def _message_date(self, message, headers):
        date_value = headers.get('date', '')
        if date_value:
            try:
                return _aware_utc_datetime(email_parser.utils.parsedate_to_datetime(date_value))
            except Exception:
                pass
        try:
            return datetime.now(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _message_to_row(self, uid, message, folder='INBOX', flags=None):
        headers = self._message_headers(message)
        sender_name, sender_email = email_parser.utils.parseaddr(headers.get('from', ''))
        if not sender_name:
            sender_name = sender_email
        content_type = headers.get('content-type', '').lower()
        message_id = headers.get('message-id', '')
        is_read = '\\seen' not in set(flag.lower() for flag in (flags or []))
        return {
            'uid': str(uid),
            'subject': headers.get('subject', '(no subject)') or '(no subject)',
            'sender_name': sender_name or sender_email or 'Unknown sender',
            'sender_email': sender_email,
            'to_addrs': _parse_addrs(headers.get('to', '')),
            'cc_addrs': _parse_addrs(headers.get('cc', '')),
            'date': self._message_date(message, headers),
            'is_read': is_read,
            'has_attachments': 'multipart' in content_type or 'attachment' in content_type,
            'snippet': '',
            'folder': folder,
            'backend': 'imap',
            'account': self.identity,
            'backend_obj': self,
            'thread_id': message_id or str(uid),
            'thread_source': 'imap',
            'message_id': message_id,
        }

    def _imap_fetch_header_messages(self, folder, uids):
        rows = []
        if not uids:
            return rows
        folder_name = self._resolve_folder(folder)
        uid_set = ','.join(str(uid) for uid in uids if str(uid).strip())
        if not uid_set:
            return rows
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return rows
            status, data = imap.uid('fetch', uid_set, '(UID FLAGS BODY.PEEK[HEADER])')
            if status != 'OK':
                return rows
            for item in data or []:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                meta, raw = item[0], item[1]
                uid_match = re.search(rb'UID\s+(\d+)', meta or b'')
                uid = uid_match.group(1).decode('ascii', errors='ignore') if uid_match else ''
                if not uid:
                    continue
                flags = _imap_flags_from_meta(meta)
                message = self._decode_message(raw)
                rows.append(self._message_to_row(uid, message, folder_name, flags=flags))
        rows.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        return rows

    def _imap_fetch_body_message(self, folder, uid):
        folder_name = self._resolve_folder(folder)
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return None
            status, data = imap.uid('fetch', str(uid), '(UID FLAGS RFC822)')
            if status != 'OK':
                return None
            raw = _first_imap_literal_bytes(data)
            if not raw:
                return None
            message = self._decode_message(raw)
            flags = set()
            for item in data or []:
                if isinstance(item, tuple):
                    flags = _imap_flags_from_meta(item[0])
                    break
            return message, folder_name, uid, flags

    def _imap_fetch_attachment_bytes(self, folder, uid, attachment):
        folder_name = self._resolve_folder(folder)
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return None
            status, data = imap.uid('fetch', str(uid), '(RFC822)')
            if status != 'OK':
                return None
            raw = _first_imap_literal_bytes(data)
            if not raw:
                return None
            message = self._decode_message(raw)
        target_name = str((attachment or {}).get('name') or '').strip().lower()
        target_cid = str((attachment or {}).get('content_id') or '').strip().lower()
        for part in message.walk():
            if part.is_multipart():
                continue
            filename = str(part.get_filename() or '').strip()
            disposition = str(part.get_content_disposition() or '').strip().lower()
            content_id = str(part.get('Content-ID', '') or '').strip().lower().strip('<>')
            if target_name and filename.lower() != target_name and part.get_content_type() != (attachment or {}).get('content_type'):
                continue
            if target_cid and content_id != target_cid:
                continue
            if disposition != 'attachment' and not filename and not target_cid:
                continue
            payload = part.get_payload(decode=True) or b''
            if payload:
                return payload
        return None

    def _folder_messages(self, folder, limit):
        folder_name = self._resolve_folder(folder)
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return []
            status, data = imap.uid('search', None, 'ALL')
            if status != 'OK':
                return []
            all_uids = [uid.decode('ascii', errors='ignore') for uid in (data[0] or b'').split() if uid]
            if not all_uids:
                return []
            limit = max(1, int(limit))
            target_uids = all_uids[-limit:]
        return self._imap_fetch_header_messages(folder_name, target_uids)

    def _store_folder_messages(self, folder, messages):
        folder_name = self._resolve_folder(folder)
        self._update_folder_sync_state(folder_name, messages=messages)
        return list(messages or [])

    def _background_refresh_folder(self, folder):
        try:
            folder_name = self._resolve_folder(folder)
            messages = self._folder_messages(folder_name, _IMAP_SYNC_RECENT_MESSAGES_LIMIT)
            self._store_folder_messages(folder_name, messages)
        finally:
            with self._refresh_lock:
                self._folder_refresh_inflight.discard(self._resolve_folder(folder))

    def _ensure_folder_refresh_async(self, folder):
        folder_name = self._resolve_folder(folder)
        with self._refresh_lock:
            if folder_name in self._folder_refresh_inflight:
                return
            self._folder_refresh_inflight.add(folder_name)
        threading.Thread(target=self._background_refresh_folder, args=(folder_name,), daemon=True).start()

    def _sync_folder_messages(self, folder, limit):
        messages = self._folder_messages(folder, limit)
        self._store_folder_messages(folder, messages)
        return messages

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        extras = []
        try:
            with self._imap_session() as imap:
                status, _selected = imap.select(self._resolve_folder('INBOX'), readonly=True)
                if status != 'OK':
                    return []
                status, data = imap.list()
                if status != 'OK' or not data:
                    return []
                for line in data:
                    flags, _delim, name = _parse_imap_list_line(line)
                    if not name:
                        continue
                    logical_name = name
                    lower = name.lower()
                    if '\\inbox' in flags or lower == 'inbox':
                        self._set_folder_alias('INBOX', name)
                        continue
                    if '\\sent' in flags or lower in {'sent', 'sent items', 'sent mail'}:
                        self._set_folder_alias('Sent', name)
                        continue
                    if '\\drafts' in flags or lower in {'drafts', 'draft'}:
                        self._set_folder_alias('Drafts', name)
                        continue
                    if '\\trash' in flags or lower in {'trash', 'deleted items', 'bin'}:
                        self._set_folder_alias('Trash', name)
                        continue
                    if '\\junk' in flags or lower in {'spam', 'junk'}:
                        self._set_folder_alias('Spam', name)
                        continue
                    if logical_name in self._STANDARD_FOLDER_IDS:
                        continue
                    extras.append((logical_name, logical_name, 'folder-symbolic'))
        except Exception:
            return []
        extras.sort(key=lambda item: item[1].lower())
        return extras

    def get_cached_messages(self, folder='INBOX', limit=50):
        folder_name = self._resolve_folder(folder)
        cached = self._folder_cached_messages(folder_name)
        cached.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        return cached[:limit]

    def fetch_messages(self, folder='INBOX', limit=50):
        ensure_network_ready()
        folder_name = self._resolve_folder(folder)
        try:
            return self._sync_folder_messages(folder_name, limit)[:limit]
        except Exception:
            self._set_sync_notice(build_sync_notice('error', f'Could not load {folder_name}', retryable=False))
            raise

    def fetch_thread_messages(self, thread_id):
        if not thread_id:
            return []
        thread_text = str(thread_id).strip()
        cached_message = None
        for folder, folder_state in list(self._folder_sync.items()):
            for msg in folder_state.get('messages', []):
                if msg.get('thread_id') == thread_text or msg.get('uid') == thread_text or msg.get('message_id') == thread_text:
                    cached_message = dict(msg)
                    break
            if cached_message is not None:
                break
        if cached_message is None:
            return []
        message_id = str(cached_message.get('message_id') or thread_text).strip()
        if not message_id:
            return [cached_message]
        folders_to_search = [cached_message.get('folder', 'INBOX')]
        for folder_id, _name, _icon in self.FOLDERS:
            if folder_id not in folders_to_search:
                folders_to_search.append(folder_id)
        results = []
        seen = set()
        for folder in folders_to_search:
            try:
                with self._imap_session() as imap:
                    status, _selected = imap.select(self._resolve_folder(folder), readonly=True)
                    if status != 'OK':
                        continue
                    for header_name in ('Message-ID', 'References', 'In-Reply-To'):
                        status, data = imap.uid('search', None, 'HEADER', header_name, message_id)
                        if status != 'OK':
                            continue
                        uids = [uid.decode('ascii', errors='ignore') for uid in (data[0] or b'').split() if uid]
                        if not uids:
                            continue
                        rows = self._imap_fetch_header_messages(folder, uids)
                        for row in rows:
                            key = (row.get('folder', ''), row.get('uid', ''))
                            if key in seen:
                                continue
                            seen.add(key)
                            results.append(row)
            except Exception:
                continue
        if not results:
            return [cached_message]
        results.append(cached_message)
        results.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        deduped = []
        deduped_seen = set()
        for row in results:
            key = (row.get('folder', ''), row.get('uid', ''))
            if key in deduped_seen:
                continue
            deduped_seen.add(key)
            deduped.append(row)
        return deduped

    def fetch_body(self, uid, folder='INBOX'):
        uid_text = str(uid or '').strip()
        if not uid_text:
            raise BodyFetchError('Message UID is unavailable')
        folder_name = self._resolve_folder(folder)
        try:
            body_message = self._imap_fetch_body_message(folder_name, uid_text)
            if body_message is None:
                raise BodyFetchError(f'Message body is unavailable for {folder_name}')
            return self._body_from_message(*body_message)
        except BodyFetchError:
            raise
        except Exception as exc:
            raise BodyFetchError(f'Could not load message from {folder_name}') from exc

    def _body_from_message(self, message, folder, uid, flags=None):
        if message is None:
            return None, None, []
        html = None
        text = None
        attachments = []
        for part in message.walk():
            if part.is_multipart():
                continue
            filename = str(part.get_filename() or '').strip()
            disposition = str(part.get_content_disposition() or '').strip().lower()
            content_type = part.get_content_type()
            content_id = str(part.get('Content-ID', '') or '').strip().strip('<>')
            payload = part.get_payload(decode=True) or b''
            if filename or disposition == 'attachment':
                attachments.append({
                    'attachment_id': filename or content_id or f'{uid}-{len(attachments) + 1}',
                    'attachment_type': content_type,
                    'name': filename or 'attachment',
                    'size': len(payload),
                    'content_type': content_type,
                    'disposition': disposition or 'attachment',
                    'content_id': content_id or '',
                    'data': payload,
                })
                continue
            try:
                content = part.get_content()
            except Exception:
                content = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
            if content_type == 'text/html' and html is None:
                html = content
            elif content_type == 'text/plain' and text is None:
                text = content
        return html, text, attachments

    def fetch_attachment_data(self, uid, attachment, folder='INBOX'):
        if attachment and attachment.get('data') is not None:
            return attachment.get('data')
        return self._imap_fetch_attachment_bytes(folder, uid, attachment)

    def mark_as_read(self, uid, folder='INBOX'):
        folder_name = self._resolve_folder(folder)
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name)
            if status != 'OK':
                return
            imap.uid('store', str(uid), '+FLAGS.SILENT', r'(\Seen)')
        self.update_cached_message_read_state(folder_name, uid, True)

    def mark_as_unread(self, uid, folder='INBOX'):
        folder_name = self._resolve_folder(folder)
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name)
            if status != 'OK':
                return
            imap.uid('store', str(uid), '-FLAGS.SILENT', r'(\Seen)')
        self.update_cached_message_read_state(folder_name, uid, False)

    def delete_message(self, uid, folder='INBOX'):
        folder_name = self._resolve_folder(folder)
        trash_folder = self._resolve_folder('Trash')
        with self._imap_session() as imap:
            status, _selected = imap.select(folder_name)
            if status != 'OK':
                return
            if trash_folder and trash_folder != folder_name:
                try:
                    imap.uid('move', str(uid), trash_folder)
                except Exception:
                    imap.uid('store', str(uid), '+FLAGS.SILENT', r'(\Deleted)')
                    imap.expunge()
            else:
                imap.uid('store', str(uid), '+FLAGS.SILENT', r'(\Deleted)')
                imap.expunge()
        self.remove_cached_message(folder_name, uid)

    def get_unread_count(self, folder='INBOX'):
        folder_name = self._resolve_folder(folder)
        try:
            with self._imap_session() as imap:
                status, _selected = imap.select(folder_name, readonly=True)
                if status != 'OK':
                    return 0
                status, data = imap.uid('search', None, 'UNSEEN')
                if status != 'OK' or not data:
                    return 0
                return len((data[0] or b'').split())
        except Exception:
            self._set_sync_notice(build_sync_notice('error', f'Could not read unread count for {folder_name}', retryable=False))
            return 0

    def fetch_contacts(self, query=''):
        return []

    def send_message(self, to, subject, body, html=None, cc=None, bcc=None, reply_to_msg=None, attachments=None):
        reply_msgid = (reply_to_msg or {}).get('message_id', '') if reply_to_msg else ''
        if attachments:
            outer = MIMEMultipart('mixed')
            outer['From'] = self.identity
            outer['To'] = to
            outer['Subject'] = subject
            if cc:
                outer['Cc'] = ', '.join(_normalize_recipients(cc))
            if reply_msgid:
                outer['In-Reply-To'] = reply_msgid
                outer['References'] = reply_msgid
            body_part = MIMEMultipart('alternative')
            body_part.attach(MIMEText(body, 'plain', 'utf-8'))
            if html:
                body_part.attach(MIMEText(html, 'html', 'utf-8'))
            outer.attach(body_part)
            for attachment in attachments:
                part = MIMEApplication(attachment['data'], Name=attachment['name'])
                part['Content-Type'] = (
                    f"{attachment.get('content_type', 'application/octet-stream')}; "
                    f'name="{attachment["name"]}"'
                )
                part['Content-Disposition'] = f'attachment; filename="{attachment["name"]}"'
                outer.attach(part)
            msg = outer
        else:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.identity
            msg['To'] = to
            msg['Subject'] = subject
            if cc:
                msg['Cc'] = ', '.join(_normalize_recipients(cc))
            if reply_msgid:
                msg['In-Reply-To'] = reply_msgid
                msg['References'] = reply_msgid
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            if html:
                msg.attach(MIMEText(html, 'html', 'utf-8'))
        recipients = _normalize_recipients(to) + _normalize_recipients(cc) + _normalize_recipients(bcc)
        with self._smtp_session() as smtp:
            smtp.sendmail(self.identity, recipients, msg.as_bytes())

    def check_background_updates(self, tracked_folders=None, reconcile_counts=False):
        folders = []
        seen = set()
        for folder in list(tracked_folders or []) + [folder_id for folder_id, _name, _icon in self.FOLDERS]:
            folder_text = _mailbox_folder_name(folder)
            if not folder_text or folder_text in seen:
                continue
            folders.append(folder_text)
            seen.add(folder_text)

        changed_folders = set()
        new_messages = []
        counts = {}
        for folder in folders:
            try:
                previous = self._folder_cached_messages(folder)
                refreshed = self._sync_folder_messages(folder, _IMAP_SYNC_RECENT_MESSAGES_LIMIT)
                previous_uids = {msg.get('uid') for msg in previous if msg.get('uid')}
                refreshed_uids = {msg.get('uid') for msg in refreshed if msg.get('uid')}
                if refreshed_uids != previous_uids or messages_changed(previous, refreshed):
                    changed_folders.add(folder)
                if folder.upper() == 'INBOX' and refreshed_uids != previous_uids:
                    previous_by_uid = {msg.get('uid'): msg for msg in previous if msg.get('uid')}
                    for msg in refreshed:
                        if msg.get('uid') not in previous_by_uid and not msg.get('is_read', True):
                            new_messages.append(msg)
            except Exception:
                self._set_sync_notice(build_sync_notice('error', f'Could not load {folder}', retryable=False))
                continue

        if reconcile_counts or 'INBOX' in changed_folders:
            counts['inbox'] = self.get_unread_count('INBOX')
        if reconcile_counts or 'Trash' in changed_folders:
            counts['trash'] = self.get_unread_count('Trash')
        if reconcile_counts or 'Spam' in changed_folders:
            counts['spam'] = self.get_unread_count('Spam')
        if reconcile_counts or 'Drafts' in changed_folders:
            counts['drafts'] = self.get_unread_count('Drafts')
        if reconcile_counts or 'Sent' in changed_folders:
            counts['sent'] = self.get_unread_count('Sent')
        notice = self.consume_sync_notices()
        return {
            'account': self.identity,
            'provider': self.provider,
            'changed_folders': changed_folders,
            'new_messages': new_messages,
            'counts': counts,
            'notice': notice,
        }

    def update_cached_message_read_state(self, folder, uid, is_read):
        changed = False
        folder_name = self._resolve_folder(folder)
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder_name)
            for msg in folder_state.get('messages', []):
                if msg.get('uid') != str(uid):
                    continue
                msg['is_read'] = bool(is_read)
                folder_state['last_accessed_at'] = _utcnow_iso()
                changed = True
                break
        if changed:
            self._persist_sync_state()
        return changed

    def remove_cached_message(self, folder, uid):
        removed = False
        folder_name = self._resolve_folder(folder)
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder_name)
            before = len(folder_state.get('messages', []))
            folder_state['messages'] = [msg for msg in folder_state.get('messages', []) if msg.get('uid') != str(uid)]
            removed = len(folder_state['messages']) != before
            if removed:
                folder_state['last_accessed_at'] = _utcnow_iso()
        if removed:
            self._persist_sync_state()
        return removed


__all__ = ['IMAPSMTPBackend', '_IMAP_SMTP_TIMEOUT_SECS']

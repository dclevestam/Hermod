"""Gmail provider implementation."""

import contextlib
import base64
import email as email_parser
import json
import imaplib
import re
import smtplib
import ssl
import time
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import policy as email_policy
from email.parser import BytesParser

import google.auth.credentials
from googleapiclient.discovery import build as _gapi_build
from googleapiclient.errors import HttpError as _GapiHttpError

try:
    from ..accounts.auth.oauth_common import OAuthTokenAcquisitionError
    from ..diagnostics.logger import log_event
    from ..sync_state import get_account_state, set_account_state
    from .common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        BodyFetchError,
        build_sync_notice,
        build_sync_policy,
        classify_oauth_token_error,
        classify_http_error,
        retry_delay_for_http_error,
        build_count_policy,
        messages_changed,
        SyncHealthState,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from .sync_rows import deserialize_sync_messages, serialize_sync_messages
except ImportError:
    from accounts.auth.oauth_common import OAuthTokenAcquisitionError
    from diagnostics.logger import log_event
    from sync_state import get_account_state, set_account_state
    from providers.common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        BodyFetchError,
        build_sync_notice,
        build_sync_policy,
        classify_oauth_token_error,
        classify_http_error,
        retry_delay_for_http_error,
        build_count_policy,
        messages_changed,
        SyncHealthState,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from providers.sync_rows import deserialize_sync_messages, serialize_sync_messages


_GMAIL_SMTP_TIMEOUT_SECS = 20
_GMAIL_API_TIMEOUT_SECS = 10
_GMAIL_TOKEN_CACHE_TTL_SECS = 3300
_SYNC_RECENT_MESSAGES_LIMIT = 100
_GMAIL_BATCH_MAX = 100


class _HermodGmailCredentials(google.auth.credentials.Credentials):
    """Adapter that plugs our existing OAuth token cache into googleapiclient.

    Our access tokens come from ``backend._token()``, which delegates to the
    account source's refresh logic. We stamp every outbound request with the
    current cached token; on 401 the SDK will call ``refresh()``, which
    invalidates the cache so the next ``_token()`` triggers a real refresh.
    """

    def __init__(self, backend):
        super().__init__()
        self._backend = backend

    def refresh(self, request):
        self._backend._invalidate_token()
        self.token = self._backend._token()

    @property
    def expired(self):
        return False

    @property
    def valid(self):
        return True

    def apply(self, headers, token=None):
        resolved = token or self._backend._token()
        self.token = resolved
        headers["authorization"] = "Bearer {}".format(resolved)

    def before_request(self, request, method, url, headers):
        self.apply(headers)
_GMAIL_METADATA_HEADERS = [
    'From',
    'To',
    'Cc',
    'Subject',
    'Date',
    'Content-Type',
    'Message-ID',
]


def _gmail_api_id_to_imap_id(value):
    text = str(value or '').strip()
    if not text:
        return ''
    if text.lower().startswith('0x'):
        text = text[2:]
    try:
        return str(int(text, 16))
    except ValueError:
        return text


def _decode_imap_utf7(s):
    """Decode IMAP modified UTF-7 encoded strings (e.g. '&AOQ-' -> 'ä')."""
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

class GmailBackend:
    FOLDERS = [
        ('INBOX', 'Inbox', 'mail-inbox-symbolic'),
        ('[Gmail]/Sent Mail', 'Sent', 'mail-send-symbolic'),
        ('[Gmail]/Drafts', 'Drafts', 'accessories-text-editor-symbolic'),
        ('[Gmail]/Trash', 'Trash', 'user-trash-symbolic'),
        ('[Gmail]/Spam', 'Spam', 'mail-mark-junk-symbolic'),
    ]
    _STANDARD_FOLDER_IDS = {f[0] for f in FOLDERS} | {'[Gmail]', '[Google Mail]'}
    _GMAIL_SYSTEM_LABELS = {
        'INBOX': 'INBOX',
        '[Gmail]/Sent Mail': 'SENT',
        '[Gmail]/Drafts': 'DRAFT',
        '[Gmail]/Trash': 'TRASH',
        '[Gmail]/Spam': 'SPAM',
        '[Google Mail]/Sent Mail': 'SENT',
        '[Google Mail]/Drafts': 'DRAFT',
        '[Google Mail]/Trash': 'TRASH',
        '[Google Mail]/Spam': 'SPAM',
    }
    _PARTIAL_SYNC_LABEL_IDS = frozenset({'INBOX', 'SENT', 'DRAFT', 'TRASH', 'SPAM'})

    def __init__(self, account_source):
        descriptor = coerce_account_descriptor(account_source, 'gmail')
        self.account_descriptor = descriptor
        self.source_obj = descriptor.source_obj
        self.account = getattr(self.source_obj, 'get_account', lambda: None)()
        self.identity = descriptor.identity
        self.presentation_name = descriptor.presentation_name or descriptor.identity
        self.accent_color = str((descriptor.metadata or {}).get('accent_color') or '').strip()
        self.provider = 'gmail'
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        config = dict((descriptor.metadata or {}).get('config') or {})
        mail_obj = getattr(self.source_obj, 'get_mail', lambda: None)()
        mail_props = getattr(mail_obj, 'props', None)
        self._imap_host = str(getattr(mail_props, 'imap_host', config.get('imap_host', '')) or '').strip()
        self._imap_user = str(
            getattr(mail_props, 'imap_user_name', config.get('imap_user_name', self.identity)) or self.identity
        ).strip() or self.identity
        self._imap_use_ssl = bool(getattr(mail_props, 'imap_use_ssl', config.get('imap_use_ssl', True)))
        self._imap_use_tls = bool(getattr(mail_props, 'imap_use_tls', config.get('imap_use_tls', False)))
        self._imap_accept_ssl_errors = bool(
            getattr(mail_props, 'imap_accept_ssl_errors', config.get('imap_accept_ssl_errors', False))
        )
        self._allow_imap_fallback = bool(config.get('allow_imap_fallback', False)) and bool(self._imap_host)
        self._use_gmail_api_send = bool(config.get('send_via_api', True))
        self._special_folders = {}
        self._gmail_api_available = None
        sync_state = get_account_state('gmail', self.identity)
        folder_states = sync_state.get('folders', {})
        inbox_state = folder_states.get('INBOX', {})
        self._cached_inbox_messages = self._deserialize_sync_messages(inbox_state.get('messages', []))
        self._inbox_history_id = inbox_state.get('history_id') or ''
        self._folder_sync = {}
        for folder, folder_state in folder_states.items():
            if not folder or folder == 'INBOX':
                continue
            self._folder_sync[folder] = {
                'messages': self._deserialize_sync_messages(folder_state.get('messages', [])),
                'history_id': folder_state.get('history_id') or '',
            }
        self._gmail_history_supported = None
        self._gmail_history_seed_inflight = set()
        self._gmail_labels_by_name = None
        self._gmail_labels_loaded_at = ''
        self._sync_notices = []
        self._cached_token = None
        self._cached_token_expiry = 0.0
        self._gmail_service = None
        self._gmail_service_lock = threading.Lock()
        self._gmail_list_memo = {}
        self._gmail_list_memo_lock = threading.Lock()
        self._gmail_last_health_event = None
        self._sync_health = SyncHealthState(
            provider='gmail',
            account=self.identity,
            primary_label='Gmail API',
            fallback_label='IMAP' if self._gmail_has_imap_fallback() else 'Unavailable',
        )

    def _serialize_sync_messages(self, messages):
        return serialize_sync_messages(
            messages,
            limit=_SYNC_RECENT_MESSAGES_LIMIT,
            default_folder='INBOX',
            default_thread_source='gmail-imap',
            extra_keys=('gmail_msgid',),
        )

    def _deserialize_sync_messages(self, messages):
        return deserialize_sync_messages(
            messages,
            limit=_SYNC_RECENT_MESSAGES_LIMIT,
            default_folder='INBOX',
            provider_name='gmail',
            identity=self.identity,
            backend_obj=self,
            default_thread_source='gmail-imap',
            extra_keys=('gmail_msgid',),
        )

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

    def _persist_sync_state(self):
        with self._sync_lock:
            folders = {}
            if self._cached_inbox_messages or self._inbox_history_id:
                folders['INBOX'] = {
                    'history_id': self._inbox_history_id,
                    'messages': self._serialize_sync_messages(self._cached_inbox_messages),
                }
            for folder, folder_state in self._folder_sync.items():
                history_id = folder_state.get('history_id') or ''
                messages = self._serialize_sync_messages(folder_state.get('messages', []))
                if not history_id and not messages:
                    continue
                folders[folder] = {
                    'history_id': history_id,
                    'messages': messages,
                }
            state = {'folders': folders} if folders else {}
        set_account_state('gmail', self.identity, state)

    def _folder_sync_state(self, folder):
        return self._folder_sync.setdefault(folder, {'messages': [], 'history_id': ''})

    def _folder_cached_messages(self, folder):
        with self._sync_lock:
            if folder == 'INBOX':
                return list(self._cached_inbox_messages)
            return list(self._folder_sync_state(folder).get('messages', []))

    def _folder_history_id(self, folder):
        with self._sync_lock:
            if folder == 'INBOX':
                return self._inbox_history_id
            return self._folder_sync_state(folder).get('history_id') or ''

    def _update_folder_sync_state(self, folder, messages=None, history_id=None):
        with self._sync_lock:
            if folder == 'INBOX':
                if messages is not None:
                    ordered = sorted(
                        list(messages or []),
                        key=lambda item: item.get('date') or datetime.now(timezone.utc),
                        reverse=True,
                    )
                    self._cached_inbox_messages = ordered[:_SYNC_RECENT_MESSAGES_LIMIT]
                if history_id is not None:
                    self._inbox_history_id = history_id
            else:
                folder_state = self._folder_sync_state(folder)
                if messages is not None:
                    ordered = sorted(
                        list(messages or []),
                        key=lambda item: item.get('date') or datetime.now(timezone.utc),
                        reverse=True,
                    )
                    folder_state['messages'] = ordered[:_SYNC_RECENT_MESSAGES_LIMIT]
                if history_id is not None:
                    folder_state['history_id'] = history_id
        self._persist_sync_state()

    def _update_inbox_sync_state(self, messages=None, history_id=None):
        self._update_folder_sync_state('INBOX', messages=messages, history_id=history_id)

    def update_cached_message_read_state(self, folder, uid, is_read):
        changed = False
        with self._sync_lock:
            if folder == 'INBOX':
                messages = self._cached_inbox_messages
            else:
                messages = self._folder_sync_state(folder).get('messages', [])
            for msg in messages:
                if msg.get('uid') != uid:
                    continue
                msg['is_read'] = bool(is_read)
                changed = True
                break
        if changed:
            self._persist_sync_state()
        return changed

    def remove_cached_message(self, folder, uid):
        removed = False
        with self._sync_lock:
            if folder == 'INBOX':
                before = len(self._cached_inbox_messages)
                self._cached_inbox_messages = [msg for msg in self._cached_inbox_messages if msg.get('uid') != uid]
                removed = len(self._cached_inbox_messages) != before
            else:
                folder_state = self._folder_sync_state(folder)
                before = len(folder_state.get('messages', []))
                folder_state['messages'] = [msg for msg in folder_state.get('messages', []) if msg.get('uid') != uid]
                removed = len(folder_state['messages']) != before
        if removed:
            self._persist_sync_state()
        return removed

    def _gmail_api_request(self, path, query=None, method='GET', data=None, headers=None):
        ensure_network_ready()
        token = self._token()
        url = f'https://gmail.googleapis.com/gmail/v1{path}'
        if query:
            url += '?' + urllib.parse.urlencode(query, doseq=True)
        request_headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
        }
        if headers:
            request_headers.update(dict(headers))
        payload = None
        if data is not None:
            payload = json.dumps(data).encode('utf-8')
            request_headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(
            url,
            data=payload,
            headers=request_headers,
            method=str(method or 'GET').upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=_GMAIL_API_TIMEOUT_SECS) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._invalidate_token()
            raise

    def _gmail_api_message_id(self, value):
        text = str(value or '').strip()
        if not text:
            return ''
        if text.lower().startswith('0x'):
            text = text[2:]
        try:
            return format(int(text), 'x')
        except ValueError:
            return text

    def _gmail_api_label_id_for_folder(self, folder):
        label_name = self._gmail_label_name_for_folder(folder)
        if not label_name:
            return None
        if label_name in self._PARTIAL_SYNC_LABEL_IDS:
            return label_name
        label = self._gmail_labels().get(label_name)
        if label:
            return label.get('id') or None
        return None

    def _gmail_api_message_url(self, message_id):
        return f'/users/me/messages/{urllib.parse.quote(str(message_id), safe="")}'

    def _gmail_api_thread_url(self, thread_id):
        return f'/users/me/threads/{urllib.parse.quote(str(thread_id), safe="")}'

    def _gmail_api_attachment_url(self, message_id, attachment_id):
        return (
            f'/users/me/messages/{urllib.parse.quote(str(message_id), safe="")}'
            f'/attachments/{urllib.parse.quote(str(attachment_id), safe="")}'
        )

    def _gmail_api_decode_bytes(self, value):
        if not value:
            return b''
        text = str(value).strip()
        if not text:
            return b''
        padding = '=' * ((4 - len(text) % 4) % 4)
        return base64.urlsafe_b64decode(text + padding)

    def _gmail_api_header_map(self, payload):
        headers = {}
        for header in (payload or {}).get('headers', []):
            name = str(header.get('name') or '').strip().lower()
            if not name:
                continue
            headers[name] = header.get('value', '')
        return headers

    def _gmail_api_message_body_date(self, resource, headers):
        date_value = headers.get('date', '')
        if date_value:
            try:
                return _aware_utc_datetime(email_parser.utils.parsedate_to_datetime(date_value))
            except Exception:
                pass
        internal_ms = resource.get('internalDate')
        try:
            return datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _gmail_api_part_is_attachment(self, part, headers):
        filename = str(part.get('filename') or '').strip()
        if filename:
            return True
        disposition = str(headers.get('content-disposition', '')).lower()
        if 'attachment' in disposition or 'inline' in disposition:
            return True
        body = part.get('body', {}) or {}
        if body.get('attachmentId'):
            return True
        return False

    def _gmail_api_extract_part(self, message_id, part, state):
        headers = self._gmail_api_header_map(part)
        mime_type = str(part.get('mimeType') or '').lower()
        body = part.get('body', {}) or {}
        filename = str(part.get('filename') or '').strip()
        content_id = headers.get('content-id', '')
        disposition = str(headers.get('content-disposition', '')).lower()
        data = body.get('data')
        text_payload = None
        if data:
            try:
                text_payload = self._gmail_api_decode_bytes(data).decode('utf-8', errors='replace')
            except Exception:
                text_payload = self._gmail_api_decode_bytes(data).decode('latin-1', errors='replace')

        if mime_type == 'text/html' and state['html'] is None and not self._gmail_api_part_is_attachment(part, headers):
            state['html'] = text_payload or ''
        elif mime_type == 'text/plain' and state['text'] is None and not self._gmail_api_part_is_attachment(part, headers):
            state['text'] = text_payload or ''

        if self._gmail_api_part_is_attachment(part, headers):
            attachment_data = b''
            if body.get('attachmentId'):
                try:
                    attachment = self._gmail_api_request(
                        self._gmail_api_attachment_url(message_id, body.get('attachmentId'))
                    )
                    attachment_data = self._gmail_api_decode_bytes(attachment.get('data', ''))
                except Exception:
                    attachment_data = b''
            elif body.get('data'):
                attachment_data = self._gmail_api_decode_bytes(body.get('data'))
            state['attachments'].append({
                'name': _decode_str(filename) or 'attachment',
                'size': int(body.get('size') or len(attachment_data)),
                'content_type': part.get('mimeType', 'application/octet-stream'),
                'disposition': 'inline' if 'inline' in disposition or content_id else 'attachment',
                'content_id': content_id,
                'data': attachment_data,
            })
            return

        for child in part.get('parts', []) or []:
            self._gmail_api_extract_part(message_id, child, state)

    def _gmail_api_message_to_row(self, resource, folder='INBOX', uid=None):
        payload = resource.get('payload') or {}
        headers = self._gmail_api_header_map(payload)
        from_ = _decode_str(headers.get('from', ''))
        sender_name, sender_email = email_parser.utils.parseaddr(from_)
        if not sender_name:
            sender_name = sender_email
        api_id = str(resource.get('id') or '').strip()
        row = {
            'uid': uid or api_id,
            'subject': _decode_str(headers.get('subject', '(no subject)')),
            'sender_name': sender_name or sender_email or 'Unknown',
            'sender_email': sender_email,
            'to_addrs': _parse_addrs(_decode_str(headers.get('to', ''))),
            'cc_addrs': _parse_addrs(_decode_str(headers.get('cc', ''))),
            'date': self._gmail_api_message_body_date(resource, headers),
            'is_read': 'UNREAD' not in set(resource.get('labelIds', [])),
            'has_attachments': False,
            'snippet': resource.get('snippet', ''),
            'folder': folder,
            'backend': 'gmail',
            'account': self.identity,
            'backend_obj': self,
            'thread_id': _gmail_api_id_to_imap_id(resource.get('threadId')),
            'thread_source': 'gmail-api',
            'message_id': _decode_str(headers.get('message-id', '')),
            'gmail_msgid': _gmail_api_id_to_imap_id(api_id),
        }
        if payload.get('parts'):
            state = {'html': None, 'text': None, 'attachments': []}
            self._gmail_api_extract_part(api_id, payload, state)
            row['has_attachments'] = bool(state['attachments'])
        else:
            row['has_attachments'] = bool(resource.get('payload', {}).get('body', {}).get('attachmentId'))
        return row

    def _seed_special_folders_from_labels(self, labels_by_name):
        logical_to_actual = {
            'INBOX': None,
            '[Gmail]/Sent Mail': None,
            '[Gmail]/Drafts': None,
            '[Gmail]/Trash': None,
            '[Gmail]/Spam': None,
        }
        by_id = {}
        for name, label in (labels_by_name or {}).items():
            label_id = str((label or {}).get('id') or '').strip()
            if label_id:
                by_id[label_id] = name
        for logical_name, label_id in self._GMAIL_SYSTEM_LABELS.items():
            actual_name = by_id.get(label_id)
            if actual_name:
                logical_to_actual[logical_name] = actual_name
        with self._sync_lock:
            self._special_folders = {
                logical_name: actual_name
                for logical_name, actual_name in logical_to_actual.items()
                if actual_name
            }

    def _gmail_api_message_resource(self, message_id):
        return self._gmail_api_request(
            self._gmail_api_message_url(message_id),
            query={'format': 'full'},
        )

    def _gmail_api_thread_resource(self, thread_id):
        return self._gmail_api_request(
            self._gmail_api_thread_url(thread_id),
            query={'format': 'full'},
        )

    def _gmail_api_label_count(self, label_id):
        data = self._gmail_api_request(f'/users/me/labels/{urllib.parse.quote(str(label_id), safe="")}')
        try:
            return int(data.get('messagesUnread') or 0)
        except Exception:
            return 0

    def _gmail_api_modify_message(self, message_id, add_label_ids=None, remove_label_ids=None):
        payload = {}
        if add_label_ids:
            payload['addLabelIds'] = list(dict.fromkeys(add_label_ids))
        if remove_label_ids:
            payload['removeLabelIds'] = list(dict.fromkeys(remove_label_ids))
        return self._gmail_api_request(self._gmail_api_message_url(message_id) + '/modify', method='POST', data=payload)

    def _gmail_cached_message_by_uid(self, folder, uid):
        uid_text = str(uid or '').strip()
        if not uid_text:
            return None
        folders = []
        if folder:
            folders.append(folder)
        if 'INBOX' not in folders:
            folders.append('INBOX')
        with self._sync_lock:
            folders.extend(
                folder_name
                for folder_name in self._folder_sync
                if folder_name not in folders and folder_name != 'INBOX'
            )
            inbox_messages = list(self._cached_inbox_messages)
            folder_messages = {
                folder_name: list(state.get('messages', []))
                for folder_name, state in self._folder_sync.items()
            }
        for folder_name in folders:
            messages = inbox_messages if folder_name == 'INBOX' else folder_messages.get(folder_name, [])
            for msg in messages:
                if str(msg.get('uid') or '').strip() == uid_text:
                    return dict(msg)
        return None

    def _gmail_api_message_id_for_uid(self, folder, uid):
        cached = self._gmail_cached_message_by_uid(folder, uid)
        if cached:
            api_id = self._gmail_api_message_id(cached.get('gmail_msgid'))
            if api_id:
                return api_id
        uid_text = str(uid or '').strip()
        if not uid_text:
            return ''
        if uid_text.startswith('0x') or re.search(r'[a-fA-F]', uid_text):
            return self._gmail_api_message_id(uid_text)
        return ''

    def _gmail_api_folder_for_labels(self, label_ids):
        labels = set(label_ids or [])
        if 'INBOX' in labels:
            return 'INBOX'
        if 'SENT' in labels:
            return '[Gmail]/Sent Mail'
        if 'DRAFT' in labels:
            return '[Gmail]/Drafts'
        if 'TRASH' in labels:
            return '[Gmail]/Trash'
        if 'SPAM' in labels:
            return '[Gmail]/Spam'
        return '[Gmail]/All Mail'

    def _gmail_api_body_for_message(self, api_id):
        resource = self._gmail_api_message_resource(api_id)
        payload = resource.get('payload') or {}
        state = {'html': None, 'text': None, 'attachments': []}
        self._gmail_api_extract_part(api_id, payload, state)
        return state['html'], state['text'], state['attachments']

    def _gmail_api_fetch_thread_messages(self, thread_id):
        api_thread_id = self._gmail_api_message_id(thread_id)
        if not api_thread_id:
            return []
        resource = self._gmail_api_thread_resource(api_thread_id)
        cached_by_api_id = {}
        with self._sync_lock:
            folders = ['INBOX'] + [folder for folder in self._folder_sync if folder != 'INBOX']
            inbox_messages = list(self._cached_inbox_messages)
            folder_messages = {
                folder_name: list(state.get('messages', []))
                for folder_name, state in self._folder_sync.items()
            }
        for folder_name in folders:
            messages = inbox_messages if folder_name == 'INBOX' else folder_messages.get(folder_name, [])
            for msg in messages:
                gmail_msgid = self._gmail_api_message_id(msg.get('gmail_msgid'))
                if gmail_msgid:
                    cached_by_api_id[gmail_msgid] = msg
        messages = []
        for api_message in resource.get('messages', []):
            api_id = str(api_message.get('id') or '').strip()
            if not api_id:
                continue
            cached = cached_by_api_id.get(api_id)
            row = self._gmail_api_message_to_row(
                api_message,
                folder=self._gmail_api_folder_for_labels(api_message.get('labelIds', [])),
                uid=(cached.get('uid') if cached else api_id),
            )
            if cached and cached.get('thread_id'):
                row['thread_id'] = cached.get('thread_id')
            messages.append(row)
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')))
        return messages

    def _gmail_list_memo_entry(self, folder):
        with self._gmail_list_memo_lock:
            entry = self._gmail_list_memo.get(folder)
            if entry is None:
                entry = {
                    'refs': [],
                    'next_token': None,
                    'exhausted': False,
                    'metadata_cache': {},
                }
                self._gmail_list_memo[folder] = entry
            return entry

    def _gmail_list_memo_reset(self, folder=None):
        with self._gmail_list_memo_lock:
            if folder is None:
                self._gmail_list_memo.clear()
            else:
                self._gmail_list_memo.pop(folder, None)

    def _gmail_api_fetch_messages(self, folder='INBOX', limit=50):
        label_id = self._gmail_api_label_id_for_folder(folder)
        if not label_id:
            return None
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []

        memo = self._gmail_list_memo_entry(folder)
        page_size = min(max(limit, 20), 100)
        query_base = {
            'labelIds': [label_id],
            'maxResults': page_size,
            'fields': 'messages(id),nextPageToken',
        }
        while len(memo['refs']) < limit and not memo['exhausted']:
            query = dict(query_base)
            if memo['next_token']:
                query['pageToken'] = memo['next_token']
            data = self._gmail_api_request('/users/me/messages', query=query)
            refs = data.get('messages') or []
            for ref in refs:
                api_id = str((ref or {}).get('id') or '').strip()
                if api_id and api_id not in memo['metadata_cache']:
                    memo['refs'].append(api_id)
            next_token = data.get('nextPageToken')
            memo['next_token'] = next_token
            if not next_token or not refs:
                memo['exhausted'] = True
                break

        api_ids = list(memo['refs'][:limit])
        if not api_ids:
            return []

        missing = [api_id for api_id in api_ids if api_id not in memo['metadata_cache']]
        if missing:
            fetched = self._gmail_batch_get_metadata(missing)
            for api_id, api_message in fetched.items():
                if not api_message:
                    continue
                row = self._gmail_api_message_to_row(
                    api_message,
                    folder=folder,
                    uid=api_id,
                )
                memo['metadata_cache'][api_id] = row

        messages = [memo['metadata_cache'][api_id] for api_id in api_ids if api_id in memo['metadata_cache']]
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')), reverse=True)
        return messages[:limit]

    def _gmail_imap_ssl_context(self):
        if self._imap_accept_ssl_errors:
            return ssl._create_unverified_context()
        return ssl.create_default_context()

    def _gmail_imap_folder_name(self, folder):
        folder_text = str(folder or '').strip() or 'INBOX'
        with self._sync_lock:
            for logical_name, actual_name in self._special_folders.items():
                if folder_text == logical_name:
                    return actual_name
                logical_folder = self._GMAIL_SYSTEM_LABELS.get(logical_name)
                if logical_folder and folder_text == logical_folder:
                    return actual_name
        return folder_text

    def _gmail_notice_folder_name(self, folder):
        folder_text = str(folder or '').strip() or 'Inbox'
        folder_text = folder_text.replace('[Gmail]/', '').replace('[Google Mail]/', '')
        if folder_text.upper() == 'INBOX':
            return 'Inbox'
        if folder_text.lower() == 'sent mail':
            return 'Sent Mail'
        if folder_text.lower() == 'drafts':
            return 'Drafts'
        if folder_text.lower() == 'trash':
            return 'Trash'
        if folder_text.lower() == 'spam':
            return 'Spam'
        return folder_text

    def _gmail_notice_for_exception(self, exc, folder):
        if isinstance(exc, OAuthTokenAcquisitionError):
            return classify_oauth_token_error(exc, fallback_detail='Sign-in needs attention', folder=self._gmail_notice_folder_name(folder))
        if isinstance(exc, urllib.error.HTTPError):
            return classify_http_error(exc, fallback_detail='Sync issue', folder=self._gmail_notice_folder_name(folder))
        return build_sync_notice('error', f'Could not load {self._gmail_notice_folder_name(folder)}', retryable=True)

    def _gmail_has_imap_fallback(self):
        return bool(getattr(self, '_allow_imap_fallback', False))

    def _gmail_degraded_route(self):
        return 'fallback' if self._gmail_has_imap_fallback() else 'primary'

    def _gmail_health_state(self):
        health = getattr(self, '_sync_health', None)
        if health is None:
            health = SyncHealthState(
                provider='gmail',
                account=getattr(self, 'identity', ''),
                primary_label='Gmail API',
                fallback_label='IMAP' if self._gmail_has_imap_fallback() else 'Unavailable',
            )
            self._sync_health = health
        return health

    def get_sync_health(self):
        return self._gmail_health_state().as_sidebar_status()

    def get_sync_policy(self):
        if self._gmail_has_imap_fallback():
            return build_sync_policy(
                'gmail',
                'Gmail API history and message fetch',
                'IMAP fallback with timed API recovery',
                'Gmail history refresh plus IMAP unread recount',
                notes='Primary Gmail API route probes again on a timer. IMAP is temporary fallback only.',
            )
        return build_sync_policy(
            'gmail',
            'Gmail API history and message fetch',
            'No transport fallback',
            'Gmail API history refresh',
            notes='Native Gmail uses the Gmail API only. When Google authorization degrades, cached state is shown until the next probe succeeds.',
        )

    def get_unread_count_policy(self, folder='INBOX', force_primary=False):
        health = self._gmail_health_state()
        route = 'primary'
        source = 'gmail-api-label-count'
        if self._gmail_has_imap_fallback() and not force_primary and (health.state != 'ready' or health.route != 'primary'):
            route = 'fallback'
            source = 'imap-unseen'
        return build_count_policy(
            'gmail',
            'Gmail API unread label count',
            'IMAP UNSEEN unread count' if self._gmail_has_imap_fallback() else 'No fallback',
            'Gmail API label count after history reconciliation',
            route=route,
            source=source,
            notes=(
                'Primary Gmail counts use the API while healthy; fallback counts use IMAP when Gmail is degraded.'
                if self._gmail_has_imap_fallback()
                else 'Native Gmail counts use the Gmail API directly and keep the last cached value while authorization recovers.'
            ),
        )

    def _gmail_probe_api_now(self):
        return self._gmail_health_state().should_probe_primary()

    def _gmail_probe_api_for_counts(self, force=False):
        if force:
            return True
        health = self._gmail_health_state()
        return health.state == 'ready' and health.should_probe_primary()

    def force_primary_probe(self):
        health = self._gmail_health_state()
        if health.route != 'primary' or not health.should_probe_primary():
            health.retryable = True
            health.retry_after_at = 0.0
            health.retry_after_seconds = 0
            if health.state == 'error':
                health.state = 'warning'
            self._sync_health = health
        self._gmail_api_available = None
        return health

    def _gmail_mark_api_ready(self, detail='Ready'):
        self._gmail_health_state().mark_ready(detail)
        self._gmail_api_available = True
        self._gmail_last_health_event = None

    def _gmail_log_health_event(self, level, kind, detail, *, code='', route='fallback', retryable=False):
        event_key = (
            str(level or '').strip().lower(),
            str(kind or '').strip().lower(),
            str(detail or '').strip(),
            str(code or '').strip(),
            str(route or '').strip().lower(),
            bool(retryable),
        )
        if getattr(self, '_gmail_last_health_event', None) == event_key:
            return
        self._gmail_last_health_event = event_key
        try:
            log_event(
                kind,
                level=level,
                message=detail,
                context={
                    'provider': 'gmail',
                    'account': self.identity,
                    'route': route,
                    'code': code,
                    'retryable': bool(retryable),
                },
                persist=True,
            )
        except Exception:
            pass

    def _gmail_mark_api_fallback(self, detail, *, tooltip='', code='', retryable=True, retry_after_seconds=None):
        detail_text = str(detail or '').strip() or 'Gmail API unavailable'
        if detail_text.lower().startswith('using imap for '):
            suffix = detail_text[len('Using IMAP for '):].strip()
            detail_text = f'Gmail API unavailable for {suffix}' if suffix else 'Gmail API unavailable'
        route = self._gmail_degraded_route()
        self._gmail_health_state().mark_warning(
            detail_text,
            tooltip=tooltip,
            code=code,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            route=route,
        )
        self._gmail_api_available = False
        self._gmail_log_health_event(
            'warning',
            'gmail-api-fallback',
            detail_text,
            code=code,
            route=route,
            retryable=retryable,
        )

    def _gmail_mark_api_error(self, detail, *, tooltip='', code='', retryable=False, retry_after_seconds=None):
        route = self._gmail_degraded_route()
        self._gmail_health_state().mark_error(
            detail,
            tooltip=tooltip,
            code=code,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            route=route,
        )
        self._gmail_api_available = False
        self._gmail_log_health_event(
            'error',
            'gmail-api-error',
            detail,
            code=code,
            route=route,
            retryable=retryable,
        )

    def _gmail_retry_delay_for_exception(self, exc):
        if isinstance(exc, urllib.error.HTTPError):
            return retry_delay_for_http_error(exc, default=60, maximum=900)
        text = str(exc).lower()
        if 'timed out' in text or 'temporarily unavailable' in text or 'connection reset' in text:
            return 60
        if 'permission denied' in text or 'unauthorized' in text:
            return 900
        return 180

    def _gmail_mark_retrying(self, notice, exc, folder, fallback_detail):
        folder_name = self._gmail_notice_folder_name(folder)
        retry_after = self._gmail_retry_delay_for_exception(exc)
        code = str((notice or {}).get('code') or '')
        tooltip = str((notice or {}).get('detail') or '').strip()
        retry_text = f'Retrying Gmail API in {max(1, retry_after // 60) if retry_after >= 60 else retry_after}s.'
        if self._gmail_has_imap_fallback():
            route_text = 'Reading through IMAP for now.'
        else:
            route_text = 'Keeping cached Gmail data for now.'
        tooltip = f'{tooltip} {route_text} {retry_text}'.strip() if tooltip else f'{route_text} {retry_text}'
        self._gmail_mark_api_fallback(
            fallback_detail or f'Gmail API unavailable for {folder_name}',
            tooltip=tooltip,
            code=code,
            retryable=True,
            retry_after_seconds=retry_after,
        )

    @contextlib.contextmanager
    def _gmail_imap_session(self):
        ensure_network_ready()
        if not self._imap_host:
            raise RuntimeError('IMAP host unavailable')
        auth_token = self._token()
        auth_str = f'user={self._imap_user}\x01auth=Bearer {auth_token}\x01\x01'
        context = self._gmail_imap_ssl_context()
        port = 993 if self._imap_use_ssl else 143
        if self._imap_use_ssl:
            imap = imaplib.IMAP4_SSL(self._imap_host, port, ssl_context=context)
        else:
            imap = imaplib.IMAP4(self._imap_host, port)
            if self._imap_use_tls:
                imap.starttls(ssl_context=context)
        imap.authenticate('XOAUTH2', lambda _challenge: auth_str.encode('utf-8'))
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

    def _gmail_imap_decode_message(self, raw_bytes):
        return BytesParser(policy=email_policy.default).parsebytes(raw_bytes or b'')

    def _gmail_imap_message_headers(self, message):
        headers = {}
        for name in ('from', 'to', 'cc', 'subject', 'date', 'message-id', 'content-type'):
            headers[name] = _decode_str(message.get(name, ''))
        return headers

    def _gmail_imap_message_date(self, message, headers):
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

    def _gmail_imap_extract_part(self, part, state):
        headers = {}
        for name in ('content-id', 'content-disposition'):
            headers[name] = _decode_str(part.get(name, ''))
        mime_type = str(part.get_content_type() or '').lower()
        filename = str(part.get_filename() or '').strip()
        disposition = str(part.get_content_disposition() or '').strip().lower()
        payload = part.get_payload(decode=True) or b''
        text_payload = None
        if payload:
            try:
                text_payload = payload.decode('utf-8', errors='replace')
            except Exception:
                text_payload = payload.decode('latin-1', errors='replace')

        if mime_type == 'text/html' and state['html'] is None and not filename and disposition != 'attachment':
            state['html'] = text_payload or ''
        elif mime_type == 'text/plain' and state['text'] is None and not filename and disposition != 'attachment':
            state['text'] = text_payload or ''

        is_attachment = bool(filename or disposition == 'attachment' or part.get('Content-ID'))
        if is_attachment:
            content_id = _decode_str(part.get('Content-ID', '')).strip().strip('<>')
            state['attachments'].append({
                'name': filename or 'attachment',
                'size': len(payload),
                'content_type': mime_type or 'application/octet-stream',
                'disposition': 'inline' if disposition == 'inline' or content_id else 'attachment',
                'content_id': content_id,
                'data': payload,
            })
            return

        for child in part.get_payload() or []:
            if getattr(child, 'is_multipart', None) and child.is_multipart():
                self._gmail_imap_extract_part(child, state)
            elif hasattr(child, 'get_content_type'):
                self._gmail_imap_extract_part(child, state)

    def _gmail_imap_message_to_row(self, uid, message, folder='INBOX', flags=None, gmail_msgid=''):
        headers = self._gmail_imap_message_headers(message)
        from_ = _decode_str(headers.get('from', ''))
        sender_name, sender_email = email_parser.utils.parseaddr(from_)
        if not sender_name:
            sender_name = sender_email
        content_type = headers.get('content-type', '').lower()
        message_id = headers.get('message-id', '')
        is_read = '\\seen' not in set(flag.lower() for flag in (flags or []))
        row = {
            'uid': str(uid),
            'subject': headers.get('subject', '(no subject)') or '(no subject)',
            'sender_name': sender_name or sender_email or 'Unknown sender',
            'sender_email': sender_email,
            'to_addrs': _parse_addrs(headers.get('to', '')),
            'cc_addrs': _parse_addrs(headers.get('cc', '')),
            'date': self._gmail_imap_message_date(message, headers),
            'is_read': is_read,
            'has_attachments': 'multipart' in content_type or 'attachment' in content_type,
            'snippet': '',
            'folder': folder,
            'backend': 'gmail',
            'account': self.identity,
            'backend_obj': self,
            'thread_id': message_id or str(uid),
            'thread_source': 'gmail-imap',
            'message_id': message_id,
            'gmail_msgid': str(gmail_msgid or '').strip(),
        }
        if message.is_multipart():
            state = {'html': None, 'text': None, 'attachments': []}
            for part in message.walk():
                if part.is_multipart():
                    continue
                self._gmail_imap_extract_part(part, state)
            row['has_attachments'] = bool(state['attachments'])
        return row

    def _gmail_imap_fetch_header_messages(self, folder, uids):
        rows = []
        if not uids:
            return rows
        folder_name = self._gmail_imap_folder_name(folder)
        uid_set = ','.join(str(uid) for uid in uids if str(uid).strip())
        if not uid_set:
            return rows
        with self._gmail_imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return rows
            status, data = imap.uid('fetch', uid_set, '(UID FLAGS X-GM-MSGID BODY.PEEK[HEADER])')
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
                gmail_msgid_match = re.search(rb'X-GM-MSGID\s+(\d+)', meta or b'')
                gmail_msgid = gmail_msgid_match.group(1).decode('ascii', errors='ignore') if gmail_msgid_match else ''
                flags = {flag.decode('ascii', errors='ignore').lower() for flag in re.findall(rb'\\[A-Za-z]+', meta or b'')}
                message = self._gmail_imap_decode_message(raw)
                rows.append(self._gmail_imap_message_to_row(uid, message, folder_name, flags=flags, gmail_msgid=gmail_msgid))
        rows.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        return rows

    def _gmail_imap_folder_messages(self, folder, limit):
        folder_name = self._gmail_imap_folder_name(folder)
        with self._gmail_imap_session() as imap:
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
        return self._gmail_imap_fetch_header_messages(folder_name, target_uids)

    def _gmail_imap_fetch_messages(self, folder='INBOX', limit=50):
        try:
            return self._gmail_imap_folder_messages(folder, limit)
        except Exception as exc:
            self._set_sync_notice(build_sync_notice('error', f'Could not load {folder}', retryable=False))
            raise

    def _gmail_imap_unread_count(self, folder='INBOX'):
        folder_name = self._gmail_imap_folder_name(folder)
        with self._gmail_imap_session() as imap:
            status, _selected = imap.select(folder_name, readonly=True)
            if status != 'OK':
                return 0
            status, data = imap.uid('search', None, 'UNSEEN')
            if status != 'OK' or not data:
                return 0
            return len((data[0] or b'').split())

    def _gmail_profile(self):
        return self._gmail_api_request('/users/me/profile')

    def _gmail_labels(self, force=False):
        with self._sync_lock:
            if self._gmail_labels_by_name is not None and not force:
                return dict(self._gmail_labels_by_name)
        data = self._gmail_api_request('/users/me/labels')
        labels_by_name = {}
        for label in data.get('labels', []):
            name = str(label.get('name') or '')
            label_id = str(label.get('id') or '')
            if not name or not label_id:
                continue
            labels_by_name[name] = {
                'id': label_id,
                'type': label.get('type', ''),
            }
        with self._sync_lock:
            self._gmail_labels_by_name = labels_by_name
            self._gmail_labels_loaded_at = _utcnow_iso()
            self._seed_special_folders_from_labels(labels_by_name)
            return dict(self._gmail_labels_by_name)

    def _gmail_label_name_for_folder(self, folder):
        if not folder:
            return None
        folder_text = str(folder)
        if folder_text in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[folder_text]
        with self._sync_lock:
            for logical_name, actual_name in self._special_folders.items():
                if folder_text == actual_name or folder_text == _decode_imap_utf7(actual_name):
                    return self._GMAIL_SYSTEM_LABELS.get(logical_name)
        label = self._gmail_labels().get(folder_text)
        if label is not None:
            return folder_text
        decoded = _decode_imap_utf7(folder_text)
        if decoded != folder_text and self._gmail_labels().get(decoded) is not None:
            return decoded
        if folder_text.startswith('[Gmail]/') or folder_text.startswith('[Google Mail]/'):
            return None
        return folder_text

    def gmail_label_for_folder(self, folder):
        label_name = self._gmail_label_name_for_folder(folder)
        if not label_name:
            return None
        if label_name in {'INBOX', 'SENT', 'DRAFT', 'TRASH', 'SPAM'}:
            return {'id': label_name, 'name': label_name, 'type': 'system'}
        label = self._gmail_labels().get(label_name)
        if label is None:
            return None
        return {
            'id': label.get('id'),
            'name': label_name,
            'type': label.get('type', ''),
        }

    def _gmail_partial_sync_label(self, folder):
        label_name = self._gmail_label_name_for_folder(folder)
        if label_name in self._PARTIAL_SYNC_LABEL_IDS:
            return {'id': label_name, 'name': label_name, 'type': 'system'}
        return None

    def _gmail_history_probe(self, start_history_id, label_id='INBOX'):
        latest_history_id = start_history_id
        page_token = None
        actions = {}
        new_ids = set()
        while True:
            query = {
                'startHistoryId': start_history_id,
                'labelId': label_id,
                'maxResults': 100,
                'historyTypes': ['messageAdded', 'messageDeleted', 'labelAdded', 'labelRemoved'],
            }
            if page_token:
                query['pageToken'] = page_token
            data = self._gmail_api_request('/users/me/history', query=query)
            for entry in data.get('history', []):
                self._apply_history_actions(actions, entry, label_id=label_id, new_ids=new_ids)
            latest_history_id = data.get('historyId') or latest_history_id
            page_token = data.get('nextPageToken')
            if not page_token:
                break
        refresh_map = {
            msgid: action['api_id']
            for msgid, action in actions.items()
            if action.get('action') == 'refresh'
        }
        remove_ids = {
            msgid
            for msgid, action in actions.items()
            if action.get('action') == 'remove'
        }
        return {
            'changed': bool(refresh_map or remove_ids),
            'history_id': latest_history_id,
            'refresh_map': refresh_map,
            'remove_ids': remove_ids,
            'new_ids': new_ids,
        }

    def _apply_history_actions(self, actions, entry, label_id='INBOX', new_ids=None):
        def mark(items, action, required_label=None, mark_new=False):
            for item in items or []:
                api_id = str(item.get('message', {}).get('id') or '').strip()
                gmail_msgid = _gmail_api_id_to_imap_id(api_id)
                if not gmail_msgid:
                    continue
                label_ids = set(item.get('labelIds', []))
                if required_label and required_label not in label_ids:
                    continue
                if mark_new and new_ids is not None:
                    new_ids.add(gmail_msgid)
                actions[gmail_msgid] = {
                    'api_id': api_id,
                    'action': action,
                }

        mark(entry.get('labelsAdded'), 'refresh', required_label='UNREAD')
        mark(entry.get('labelsRemoved'), 'refresh', required_label='UNREAD')
        mark(entry.get('messagesAdded'), 'refresh')
        mark(entry.get('messagesAdded'), 'refresh', required_label=label_id, mark_new=True)
        mark(entry.get('labelsAdded'), 'refresh', required_label=label_id)
        mark(entry.get('labelsRemoved'), 'remove', required_label=label_id)
        mark(entry.get('messagesDeleted'), 'remove')

    def _seed_gmail_history_state(self, folder='INBOX'):
        try:
            profile = self._gmail_profile()
            history_id = profile.get('historyId')
            if history_id:
                self._gmail_history_supported = True
                self._update_folder_sync_state(folder, history_id=history_id)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                self._gmail_history_supported = False
        except Exception:
            pass
        finally:
            with self._sync_lock:
                self._gmail_history_seed_inflight.discard(folder)

    def _ensure_gmail_history_seed_async(self, folder='INBOX'):
        if self._gmail_history_supported is False:
            return
        with self._sync_lock:
            if folder in self._gmail_history_seed_inflight:
                return
            self._gmail_history_seed_inflight.add(folder)
        threading.Thread(target=self._seed_gmail_history_state, args=(folder,), daemon=True).start()

    def _probe_cached_folder_messages(self, folder, label_id):
        cached_messages = self._folder_cached_messages(folder)
        history_id = self._folder_history_id(folder)
        if not cached_messages or not history_id or self._gmail_history_supported is False:
            return None
        try:
            history = self._gmail_history_probe(history_id, label_id=label_id)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self._update_folder_sync_state(folder, history_id='')
                return {'status': 'reset'}
            if exc.code in (401, 403):
                self._gmail_history_supported = False
                return {'status': 'unsupported'}
            raise
        if not history.get('changed'):
            self._gmail_history_supported = True
            self._update_folder_sync_state(folder, history_id=history.get('history_id'))
            return {'status': 'unchanged', 'messages': cached_messages}
        self._gmail_history_supported = True
        return {
            'status': 'changed',
            'history_id': history.get('history_id'),
            'refresh_map': history.get('refresh_map', {}),
            'remove_ids': history.get('remove_ids', set()),
            'new_ids': history.get('new_ids', set()),
        }

    def _probe_cached_inbox_messages(self):
        return self._probe_cached_folder_messages('INBOX', 'INBOX')

    def _gmail_message_metadata(self, api_id):
        return self._gmail_api_request(
            f'/users/me/messages/{urllib.parse.quote(str(api_id), safe="")}',
            query={'format': 'metadata', 'metadataHeaders': _GMAIL_METADATA_HEADERS},
        )

    def _gmail_api_service(self):
        """Lazy-build the googleapiclient Gmail service for this backend."""
        service = self._gmail_service
        if service is not None:
            return service
        with self._gmail_service_lock:
            if self._gmail_service is not None:
                return self._gmail_service
            ensure_network_ready()
            # Prime the token cache so the first SDK call has a live token.
            self._token()
            credentials = _HermodGmailCredentials(self)
            self._gmail_service = _gapi_build(
                'gmail',
                'v1',
                credentials=credentials,
                cache_discovery=False,
            )
            return self._gmail_service

    def _gmail_batch_get_metadata(self, api_ids):
        """Fetch metadata for many Gmail message IDs in one batch round-trip.

        Returns a ``{api_id: message_dict}`` mapping. Missing entries mean the
        per-message sub-request failed — callers should treat those as
        skipped rather than raising.
        """
        ids = [str(x or '').strip() for x in (api_ids or []) if x]
        ids = [x for x in ids if x]
        if not ids:
            return {}
        service = self._gmail_api_service()
        results = {}
        pending = list(ids)
        # One retry pass for sub-requests that transiently fail.
        for attempt in (0, 1):
            failures = []
            auth_failed = False

            def _make_callback(aid, failures_list):
                def _cb(request_id, response, exception):
                    nonlocal auth_failed
                    if exception is not None:
                        status = getattr(exception, 'status_code', None)
                        if status is None and isinstance(exception, _GapiHttpError):
                            status = exception.resp.status if exception.resp else None
                        if status in (401, 403):
                            auth_failed = True
                        failures_list.append(aid)
                        return
                    if response:
                        results[aid] = response
                return _cb

            messages_api = service.users().messages()
            for start in range(0, len(pending), _GMAIL_BATCH_MAX):
                chunk = pending[start:start + _GMAIL_BATCH_MAX]
                batch = service.new_batch_http_request()
                for aid in chunk:
                    batch.add(
                        messages_api.get(
                            userId='me',
                            id=aid,
                            format='metadata',
                            metadataHeaders=_GMAIL_METADATA_HEADERS,
                        ),
                        callback=_make_callback(aid, failures),
                    )
                try:
                    batch.execute()
                except _GapiHttpError as exc:
                    # Whole batch failed. If it's an auth error, refresh and
                    # retry once; otherwise surface via standard notice path.
                    status = exc.resp.status if exc.resp else None
                    if status in (401, 403) and attempt == 0:
                        self._invalidate_token()
                        failures = list(chunk) + failures
                        continue
                    raise
                except Exception:
                    # Unknown whole-batch failure — log and give the retry
                    # loop a chance to pick remaining ids up serially.
                    failures.extend(chunk)

            if not failures:
                break
            if auth_failed and attempt == 0:
                self._invalidate_token()
            pending = failures
        return results

    def _gmail_header_map(self, api_message):
        headers = {}
        for header in (((api_message or {}).get('payload') or {}).get('headers') or []):
            name = str(header.get('name') or '').strip().lower()
            if not name:
                continue
            headers[name] = header.get('value', '')
        return headers

    def _gmail_date_from_api(self, api_message, headers):
        date_value = headers.get('date', '')
        if date_value:
            try:
                return _aware_utc_datetime(email_parser.utils.parsedate_to_datetime(date_value))
            except Exception:
                pass
        internal_ms = (api_message or {}).get('internalDate')
        try:
            return datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _gmail_message_from_api_metadata(self, api_message, uid, folder='INBOX'):
        headers = self._gmail_header_map(api_message)
        from_ = _decode_str(headers.get('from', ''))
        sender_name, sender_email = email_parser.utils.parseaddr(from_)
        if not sender_name:
            sender_name = sender_email
        content_type = headers.get('content-type', '').lower()
        api_id = str((api_message or {}).get('id') or '').strip()
        return {
            'uid': uid,
            'subject': _decode_str(headers.get('subject', '(no subject)')),
            'sender_name': sender_name or sender_email,
            'sender_email': sender_email,
            'to_addrs': _parse_addrs(_decode_str(headers.get('to', ''))),
            'cc_addrs': _parse_addrs(_decode_str(headers.get('cc', ''))),
            'date': self._gmail_date_from_api(api_message, headers),
            'is_read': 'UNREAD' not in set((api_message or {}).get('labelIds', [])),
            'has_attachments': 'multipart/mixed' in content_type,
            'snippet': (api_message or {}).get('snippet', ''),
            'folder': folder,
            'backend': 'gmail',
            'account': self.identity,
            'backend_obj': self,
            'thread_id': _gmail_api_id_to_imap_id((api_message or {}).get('threadId')),
            'thread_source': 'gmail-api',
            'message_id': _decode_str(headers.get('message-id', '')),
            'gmail_msgid': _gmail_api_id_to_imap_id(api_id),
        }

    def _fetch_gmail_metadata_messages(self, refresh_map, folder='INBOX'):
        refreshed = {}
        if not refresh_map:
            return refreshed
        cached_by_msgid = {
            msg.get('gmail_msgid'): dict(msg)
            for msg in self._folder_cached_messages(folder)
            if msg.get('gmail_msgid')
        }
        api_ids = [api_id for api_id in refresh_map.values() if api_id]
        metadata_by_id = self._gmail_batch_get_metadata(api_ids)
        for gmail_msgid, api_id in refresh_map.items():
            api_message = metadata_by_id.get(api_id)
            if not api_message:
                continue
            cached = cached_by_msgid.get(gmail_msgid, {})
            uid = cached.get('uid') or api_id
            refreshed[gmail_msgid] = self._gmail_message_from_api_metadata(api_message, uid, folder)
        return refreshed

    def _top_up_cached_folder_messages(self, folder, current_messages, target_count):
        if target_count <= 0:
            return list(current_messages or [])
        current_messages = list(current_messages or [])
        known_msgids = {msg.get('gmail_msgid') for msg in current_messages if msg.get('gmail_msgid')}
        try:
            api_messages = self._gmail_api_fetch_messages(folder, target_count)
            if api_messages is not None:
                for extra in api_messages:
                    gmail_msgid = extra.get('gmail_msgid')
                    if gmail_msgid and gmail_msgid in known_msgids:
                        continue
                    current_messages.append(extra)
                    if gmail_msgid:
                        known_msgids.add(gmail_msgid)
                    if len(current_messages) >= target_count:
                        break
                current_messages.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
                return current_messages[:target_count]
        except Exception:
            pass
        current_messages.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        return current_messages[:target_count]

    def _top_up_cached_inbox_messages(self, current_messages, target_count):
        return self._top_up_cached_folder_messages('INBOX', current_messages, target_count)

    def _refresh_cached_folder_messages(self, folder, history_probe, limit):
        cached_messages = self._folder_cached_messages(folder)
        if not cached_messages:
            return None
        if any(not msg.get('gmail_msgid') for msg in cached_messages):
            return None
        current_by_msgid = {
            msg.get('gmail_msgid'): dict(msg)
            for msg in cached_messages
            if msg.get('gmail_msgid')
        }
        if not current_by_msgid:
            return None
        remove_ids = set(history_probe.get('remove_ids', set()))
        refresh_map = dict(history_probe.get('refresh_map', {}))
        for gmail_msgid in remove_ids:
            current_by_msgid.pop(gmail_msgid, None)
        if refresh_map:
            refreshed_by_msgid = self._fetch_gmail_metadata_messages(refresh_map, folder)
            if not refreshed_by_msgid and refresh_map:
                return None
            for gmail_msgid, refreshed in refreshed_by_msgid.items():
                current_by_msgid[gmail_msgid] = refreshed
        target_count = min(_SYNC_RECENT_MESSAGES_LIMIT, max(limit, len(cached_messages)))
        merged = sorted(
            current_by_msgid.values(),
            key=lambda item: item.get('date') or datetime.now(timezone.utc),
            reverse=True,
        )[:_SYNC_RECENT_MESSAGES_LIMIT]
        if len(merged) < target_count:
            merged = self._top_up_cached_folder_messages(folder, merged, target_count)
        self._update_folder_sync_state(folder, messages=merged, history_id=history_probe.get('history_id'))
        self._gmail_list_memo_reset(folder)
        return merged[:limit]

    def _refresh_cached_inbox_messages(self, history_probe, limit):
        return self._refresh_cached_folder_messages('INBOX', history_probe, limit)

    def _invalidate_token(self):
        self._cached_token = None
        self._cached_token_expiry = 0.0
        invalidator = getattr(self.source_obj, 'invalidate_access_token', None)
        if callable(invalidator):
            try:
                invalidator()
            except Exception:
                pass

    def _token(self):
        now = time.monotonic()
        cached_token = getattr(self, '_cached_token', None)
        cached_expiry = float(getattr(self, '_cached_token_expiry', 0.0) or 0.0)
        if cached_token and cached_expiry > now:
            return cached_token
        try:
            getter = getattr(self.source_obj, 'get_access_token', None)
            if not callable(getter):
                raise OAuthTokenAcquisitionError(
                    'OAuth token source is unavailable',
                    stage='source lookup',
                    retryable=False,
                    source=str(getattr(self.account_descriptor, 'source', '') or 'oauth'),
                )
            token = getter(network_ready_fn=network_ready)
        except Exception:
            self._invalidate_token()
            raise
        self._cached_token = token
        self._cached_token_expiry = now + _GMAIL_TOKEN_CACHE_TTL_SECS
        return token

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        try:
            labels = self._gmail_labels()
            extra = []
            for name, label in labels.items():
                if not name:
                    continue
                if name in self._STANDARD_FOLDER_IDS or name in self._GMAIL_SYSTEM_LABELS.values():
                    continue
                if str(label.get('type') or '').lower() == 'system':
                    continue
                extra.append((name, name, 'folder-symbolic'))
            extra.sort(key=lambda item: item[1].lower())
            return extra
        except Exception:
            return []

    def get_cached_messages(self, folder='INBOX', limit=50):
        return self._folder_cached_messages(folder)[:limit]

    def fetch_messages(self, folder='INBOX', limit=50):
        sync_label = self._gmail_partial_sync_label(folder)
        use_partial_sync_cache = bool(sync_label) and int(limit) <= _SYNC_RECENT_MESSAGES_LIMIT
        history_probe = None
        api_available = self._gmail_probe_api_now()
        api_exc = None
        api_notice = None
        if use_partial_sync_cache:
            history_probe = self._probe_cached_folder_messages(folder, sync_label['id'])
            if history_probe and history_probe.get('status') == 'unchanged':
                self._gmail_mark_api_ready('Ready')
                return list(history_probe.get('messages', []))[:limit]
            if history_probe and history_probe.get('status') == 'changed':
                refreshed = self._refresh_cached_folder_messages(folder, history_probe, limit)
                if refreshed is not None:
                    self._gmail_mark_api_ready('Ready')
                    return refreshed
        internal_limit = max(limit, _SYNC_RECENT_MESSAGES_LIMIT) if sync_label else limit
        if api_available:
            try:
                api_messages = self._gmail_api_fetch_messages(folder, internal_limit)
                if api_messages is not None:
                    self._gmail_mark_api_ready('Ready')
                    if sync_label:
                        self._update_folder_sync_state(
                            folder,
                            messages=api_messages,
                            history_id=history_probe.get('history_id') if history_probe and history_probe.get('status') == 'changed' else None,
                        )
                        if not (history_probe and history_probe.get('status') == 'changed'):
                            self._ensure_gmail_history_seed_async(folder)
                    else:
                        self._update_folder_sync_state(folder, messages=api_messages)
                    return api_messages[:limit]
            except Exception as exc:
                api_exc = exc
                api_notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(api_notice)
        if self._gmail_has_imap_fallback():
            try:
                imap_messages = self._gmail_imap_fetch_messages(folder, internal_limit)
            except Exception as exc:
                if api_exc is None:
                    api_exc = exc
            else:
                if api_exc is not None:
                    self._gmail_mark_retrying(
                        api_notice or {},
                        api_exc,
                        folder,
                        f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                    )
                self._update_folder_sync_state(folder, messages=imap_messages)
                return imap_messages[:limit]
        if sync_label and not (history_probe and history_probe.get('status') == 'changed'):
            self._ensure_gmail_history_seed_async(folder)
        if api_notice is not None:
            self._gmail_mark_api_error(
                api_notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                tooltip=api_notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                code=str(api_notice.get('code') or ''),
                retryable=bool(api_notice.get('retryable', False)),
            )
            raise api_exc
        raise RuntimeError(f'Could not load {self._gmail_notice_folder_name(folder)}')

    def check_background_updates(self, tracked_folders=None, reconcile_counts=False):
        health = self._gmail_health_state()
        if self._gmail_has_imap_fallback() and health.route != 'primary' and not health.should_probe_primary():
            return self._gmail_imap_check_background_updates(tracked_folders=tracked_folders, reconcile_counts=reconcile_counts)
        if not self._gmail_has_imap_fallback() and health.state != 'ready' and not health.should_probe_primary():
            notice = self.consume_sync_notices()
            return {
                'account': self.identity,
                'provider': self.provider,
                'changed_folders': set(),
                'new_messages': [],
                'counts': {},
                'notice': notice,
            }
        folders = []
        seen = set()
        default_folders = [folder_id for folder_id, _name, _icon in self.FOLDERS]
        for folder in list(tracked_folders or []) + default_folders:
            folder_text = str(folder or '').strip()
            if not folder_text or folder_text in seen:
                continue
            folders.append(folder_text)
            seen.add(folder_text)

        changed_folders = set()
        new_messages = []
        counts = {}
        for folder in folders:
            label = self.gmail_label_for_folder(folder)
            if not label or not label.get('id'):
                continue
            history_probe = self._probe_cached_folder_messages(folder, label['id'])
            if history_probe is None:
                self._ensure_gmail_history_seed_async(folder)
                continue
            status = history_probe.get('status')
            if status == 'unchanged':
                continue
            if status in {'reset', 'unsupported'}:
                if status == 'reset':
                    self._ensure_gmail_history_seed_async(folder)
                continue
            if status != 'changed':
                continue
            changed_folders.add(folder)
            cached_messages = self._folder_cached_messages(folder)
            previous_by_msgid = {
                msg.get('gmail_msgid'): dict(msg)
                for msg in cached_messages
                if msg.get('gmail_msgid')
            }
            refreshed = self._refresh_cached_folder_messages(folder, history_probe, _SYNC_RECENT_MESSAGES_LIMIT)
            if refreshed is None:
                refreshed = self._folder_cached_messages(folder)
                self._update_folder_sync_state(
                    folder,
                    messages=refreshed,
                    history_id=history_probe.get('history_id'),
                )
            if folder == 'INBOX':
                refreshed_by_msgid = {
                    msg.get('gmail_msgid'): msg
                    for msg in refreshed
                    if msg.get('gmail_msgid')
                }
                for gmail_msgid in history_probe.get('new_ids', set()):
                    if gmail_msgid in previous_by_msgid:
                        continue
                    message = refreshed_by_msgid.get(gmail_msgid)
                    if message and not message.get('is_read', True):
                        new_messages.append(message)

        if reconcile_counts or 'INBOX' in changed_folders:
            counts['inbox'] = self.get_unread_count('INBOX', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Trash' in changed_folders:
            counts['trash'] = self.get_unread_count('[Gmail]/Trash', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Spam' in changed_folders:
            counts['spam'] = self.get_unread_count('[Gmail]/Spam', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Drafts' in changed_folders:
            counts['drafts'] = self.get_unread_count('[Gmail]/Drafts', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Sent Mail' in changed_folders:
            counts['sent'] = self.get_unread_count('[Gmail]/Sent Mail', force_primary=reconcile_counts)
        notice = self.consume_sync_notices()
        return {
            'account': self.identity,
            'provider': self.provider,
            'changed_folders': changed_folders,
            'new_messages': new_messages,
            'counts': counts,
            'notice': notice,
        }

    def _gmail_imap_check_background_updates(self, tracked_folders=None, reconcile_counts=False):
        folders = []
        seen = set()
        default_folders = [folder_id for folder_id, _name, _icon in self.FOLDERS]
        for folder in list(tracked_folders or []) + default_folders:
            folder_text = str(folder or '').strip()
            if not folder_text or folder_text in seen:
                continue
            folders.append(folder_text)
            seen.add(folder_text)

        changed_folders = set()
        new_messages = []
        counts = {}
        for folder in folders:
            previous = self._folder_cached_messages(folder)
            try:
                refreshed = self._gmail_imap_fetch_messages(folder, _SYNC_RECENT_MESSAGES_LIMIT)
            except Exception:
                continue
            previous_uids = {msg.get('uid') for msg in previous if msg.get('uid')}
            refreshed_uids = {msg.get('uid') for msg in refreshed if msg.get('uid')}
            if refreshed_uids != previous_uids or messages_changed(previous, refreshed):
                changed_folders.add(folder)
            if folder == 'INBOX' and refreshed_uids != previous_uids:
                previous_by_uid = {msg.get('uid'): msg for msg in previous if msg.get('uid')}
                for msg in refreshed:
                    if msg.get('uid') not in previous_by_uid and not msg.get('is_read', True):
                        new_messages.append(msg)
            self._update_folder_sync_state(folder, messages=refreshed)

        if reconcile_counts or 'INBOX' in changed_folders:
            counts['inbox'] = self.get_unread_count('INBOX', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Trash' in changed_folders:
            counts['trash'] = self.get_unread_count('[Gmail]/Trash', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Spam' in changed_folders:
            counts['spam'] = self.get_unread_count('[Gmail]/Spam', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Drafts' in changed_folders:
            counts['drafts'] = self.get_unread_count('[Gmail]/Drafts', force_primary=reconcile_counts)
        if reconcile_counts or '[Gmail]/Sent Mail' in changed_folders:
            counts['sent'] = self.get_unread_count('[Gmail]/Sent Mail', force_primary=reconcile_counts)
        notice = self.consume_sync_notices()
        return {
            'account': self.identity,
            'provider': self.provider,
            'changed_folders': changed_folders,
            'new_messages': new_messages,
            'counts': counts,
            'notice': notice,
        }

    def fetch_thread_messages(self, thread_id):
        if not thread_id:
            return []
        try:
            messages = self._gmail_api_fetch_thread_messages(thread_id)
            self._gmail_mark_api_ready('Ready')
            return messages
        except Exception as exc:
            notice = self._gmail_notice_for_exception(exc, 'INBOX')
            self._set_sync_notice(notice)
            return []

    def fetch_body(self, uid, folder='INBOX'):
        api_message_id = self._gmail_api_message_id_for_uid(folder, uid)
        if api_message_id and self._gmail_probe_api_now():
            try:
                body = self._gmail_api_body_for_message(api_message_id)
                self._gmail_mark_api_ready('Ready')
                return body
            except Exception as exc:
                notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(notice)
                retry_after = self._gmail_retry_delay_for_exception(exc)
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
                    self._gmail_mark_api_error(
                        'Sign-in needs attention',
                        tooltip=notice.get('detail') or 'Sign-in needs attention',
                        code=str(exc.code),
                        retryable=True,
                        retry_after_seconds=retry_after,
                    )
                else:
                    self._gmail_mark_api_fallback(
                        f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                        tooltip=notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                        code=str(notice.get('code') or ''),
                        retryable=True,
                        retry_after_seconds=retry_after,
                    )
        if not self._gmail_has_imap_fallback():
            raise BodyFetchError(f'Could not load message from {self._gmail_notice_folder_name(folder)}')
        try:
            folder_name = self._gmail_imap_folder_name(folder)
            with self._gmail_imap_session() as imap:
                status, _selected = imap.select(folder_name, readonly=True)
                if status != 'OK':
                    raise BodyFetchError(f'Message body is unavailable for {folder_name}')
                status, data = imap.uid('fetch', str(uid), '(UID FLAGS RFC822)')
                if status != 'OK':
                    raise BodyFetchError(f'Message body is unavailable for {folder_name}')
                raw = b''
                flags = set()
                for item in data or []:
                    if isinstance(item, tuple):
                        raw = item[1] or b''
                        flags = {flag.decode('ascii', errors='ignore').lower() for flag in re.findall(rb'\\[A-Za-z]+', item[0] or b'')}
                        break
                if not raw:
                    raise BodyFetchError(f'Message body is unavailable for {folder_name}')
                message = self._gmail_imap_decode_message(raw)
                state = {'html': None, 'text': None, 'attachments': []}
                for part in message.walk():
                    if part.is_multipart():
                        continue
                    self._gmail_imap_extract_part(part, state)
                return state['html'], state['text'], state['attachments']
        except BodyFetchError:
            raise
        except Exception as exc:
            raise BodyFetchError(f'Could not load message from {self._gmail_notice_folder_name(folder)}') from exc

    def mark_as_read(self, uid, folder='INBOX'):
        api_message_id = self._gmail_api_message_id_for_uid(folder, uid)
        if api_message_id and self._gmail_probe_api_now():
            try:
                self._gmail_api_modify_message(api_message_id, remove_label_ids=['UNREAD'])
                self.update_cached_message_read_state(folder, uid, True)
                self._gmail_mark_api_ready('Ready')
                return
            except Exception as exc:
                notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(notice)
                self._gmail_mark_api_fallback(
                    f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                    tooltip=notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                    code=str(notice.get('code') or ''),
                    retryable=bool(notice.get('retryable', True)),
                    retry_after_seconds=self._gmail_retry_delay_for_exception(exc),
                )
        if not self._gmail_has_imap_fallback():
            return
        try:
            folder_name = self._gmail_imap_folder_name(folder)
            with self._gmail_imap_session() as imap:
                status, _selected = imap.select(folder_name)
                if status != 'OK':
                    return
                imap.uid('store', str(uid), '+FLAGS.SILENT', r'(\Seen)')
                self.update_cached_message_read_state(folder, uid, True)
        except Exception:
            pass

    def mark_as_unread(self, uid, folder='INBOX'):
        api_message_id = self._gmail_api_message_id_for_uid(folder, uid)
        if api_message_id and self._gmail_probe_api_now():
            try:
                self._gmail_api_modify_message(api_message_id, add_label_ids=['UNREAD'])
                self.update_cached_message_read_state(folder, uid, False)
                self._gmail_mark_api_ready('Ready')
                return
            except Exception as exc:
                notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(notice)
                self._gmail_mark_api_fallback(
                    f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                    tooltip=notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                    code=str(notice.get('code') or ''),
                    retryable=bool(notice.get('retryable', True)),
                    retry_after_seconds=self._gmail_retry_delay_for_exception(exc),
                )
        if not self._gmail_has_imap_fallback():
            return
        try:
            folder_name = self._gmail_imap_folder_name(folder)
            with self._gmail_imap_session() as imap:
                status, _selected = imap.select(folder_name)
                if status != 'OK':
                    return
                imap.uid('store', str(uid), '-FLAGS.SILENT', r'(\Seen)')
                self.update_cached_message_read_state(folder, uid, False)
        except Exception:
            pass

    def delete_message(self, uid, folder='INBOX'):
        api_message_id = self._gmail_api_message_id_for_uid(folder, uid)
        if api_message_id and self._gmail_probe_api_now():
            try:
                self._gmail_api_request(self._gmail_api_message_url(api_message_id) + '/trash', method='POST')
                self.remove_cached_message(folder, uid)
                self._gmail_mark_api_ready('Ready')
                return
            except Exception as exc:
                notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(notice)
                self._gmail_mark_api_fallback(
                    f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                    tooltip=notice.get('detail') or f'Could not load {self._gmail_notice_folder_name(folder)}',
                    code=str(notice.get('code') or ''),
                    retryable=bool(notice.get('retryable', True)),
                    retry_after_seconds=self._gmail_retry_delay_for_exception(exc),
                )
        if not self._gmail_has_imap_fallback():
            return
        try:
            folder_name = self._gmail_imap_folder_name(folder)
            trash_folder = self._gmail_imap_folder_name('[Gmail]/Trash')
            with self._gmail_imap_session() as imap:
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
                self.remove_cached_message(folder, uid)
        except Exception:
            pass

    def get_unread_count(self, folder='INBOX', force_primary=False):
        policy = self.get_unread_count_policy(folder, force_primary=force_primary)
        label_id = self._gmail_api_label_id_for_folder(folder)
        api_exc = None
        api_notice = None
        if policy.get('route') == 'primary' and label_id and self._gmail_probe_api_for_counts(force=force_primary):
            try:
                count = self._gmail_api_label_count(label_id)
                self._gmail_mark_api_ready('Ready')
                return count
            except Exception as exc:
                api_exc = exc
                api_notice = self._gmail_notice_for_exception(exc, folder)
                self._set_sync_notice(api_notice)
                retry_after = self._gmail_retry_delay_for_exception(exc)
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
                    self._gmail_mark_api_error(
                        api_notice.get('detail') or 'Sign-in needs attention',
                        tooltip=api_notice.get('detail') or 'Sign-in needs attention',
                        code=str(exc.code),
                        retryable=True,
                        retry_after_seconds=retry_after,
                    )
                else:
                    self._gmail_mark_api_fallback(
                        f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                        tooltip=api_notice.get('detail') or f'Could not read unread count for {self._gmail_notice_folder_name(folder)}',
                        code=str(api_notice.get('code') or ''),
                    retryable=True,
                    retry_after_seconds=retry_after,
                )
        if not self._gmail_has_imap_fallback():
            return 0
        try:
            count = self._gmail_imap_unread_count(folder)
            if api_exc is not None:
                tooltip = f'Unread counts are coming from IMAP for {self._gmail_notice_folder_name(folder)}.'
                if api_notice and api_notice.get('detail'):
                    tooltip = f'{api_notice.get("detail")} {tooltip}'
                self._gmail_health_state().mark_warning(
                    f'Gmail API unavailable for {self._gmail_notice_folder_name(folder)}',
                    tooltip=tooltip,
                    retryable=True,
                    retry_after_seconds=min(120, self._gmail_retry_delay_for_exception(api_exc)),
                    route='primary',
                )
                self._gmail_api_available = False
                self._gmail_log_health_event(
                    'warning',
                    'gmail-unread-count-fallback',
                    f'Gmail unread count served from IMAP for {self._gmail_notice_folder_name(folder)}',
                    code=str(api_notice.get('code') or '') if api_notice else '',
                    route='primary',
                    retryable=True,
                )
            return count
        except Exception as exc:
            notice = self._gmail_notice_for_exception(exc, folder)
            self._set_sync_notice(notice)
            self._gmail_mark_api_error(
                notice.get('detail') or f'Could not read unread count for {self._gmail_notice_folder_name(folder)}',
                tooltip=notice.get('detail') or f'Could not read unread count for {self._gmail_notice_folder_name(folder)}',
                code=str(notice.get('code') or ''),
                retryable=bool(notice.get('retryable', False)),
                retry_after_seconds=self._gmail_retry_delay_for_exception(exc) if notice.get('retryable') else None,
            )
        return 0

    def fetch_contacts(self, query=''):
        try:
            ensure_network_ready()
            token = self._token()
            url = (
                'https://people.googleapis.com/v1/people/me/connections'
                '?personFields=names,emailAddresses&pageSize=100'
            )
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read())
            contacts = []
            query_lower = query.lower()
            for conn in data.get('connections', []):
                names = conn.get('names', [{}])
                name = names[0].get('displayName', '') if names else ''
                for entry in conn.get('emailAddresses', []):
                    addr = entry.get('value', '')
                    if addr and (not query_lower or query_lower in addr.lower() or query_lower in name.lower()):
                        contacts.append({'name': name, 'email': addr})
            return contacts[:15]
        except Exception:
            return []

    def send_message(self, to, subject, body, html=None, cc=None, bcc=None, reply_to_msg=None, attachments=None):
        ensure_network_ready()
        token = self._token()
        to_addrs = _normalize_recipients(to)
        cc_addrs = _normalize_recipients(cc)
        bcc_addrs = _normalize_recipients(bcc)
        reply_msgid = (reply_to_msg or {}).get('message_id', '') if reply_to_msg else ''
        if attachments:
            outer = MIMEMultipart('mixed')
            outer['From'] = self.identity
            outer['To'] = ', '.join(to_addrs)
            outer['Subject'] = subject
            if cc_addrs:
                outer['Cc'] = ', '.join(cc_addrs)
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
            msg['To'] = ', '.join(to_addrs)
            msg['Subject'] = subject
            if cc_addrs:
                msg['Cc'] = ', '.join(cc_addrs)
            if reply_msgid:
                msg['In-Reply-To'] = reply_msgid
                msg['References'] = reply_msgid
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            if html:
                msg.attach(MIMEText(html, 'html', 'utf-8'))
        recipients = to_addrs + cc_addrs + bcc_addrs
        if self._use_gmail_api_send:
            payload = {
                'raw': base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii').rstrip('='),
            }
            thread_id = str((reply_to_msg or {}).get('thread_id') or '').strip()
            if thread_id:
                payload['threadId'] = thread_id
            self._gmail_api_request('/users/me/messages/send', method='POST', data=payload)
            return
        auth_str = f'user={self.identity}\x01auth=Bearer {token}\x01\x01'
        smtp = smtplib.SMTP('smtp.gmail.com', 587, timeout=_GMAIL_SMTP_TIMEOUT_SECS)
        try:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth_str.encode()).decode())
            smtp.sendmail(self.identity, recipients, msg.as_bytes())
        finally:
            try:
                smtp.quit()
            except Exception:
                pass

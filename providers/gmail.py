"""Gmail provider implementation."""

import base64
import email as email_parser
import imaplib
import json
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    from ..accounts.auth.goa_oauth import get_goa_access_token
    from ..sync_state import get_account_state, set_account_state
    from .common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from .sync_rows import deserialize_sync_messages, serialize_sync_messages
except ImportError:
    from accounts.auth.goa_oauth import get_goa_access_token
    from sync_state import get_account_state, set_account_state
    from providers.common import (
        _aware_utc_datetime,
        _decode_str,
        _normalize_recipients,
        _parse_addrs,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
    from providers.sync_rows import deserialize_sync_messages, serialize_sync_messages


_GMAIL_IMAP_TIMEOUT_SECS = 20
_GMAIL_SMTP_TIMEOUT_SECS = 20
_GMAIL_API_TIMEOUT_SECS = 10
_SYNC_RECENT_MESSAGES_LIMIT = 100
_GMAIL_METADATA_HEADERS = [
    'From',
    'To',
    'Cc',
    'Subject',
    'Date',
    'Content-Type',
    'Message-ID',
]


def _imap_folder(name):
    """Quote IMAP folder names that contain spaces."""
    return f'"{name}"' if ' ' in name else name


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


_SPECIAL_USE_MAP = {
    '\\Sent': '[Gmail]/Sent Mail',
    '\\Drafts': '[Gmail]/Drafts',
    '\\Trash': '[Gmail]/Trash',
    '\\Junk': '[Gmail]/Spam',
    '\\All': None,
    '\\Flagged': None,
    '\\Important': None,
}


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
        self.goa_obj = descriptor.source_obj
        self.account = self.goa_obj.get_account()
        self.identity = descriptor.identity
        self.provider = 'gmail'
        self._imap = None
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._special_folders = {}
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

    def _gmail_api_request(self, path, query=None):
        ensure_network_ready()
        token = self._token()
        url = f'https://gmail.googleapis.com/gmail/v1{path}'
        if query:
            url += '?' + urllib.parse.urlencode(query, doseq=True)
        req = urllib.request.Request(
            url,
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=_GMAIL_API_TIMEOUT_SECS) as response:
            return json.loads(response.read())

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
            return dict(self._gmail_labels_by_name)

    def _gmail_label_name_for_folder(self, folder):
        if not folder:
            return None
        folder_text = str(folder)
        if folder_text in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[folder_text]
        special_actual_to_logical = {}
        for logical_name, actual_name in self._special_folders.items():
            if logical_name.startswith('_flag:'):
                continue
            actual_text = str(actual_name)
            special_actual_to_logical[actual_text] = logical_name
            special_actual_to_logical[_decode_imap_utf7(actual_text)] = logical_name
        logical_from_actual = special_actual_to_logical.get(folder_text)
        if logical_from_actual in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[logical_from_actual]
        decoded_folder = _decode_imap_utf7(folder_text)
        logical_from_decoded_actual = special_actual_to_logical.get(decoded_folder)
        if logical_from_decoded_actual in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[logical_from_decoded_actual]
        resolved = self._resolve_folder(folder)
        if resolved in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[resolved]
        decoded = _decode_imap_utf7(str(resolved))
        if decoded in self._GMAIL_SYSTEM_LABELS:
            return self._GMAIL_SYSTEM_LABELS[decoded]
        if decoded.startswith('[Gmail]/') or decoded.startswith('[Google Mail]/'):
            return None
        return decoded

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
            'thread_source': 'gmail-imap',
            'message_id': _decode_str(headers.get('message-id', '')),
            'gmail_msgid': _gmail_api_id_to_imap_id(api_id),
        }

    def _fetch_gmail_metadata_messages(self, refresh_map, folder='INBOX'):
        metadata_messages = {}
        for gmail_msgid, api_id in (refresh_map or {}).items():
            api_message = self._gmail_message_metadata(api_id)
            metadata_messages[gmail_msgid] = api_message
        if not metadata_messages:
            return {}
        found_uids = self._find_imap_uids_by_gmail_msgids(folder, sorted(metadata_messages))
        if len(found_uids) != len(metadata_messages):
            return {}
        refreshed = {}
        for gmail_msgid, api_message in metadata_messages.items():
            uid = found_uids.get(gmail_msgid)
            if not uid:
                return {}
            refreshed[gmail_msgid] = self._gmail_message_from_api_metadata(api_message, uid, folder)
        return refreshed

    def _parse_imap_fetch_messages(self, fetch_data, fallback_uids, folder):
        messages = []
        idx = 0
        for chunk in fetch_data or []:
            if not isinstance(chunk, tuple):
                continue
            info_bytes, raw_headers = chunk
            info = info_bytes.decode(errors='replace')
            is_read = '\\Seen' in info
            uid_m = re.search(r'\bUID\s+(\d+)', info, re.IGNORECASE)
            uid = uid_m.group(1) if uid_m else fallback_uids[min(idx, len(fallback_uids) - 1)]
            msgid_m = re.search(r'\bX-GM-MSGID\s+(\d+)', info, re.IGNORECASE)
            gmail_msgid = msgid_m.group(1) if msgid_m else ''
            thrid_m = re.search(r'\bX-GM-THRID\s+(\d+)', info, re.IGNORECASE)
            thread_id = thrid_m.group(1) if thrid_m else ''
            idx += 1
            parsed = email_parser.message_from_bytes(raw_headers)
            subject = _decode_str(parsed.get('Subject', '(no subject)'))
            message_id = _decode_str(parsed.get('Message-ID', ''))
            from_ = _decode_str(parsed.get('From', ''))
            date_str = parsed.get('Date', '')
            content_type = parsed.get('Content-Type', '').lower()
            has_attachments = 'multipart/mixed' in content_type
            to_addrs = _parse_addrs(_decode_str(parsed.get('To', '')))
            cc_addrs = _parse_addrs(_decode_str(parsed.get('Cc', '')))
            sender_name, sender_email = email_parser.utils.parseaddr(from_)
            if not sender_name:
                sender_name = sender_email
            try:
                date = _aware_utc_datetime(email_parser.utils.parsedate_to_datetime(date_str))
            except Exception:
                date = datetime.now(timezone.utc)
            messages.append({
                'uid': uid,
                'subject': subject,
                'sender_name': sender_name or sender_email,
                'sender_email': sender_email,
                'to_addrs': to_addrs,
                'cc_addrs': cc_addrs,
                'date': date,
                'is_read': is_read,
                'has_attachments': has_attachments,
                'snippet': '',
                'folder': folder,
                'backend': 'gmail',
                'account': self.identity,
                'backend_obj': self,
                'thread_id': thread_id,
                'thread_source': 'gmail-imap',
                'message_id': message_id,
                'gmail_msgid': gmail_msgid,
            })
        return messages

    def _fetch_selected_imap_messages_locked(self, imap, folder, uids):
        normalized = [str(uid).strip() for uid in (uids or []) if str(uid).strip()]
        if not normalized:
            return []
        uid_str = ','.join(normalized).encode()
        _, fetch_data = imap.uid(
            'fetch',
            uid_str,
            '(UID FLAGS X-GM-MSGID X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE CONTENT-TYPE MESSAGE-ID IN-REPLY-TO REFERENCES)])',
        )
        return self._parse_imap_fetch_messages(fetch_data, normalized, folder)

    def _fetch_messages_imap(self, folder='INBOX', limit=50):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            _, data = imap.uid('search', None, 'ALL')
            uids = data[0].split()
            if not uids:
                return []
            selected_uids = [uid.decode() for uid in uids[-limit:]]
            messages = self._fetch_selected_imap_messages_locked(imap, folder, selected_uids)
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')), reverse=True)
        return messages

    def _fetch_messages_imap_uids(self, folder, uids):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            messages = self._fetch_selected_imap_messages_locked(imap, folder, uids)
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')), reverse=True)
        return messages

    def _find_imap_uids_by_gmail_msgids(self, folder, gmail_msgids):
        targets = [msgid for msgid in dict.fromkeys(gmail_msgids or []) if msgid]
        if not targets:
            return {}
        found = {}
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            for gmail_msgid in targets:
                _, data = imap.uid('search', None, f'X-GM-MSGID {gmail_msgid}')
                uids = data[0].split() if data and data[0] else []
                if uids:
                    found[gmail_msgid] = uids[-1].decode()
        return found

    def _top_up_cached_folder_messages(self, folder, current_messages, target_count):
        if target_count <= 0:
            return list(current_messages or [])
        current_messages = list(current_messages or [])
        known_msgids = {msg.get('gmail_msgid') for msg in current_messages if msg.get('gmail_msgid')}
        known_uids = {msg.get('uid') for msg in current_messages if msg.get('uid')}
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            _, data = imap.uid('search', None, 'ALL')
            all_uids = [uid.decode() for uid in data[0].split()] if data and data[0] else []
            if not all_uids:
                return current_messages
            scan_limit = max(target_count * 3, target_count + 20)
            candidate_uids = []
            for uid in reversed(all_uids):
                if uid in known_uids:
                    continue
                candidate_uids.append(uid)
                if len(candidate_uids) >= scan_limit:
                    break
            extras = self._fetch_selected_imap_messages_locked(imap, folder, list(reversed(candidate_uids)))
        extras.sort(key=lambda item: item.get('date') or datetime.now(timezone.utc), reverse=True)
        for extra in extras:
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
        return merged[:limit]

    def _refresh_cached_inbox_messages(self, history_probe, limit):
        return self._refresh_cached_folder_messages('INBOX', history_probe, limit)

    def _token(self):
        return get_goa_access_token(self.goa_obj, self.account, network_ready_fn=network_ready)

    def _get_imap(self):
        ensure_network_ready()
        if self._imap is not None:
            try:
                self._imap.noop()
                return self._imap
            except Exception:
                try:
                    self._imap.logout()
                except Exception:
                    pass
                self._imap = None
        token = self._token()
        ctx = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL(
            'imap.gmail.com',
            ssl_context=ctx,
            timeout=_GMAIL_IMAP_TIMEOUT_SECS,
        )
        auth_str = f'user={self.identity}\x01auth=Bearer {token}\x01\x01'
        imap.authenticate('XOAUTH2', lambda _x: auth_str.encode())
        self._imap = imap
        self._detect_special_folders(imap)
        return imap

    def _detect_special_folders(self, imap):
        try:
            _, items = imap.list()
        except Exception:
            return
        self._special_folders = {}
        for item in items:
            if not isinstance(item, bytes):
                continue
            decoded = item.decode(errors='replace')
            flags_m = re.match(r'\(([^)]*)\)', decoded)
            if not flags_m:
                continue
            flags = flags_m.group(1)
            name_m = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', decoded)
            if not name_m:
                continue
            actual = (name_m.group(1) or name_m.group(2)).strip('"')
            for flag, logical_key in _SPECIAL_USE_MAP.items():
                if re.search(re.escape(flag), flags, re.IGNORECASE):
                    if logical_key is not None:
                        self._special_folders[logical_key] = actual
                    self._special_folders.setdefault(f'_flag:{flag.lower()}', actual)

    def _resolve_folder(self, folder):
        return self._special_folders.get(folder, folder)

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        with self._lock:
            imap = self._get_imap()
            _, items = imap.list()
        excluded_actuals = set(self._special_folders.values())
        extra = []
        for item in items:
            if not isinstance(item, bytes):
                continue
            decoded = item.decode(errors='replace')
            flags_m = re.match(r'\(([^)]*)\)', decoded)
            if flags_m:
                flags_lower = flags_m.group(1).lower()
                if any(flag in flags_lower for flag in ('\\all', '\\flagged', '\\important')):
                    continue
            match = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', decoded)
            if not match:
                continue
            name = (match.group(1) or match.group(2)).strip('"')
            if name in self._STANDARD_FOLDER_IDS or name in excluded_actuals:
                continue
            display = _decode_imap_utf7(
                re.sub(r'^\[Gmail\]/', '', re.sub(r'^\[Google Mail\]/', '', name))
            )
            extra.append((name, display, 'folder-symbolic'))
        return extra

    def fetch_messages(self, folder='INBOX', limit=50):
        sync_label = self._gmail_partial_sync_label(folder)
        use_partial_sync_cache = bool(sync_label) and int(limit) <= _SYNC_RECENT_MESSAGES_LIMIT
        history_probe = None
        if use_partial_sync_cache:
            history_probe = self._probe_cached_folder_messages(folder, sync_label['id'])
            if history_probe and history_probe.get('status') == 'unchanged':
                return list(history_probe.get('messages', []))[:limit]
            if history_probe and history_probe.get('status') == 'changed':
                refreshed = self._refresh_cached_folder_messages(folder, history_probe, limit)
                if refreshed is not None:
                    return refreshed
        internal_limit = max(limit, _SYNC_RECENT_MESSAGES_LIMIT) if sync_label else limit
        messages = self._fetch_messages_imap(folder, internal_limit)
        if sync_label:
            self._update_folder_sync_state(
                folder,
                messages=messages,
                history_id=history_probe.get('history_id') if history_probe and history_probe.get('status') == 'changed' else None,
            )
            if not (history_probe and history_probe.get('status') == 'changed'):
                self._ensure_gmail_history_seed_async(folder)
            return messages[:limit]
        return messages

    def check_background_updates(self, tracked_folders=None, reconcile_counts=False):
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
                refreshed = self._fetch_messages_imap(folder, _SYNC_RECENT_MESSAGES_LIMIT)
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
            counts['inbox'] = self.get_unread_count('INBOX')
        if reconcile_counts or '[Gmail]/Trash' in changed_folders:
            counts['trash'] = self.get_unread_count('[Gmail]/Trash')
        if reconcile_counts or '[Gmail]/Spam' in changed_folders:
            counts['spam'] = self.get_unread_count('[Gmail]/Spam')
        return {
            'account': self.identity,
            'provider': self.provider,
            'changed_folders': changed_folders,
            'new_messages': new_messages,
            'counts': counts,
        }

    def fetch_thread_messages(self, thread_id):
        if not thread_id:
            return []
        with self._lock:
            imap = self._get_imap()
            all_mail = self._special_folders.get('_flag:\\all') or self._special_folders.get('[Gmail]/All Mail')
            if all_mail:
                imap.select(_imap_folder(all_mail), readonly=True)
            else:
                imap.select(_imap_folder(self._resolve_folder('INBOX')), readonly=True)
            _, data = imap.uid('search', None, f'X-GM-THRID {thread_id}')
            uids = data[0].split() if data and data[0] else []
            if not uids:
                return []
            uid_str = b','.join(uids)
            _, fetch_data = imap.uid(
                'fetch',
                uid_str,
                '(UID FLAGS X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE CONTENT-TYPE MESSAGE-ID IN-REPLY-TO REFERENCES)])',
            )
        messages = []
        idx = 0
        for chunk in fetch_data:
            if not isinstance(chunk, tuple):
                continue
            info_bytes, raw_headers = chunk
            info = info_bytes.decode(errors='replace')
            is_read = '\\Seen' in info
            uid_m = re.search(r'\bUID\s+(\d+)', info, re.IGNORECASE)
            uid = uid_m.group(1) if uid_m else uids[min(idx, len(uids) - 1)].decode()
            thrid_m = re.search(r'\bX-GM-THRID\s+(\d+)', info, re.IGNORECASE)
            current_thread_id = thrid_m.group(1) if thrid_m else thread_id
            idx += 1
            parsed = email_parser.message_from_bytes(raw_headers)
            subject = _decode_str(parsed.get('Subject', '(no subject)'))
            message_id = _decode_str(parsed.get('Message-ID', ''))
            from_ = _decode_str(parsed.get('From', ''))
            date_str = parsed.get('Date', '')
            content_type = parsed.get('Content-Type', '').lower()
            has_attachments = 'multipart/mixed' in content_type
            to_addrs = _parse_addrs(_decode_str(parsed.get('To', '')))
            cc_addrs = _parse_addrs(_decode_str(parsed.get('Cc', '')))
            sender_name, sender_email = email_parser.utils.parseaddr(from_)
            if not sender_name:
                sender_name = sender_email
            try:
                date = _aware_utc_datetime(email_parser.utils.parsedate_to_datetime(date_str))
            except Exception:
                date = datetime.now(timezone.utc)
            messages.append({
                'uid': uid,
                'subject': subject,
                'sender_name': sender_name or sender_email,
                'sender_email': sender_email,
                'to_addrs': to_addrs,
                'cc_addrs': cc_addrs,
                'date': date,
                'is_read': is_read,
                'has_attachments': has_attachments,
                'snippet': '',
                'folder': all_mail or 'INBOX',
                'backend': 'gmail',
                'account': self.identity,
                'backend_obj': self,
                'thread_id': current_thread_id,
                'thread_source': 'gmail-imap',
                'message_id': message_id,
            })
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')))
        return messages

    def fetch_body(self, uid, folder='INBOX'):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            _, data = imap.uid('fetch', uid.encode(), '(BODY.PEEK[])')
            raw = data[0][1]
        msg = email_parser.message_from_bytes(raw)
        html_body = text_body = None
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                charset = part.get_content_charset() or 'utf-8'
                disp = (part.get_content_disposition() or '').lower()
                fname = part.get_filename()
                if ct == 'text/html' and html_body is None and disp != 'attachment':
                    html_body = part.get_payload(decode=True).decode(charset, errors='replace')
                elif ct == 'text/plain' and text_body is None and disp != 'attachment':
                    text_body = part.get_payload(decode=True).decode(charset, errors='replace')
                elif fname:
                    payload = part.get_payload(decode=True) or b''
                    attachments.append({
                        'name': _decode_str(fname),
                        'size': len(payload),
                        'content_type': ct,
                        'disposition': disp,
                        'content_id': part.get('Content-ID'),
                        'data': payload,
                    })
        else:
            ct = msg.get_content_type()
            charset = msg.get_content_charset() or 'utf-8'
            payload = msg.get_payload(decode=True).decode(charset, errors='replace')
            if ct == 'text/html':
                html_body = payload
            else:
                text_body = payload
        return html_body, text_body, attachments

    def mark_as_read(self, uid, folder='INBOX'):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)))
            imap.uid('store', uid.encode(), '+FLAGS', '(\\Seen)')

    def mark_as_unread(self, uid, folder='INBOX'):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)))
            imap.uid('store', uid.encode(), '-FLAGS', '(\\Seen)')

    def delete_message(self, uid, folder='INBOX'):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)))
            trash = self._resolve_folder('[Gmail]/Trash')
            imap.uid('copy', uid.encode(), _imap_folder(trash))
            imap.uid('store', uid.encode(), '+FLAGS', '(\\Deleted)')
            imap.expunge()

    def get_unread_count(self, folder='INBOX'):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            _, data = imap.uid('search', None, 'UNSEEN')
            return len(data[0].split())

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
        auth_str = f'user={self.identity}\x01auth=Bearer {token}\x01\x01'
        smtp = smtplib.SMTP('smtp.gmail.com', 587, timeout=_GMAIL_SMTP_TIMEOUT_SECS)
        try:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth_str.encode()).decode())
            recipients = _normalize_recipients(to) + _normalize_recipients(cc) + _normalize_recipients(bcc)
            smtp.sendmail(self.identity, recipients, msg.as_bytes())
        finally:
            try:
                smtp.quit()
            except Exception:
                pass

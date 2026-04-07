"""Microsoft Graph provider implementation."""

import base64
import json
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from ..accounts.auth.goa_oauth import get_goa_access_token
    from ..sync_state import get_account_state, set_account_state
    from .common import (
        _aware_utc_datetime,
        _normalize_recipients,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )
except ImportError:
    from accounts.auth.goa_oauth import get_goa_access_token
    from sync_state import get_account_state, set_account_state
    from providers.common import (
        _aware_utc_datetime,
        _normalize_recipients,
        _utcnow_iso,
        coerce_account_descriptor,
        ensure_network_ready,
        network_ready,
    )


_GRAPH_REQUEST_TIMEOUT_SECS = 20
_GRAPH_INLINE_ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024
_GRAPH_UPLOAD_CHUNK_BYTES = 3 * 1024 * 1024
_SYNC_RECENT_MESSAGES_LIMIT = 100
_GRAPH_SYNC_RETENTION_DAYS = 21
_GRAPH_SYNC_CUSTOM_FOLDER_LIMIT = 24


class MicrosoftBackend:
    BASE = 'https://graph.microsoft.com/v1.0'
    _MESSAGE_SELECT = (
        'id,subject,from,toRecipients,ccRecipients,'
        'receivedDateTime,isRead,bodyPreview,hasAttachments,conversationId,internetMessageId'
    )
    FOLDERS = [
        ('inbox', 'Inbox', 'mail-inbox-symbolic'),
        ('sentitems', 'Sent', 'mail-send-symbolic'),
        ('drafts', 'Drafts', 'accessories-text-editor-symbolic'),
        ('deleteditems', 'Trash', 'user-trash-symbolic'),
        ('junkemail', 'Spam', 'mail-mark-junk-symbolic'),
    ]
    _STANDARD_NAMES = {'Inbox', 'Sent Items', 'Drafts', 'Deleted Items', 'Junk Email', 'Outbox'}
    _STANDARD_FOLDER_IDS = frozenset(folder_id for folder_id, _name, _icon in FOLDERS)

    def __init__(self, account_source):
        descriptor = coerce_account_descriptor(account_source, 'microsoft-graph')
        self.account_descriptor = descriptor
        self.goa_obj = descriptor.source_obj
        self.account = self.goa_obj.get_account()
        self.identity = descriptor.identity
        self.provider = 'microsoft'
        self._cached_token = None
        self._token_expiry = 0
        self._sync_lock = threading.Lock()
        sync_state = get_account_state('microsoft', self.identity)
        folder_states = sync_state.get('folders', {})
        self._folder_sync = {}
        for folder, folder_state in folder_states.items():
            if not folder:
                continue
            self._folder_sync[folder] = {
                'messages': self._deserialize_sync_messages(folder_state.get('messages', [])),
                'delta_link': folder_state.get('delta_link') or '',
                'bootstrap_inflight': False,
                'last_accessed_at': folder_state.get('last_accessed_at') or '',
            }

    def _serialize_sync_messages(self, messages):
        serial = []
        for msg in (messages or [])[:_SYNC_RECENT_MESSAGES_LIMIT]:
            serial.append({
                'uid': msg.get('uid', ''),
                'subject': msg.get('subject', '(no subject)'),
                'sender_name': msg.get('sender_name', ''),
                'sender_email': msg.get('sender_email', ''),
                'to_addrs': msg.get('to_addrs', []),
                'cc_addrs': msg.get('cc_addrs', []),
                'date': (msg.get('date').isoformat() if msg.get('date') else ''),
                'is_read': msg.get('is_read', True),
                'has_attachments': msg.get('has_attachments', False),
                'snippet': msg.get('snippet', ''),
                'folder': msg.get('folder', 'inbox'),
                'thread_id': msg.get('thread_id', ''),
                'thread_source': msg.get('thread_source', 'microsoft-graph'),
                'message_id': msg.get('message_id', ''),
            })
        return serial

    def _deserialize_sync_messages(self, messages):
        restored = []
        for msg in messages or []:
            try:
                date = _aware_utc_datetime(
                    datetime.fromisoformat(msg.get('date')) if msg.get('date') else None
                )
            except Exception:
                date = datetime.now(timezone.utc)
            restored.append({
                'uid': msg.get('uid', ''),
                'subject': msg.get('subject', '(no subject)'),
                'sender_name': msg.get('sender_name', ''),
                'sender_email': msg.get('sender_email', ''),
                'to_addrs': msg.get('to_addrs', []),
                'cc_addrs': msg.get('cc_addrs', []),
                'date': date,
                'is_read': msg.get('is_read', True),
                'has_attachments': msg.get('has_attachments', False),
                'snippet': msg.get('snippet', ''),
                'folder': msg.get('folder', 'inbox'),
                'backend': 'microsoft',
                'account': self.identity,
                'backend_obj': self,
                'thread_id': msg.get('thread_id', ''),
                'thread_source': msg.get('thread_source', 'microsoft-graph'),
                'message_id': msg.get('message_id', ''),
            })
        restored.sort(key=lambda item: _aware_utc_datetime(item.get('date')), reverse=True)
        return restored[:_SYNC_RECENT_MESSAGES_LIMIT]

    def _persist_sync_state(self):
        with self._sync_lock:
            now = datetime.now(timezone.utc)
            custom_folder_rows = []
            pruned_folder_sync = {}
            for folder, folder_state in self._folder_sync.items():
                normalized_state = {
                    'messages': list(folder_state.get('messages', [])),
                    'delta_link': folder_state.get('delta_link') or '',
                    'bootstrap_inflight': bool(folder_state.get('bootstrap_inflight')),
                    'last_accessed_at': folder_state.get('last_accessed_at') or '',
                }
                if folder in self._STANDARD_FOLDER_IDS:
                    pruned_folder_sync[folder] = normalized_state
                    continue
                last_accessed_raw = normalized_state['last_accessed_at']
                try:
                    last_accessed = datetime.fromisoformat(last_accessed_raw) if last_accessed_raw else now
                except Exception:
                    last_accessed = now
                age_days = (now - last_accessed).days
                if age_days > _GRAPH_SYNC_RETENTION_DAYS:
                    continue
                custom_folder_rows.append((folder, normalized_state, last_accessed))
            custom_folder_rows.sort(key=lambda item: item[2], reverse=True)
            for folder, normalized_state, _last_accessed in custom_folder_rows[:_GRAPH_SYNC_CUSTOM_FOLDER_LIMIT]:
                pruned_folder_sync[folder] = normalized_state
            self._folder_sync = pruned_folder_sync
            folders = {}
            for folder, folder_state in self._folder_sync.items():
                messages = self._serialize_sync_messages(folder_state.get('messages', []))
                delta_link = folder_state.get('delta_link') or ''
                last_accessed_at = folder_state.get('last_accessed_at') or ''
                if not messages and not delta_link and not last_accessed_at:
                    continue
                state_row = {'delta_link': delta_link, 'messages': messages}
                if last_accessed_at:
                    state_row['last_accessed_at'] = last_accessed_at
                folders[folder] = state_row
            state = {'folders': folders} if folders else {}
        set_account_state('microsoft', self.identity, state)

    def _folder_sync_state(self, folder):
        return self._folder_sync.setdefault(
            folder,
            {
                'messages': [],
                'delta_link': '',
                'bootstrap_inflight': False,
                'last_accessed_at': '',
            },
        )

    def _update_folder_sync_state(self, folder, messages=None, delta_link=None):
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder)
            if messages is not None:
                ordered = sorted(
                    list(messages or []),
                    key=lambda item: item.get('date') or datetime.now(timezone.utc),
                    reverse=True,
                )
                folder_state['messages'] = ordered[:_SYNC_RECENT_MESSAGES_LIMIT]
            if delta_link is not None:
                folder_state['delta_link'] = delta_link
            folder_state['last_accessed_at'] = _utcnow_iso()
        self._persist_sync_state()

    def update_cached_message_read_state(self, folder, uid, is_read):
        changed = False
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder)
            for msg in folder_state.get('messages', []):
                if msg.get('uid') != uid:
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
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder)
            before = len(folder_state.get('messages', []))
            folder_state['messages'] = [msg for msg in folder_state.get('messages', []) if msg.get('uid') != uid]
            removed = len(folder_state['messages']) != before
            if removed:
                folder_state['last_accessed_at'] = _utcnow_iso()
        if removed:
            self._persist_sync_state()
        return removed

    def _message_from_graph(self, message, folder='inbox'):
        from_info = message.get('from', {}).get('emailAddress', {})
        try:
            date = datetime.fromisoformat(message['receivedDateTime'].replace('Z', '+00:00'))
        except Exception:
            date = datetime.now(timezone.utc)

        def _ms_addrs(key):
            return [
                {
                    'name': row.get('emailAddress', {}).get('name', ''),
                    'email': row.get('emailAddress', {}).get('address', ''),
                }
                for row in message.get(key, [])
                if row.get('emailAddress', {}).get('address')
            ]

        return {
            'uid': message['id'],
            'subject': message.get('subject') or '(no subject)',
            'sender_name': from_info.get('name') or from_info.get('address', 'Unknown'),
            'sender_email': from_info.get('address', ''),
            'to_addrs': _ms_addrs('toRecipients'),
            'cc_addrs': _ms_addrs('ccRecipients'),
            'date': date,
            'is_read': message.get('isRead', True),
            'has_attachments': message.get('hasAttachments', False),
            'snippet': message.get('bodyPreview', ''),
            'folder': folder,
            'backend': 'microsoft',
            'account': self.identity,
            'backend_obj': self,
            'thread_id': message.get('conversationId') or '',
            'thread_source': 'microsoft-graph',
            'message_id': message.get('internetMessageId') or '',
        }

    def _mail_folder_ref(self, folder):
        return urllib.parse.quote(str(folder), safe='')

    def _fetch_messages_full(self, folder='inbox', limit=50):
        data = self._get(
            f'/me/mailFolders/{self._mail_folder_ref(folder)}/messages'
            f'?$top={limit}&$orderby=receivedDateTime+desc'
            f'&$select={self._MESSAGE_SELECT}'
        )
        return [self._message_from_graph(message, folder) for message in data.get('value', [])]

    def _run_folder_delta(self, folder, delta_link=None, return_delta_info=False):
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder)
            current_messages = {msg['uid']: dict(msg) for msg in folder_state.get('messages', [])}
            next_ref = delta_link or (
                f'/me/mailFolders/{self._mail_folder_ref(folder)}/messages/delta'
                f'?$top=100&$orderby=receivedDateTime+desc&$select={self._MESSAGE_SELECT}'
            )
        latest_delta_link = delta_link or ''
        delta_info = {
            'added_ids': set(),
            'removed_ids': set(),
            'touched_ids': set(),
        }
        while next_ref:
            data = self._get(next_ref)
            for item in data.get('value', []):
                item_id = item.get('id')
                if not item_id:
                    continue
                delta_info['touched_ids'].add(item_id)
                if item.get('@removed') is not None:
                    delta_info['removed_ids'].add(item_id)
                    current_messages.pop(item_id, None)
                    continue
                if item_id not in current_messages:
                    delta_info['added_ids'].add(item_id)
                current_messages[item_id] = self._message_from_graph(item, folder)
            next_ref = data.get('@odata.nextLink')
            if not next_ref:
                latest_delta_link = data.get('@odata.deltaLink') or latest_delta_link
        ordered = sorted(
            current_messages.values(),
            key=lambda item: item.get('date') or datetime.now(timezone.utc),
            reverse=True,
        )[:_SYNC_RECENT_MESSAGES_LIMIT]
        if return_delta_info:
            return ordered, latest_delta_link, delta_info
        return ordered, latest_delta_link

    def _bootstrap_folder_delta_state(self, folder):
        try:
            messages, delta_link = self._run_folder_delta(folder)
            if delta_link:
                self._update_folder_sync_state(folder, messages=messages, delta_link=delta_link)
        except Exception:
            pass
        finally:
            with self._sync_lock:
                self._folder_sync_state(folder)['bootstrap_inflight'] = False

    def _ensure_folder_delta_bootstrap_async(self, folder):
        with self._sync_lock:
            folder_state = self._folder_sync_state(folder)
            if folder_state.get('delta_link') or folder_state.get('bootstrap_inflight'):
                return
            folder_state['bootstrap_inflight'] = True
        threading.Thread(target=self._bootstrap_folder_delta_state, args=(folder,), daemon=True).start()

    def _token(self):
        now = _time.monotonic()
        if self._cached_token and now < self._token_expiry:
            return self._cached_token
        token = get_goa_access_token(self.goa_obj, self.account, network_ready_fn=network_ready)
        self._cached_token = token
        self._token_expiry = now + 3300
        return token

    def _invalidate_token(self):
        self._cached_token = None
        self._token_expiry = 0

    def _request_raw(self, path, method='GET', data=None, headers=None, authenticated=True):
        url = path if path.startswith(('https://', 'http://')) else f'{self.BASE}{path}'
        attempts = 2 if authenticated else 1
        for attempt in range(attempts):
            ensure_network_ready()
            req_headers = dict(headers or {})
            if authenticated:
                token = self._token()
                req_headers.setdefault('Authorization', f'Bearer {token}')
            req = urllib.request.Request(
                url,
                data=data,
                headers=req_headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=_GRAPH_REQUEST_TIMEOUT_SECS) as response:
                    return response.read(), response.headers
            except urllib.error.HTTPError as exc:
                if authenticated and exc.code in (401, 403) and attempt == 0:
                    self._invalidate_token()
                    continue
                raise

    def _request(self, path, method='GET', data=None):
        headers = {'Accept': 'application/json'}
        payload = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            payload = json.dumps(data).encode()
        raw, _headers = self._request_raw(path, method=method, data=payload, headers=headers)
        return json.loads(raw) if raw else {}

    def _get(self, path):
        return self._request(path)

    def _get_paged(self, path):
        items = []
        next_ref = path
        while next_ref:
            data = self._get(next_ref)
            items.extend(data.get('value', []))
            next_ref = data.get('@odata.nextLink')
        return items

    def _post(self, path, data=None):
        return self._request(path, 'POST', data)

    def _patch(self, path, data):
        return self._request(path, 'PATCH', data)

    def _delete(self, path):
        for attempt in range(2):
            token = self._token()
            req = urllib.request.Request(
                path if path.startswith(('https://', 'http://')) else f'{self.BASE}{path}',
                headers={'Authorization': f'Bearer {token}'},
                method='DELETE',
            )
            try:
                with urllib.request.urlopen(req, timeout=_GRAPH_REQUEST_TIMEOUT_SECS):
                    return
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and attempt == 0:
                    self._invalidate_token()
                    continue
                raise

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        data = {'value': self._get_paged('/me/mailFolders?$top=100&$select=id,displayName')}
        extra = []
        for folder in data.get('value', []):
            if folder.get('displayName') not in self._STANDARD_NAMES:
                extra.append((folder['id'], folder['displayName'], 'folder-symbolic'))
        return extra

    def fetch_messages(self, folder='inbox', limit=50):
        if folder:
            with self._sync_lock:
                folder_state = self._folder_sync_state(folder)
                delta_link = folder_state.get('delta_link') or ''
                folder_state['last_accessed_at'] = _utcnow_iso()
            use_delta_cache = int(limit) <= _SYNC_RECENT_MESSAGES_LIMIT
            if delta_link and use_delta_cache:
                try:
                    messages, latest_delta_link = self._run_folder_delta(folder, delta_link)
                    self._update_folder_sync_state(folder, messages=messages, delta_link=latest_delta_link)
                    return list(messages[:limit])
                except urllib.error.HTTPError as exc:
                    if exc.code in (400, 404, 410):
                        self._update_folder_sync_state(folder, delta_link='')
                    else:
                        raise
            internal_limit = max(limit, _SYNC_RECENT_MESSAGES_LIMIT)
            messages = self._fetch_messages_full(folder, internal_limit)
            self._update_folder_sync_state(folder, messages=messages)
            self._ensure_folder_delta_bootstrap_async(folder)
            return messages[:limit]
        return self._fetch_messages_full(folder, limit)

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
            with self._sync_lock:
                folder_state = self._folder_sync_state(folder)
                delta_link = folder_state.get('delta_link') or ''
                folder_state['last_accessed_at'] = _utcnow_iso()
            if not delta_link:
                self._ensure_folder_delta_bootstrap_async(folder)
                continue
            try:
                messages, latest_delta_link, delta_info = self._run_folder_delta(
                    folder,
                    delta_link,
                    return_delta_info=True,
                )
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 404, 410):
                    self._update_folder_sync_state(folder, delta_link='')
                    self._ensure_folder_delta_bootstrap_async(folder)
                    continue
                raise
            self._update_folder_sync_state(folder, messages=messages, delta_link=latest_delta_link)
            if delta_info['touched_ids'] or delta_info['removed_ids']:
                changed_folders.add(folder)
            if folder == 'inbox':
                refreshed_by_uid = {msg.get('uid'): msg for msg in messages if msg.get('uid')}
                for uid in delta_info.get('added_ids', set()):
                    message = refreshed_by_uid.get(uid)
                    if message and not message.get('is_read', True):
                        new_messages.append(message)

        if reconcile_counts or 'inbox' in changed_folders:
            counts['inbox'] = self.get_unread_count('inbox')
        if reconcile_counts or 'deleteditems' in changed_folders:
            counts['trash'] = self.get_unread_count('deleteditems')
        if reconcile_counts or 'junkemail' in changed_folders:
            counts['spam'] = self.get_unread_count('junkemail')
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
        thread_filter = urllib.parse.quote(f"conversationId eq '{thread_id}'", safe="='")
        data = {'value': self._get_paged(
            '/me/messages'
            f'?$top=100&$filter={thread_filter}'
            f'&$select={self._MESSAGE_SELECT}'
        )}
        messages = [self._message_from_graph(message, 'inbox') for message in data.get('value', [])]
        messages.sort(key=lambda item: _aware_utc_datetime(item.get('date')))
        return messages

    def fetch_body(self, uid, folder=None):
        data = self._get(f'/me/messages/{uid}?$select=body,hasAttachments')
        body = data.get('body', {})
        content = body.get('content', '')
        html_body = content if body.get('contentType', 'text') == 'html' else None
        text_body = None if html_body else content
        attachments = []
        if data.get('hasAttachments'):
            att_data = {
                'value': self._get_paged(
                    f'/me/messages/{uid}/attachments'
                    f'?$select=id,name,size,contentType,isInline,contentId'
                )
            }
            for attachment_row in att_data.get('value', []):
                attachment = {
                    'attachment_id': attachment_row.get('id'),
                    'name': attachment_row.get('name', 'attachment'),
                    'size': attachment_row.get('size', 0),
                    'content_type': attachment_row.get('contentType', 'application/octet-stream'),
                    'disposition': 'inline' if attachment_row.get('isInline') else 'attachment',
                    'content_id': attachment_row.get('contentId'),
                    'data': b'',
                }
                if (
                    attachment['attachment_id']
                    and attachment['disposition'] != 'attachment'
                    and attachment['content_type'].startswith('image/')
                ):
                    try:
                        attachment['data'] = self.fetch_attachment_data(uid, attachment, folder) or b''
                    except Exception:
                        attachment['data'] = b''
                attachments.append(attachment)
        return html_body, text_body, attachments

    def fetch_attachment_data(self, uid, attachment, folder=None):
        attachment_id = (attachment or {}).get('attachment_id')
        if not attachment_id:
            raise RuntimeError('Attachment ID unavailable')
        quoted_id = urllib.parse.quote(str(attachment_id), safe='')
        raw, _headers = self._request_raw(
            f'/me/messages/{uid}/attachments/{quoted_id}/$value',
            headers={'Accept': 'application/octet-stream'},
        )
        return raw or b''

    def mark_as_read(self, uid, folder=None):
        self._patch(f'/me/messages/{uid}', {'isRead': True})

    def mark_as_unread(self, uid, folder=None):
        self._patch(f'/me/messages/{uid}', {'isRead': False})

    def delete_message(self, uid, folder=None):
        self._post(f'/me/messages/{uid}/move', {'destinationId': 'deleteditems'})

    def get_unread_count(self, folder='inbox'):
        data = self._get(f'/me/mailFolders/{self._mail_folder_ref(folder)}?$select=unreadItemCount')
        return data.get('unreadItemCount', 0)

    def fetch_contacts(self, query=''):
        try:
            ensure_network_ready()
            path = '/me/people?$top=15&$select=displayName,scoredEmailAddresses'
            if query:
                search_query = urllib.parse.quote(f'"{query}"', safe='')
                path += f'&$search={search_query}'
            data = self._get(path)
            contacts = []
            query_lower = query.lower()
            for person in data.get('value', []):
                name = person.get('displayName', '')
                for scored in person.get('scoredEmailAddresses', []):
                    addr = scored.get('address', '')
                    if addr and (not query_lower or query_lower in addr.lower() or query_lower in name.lower()):
                        contacts.append({'name': name, 'email': addr})
            return contacts
        except Exception:
            return []

    def _file_attachment_payload(self, attachment):
        return {
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': attachment['name'],
            'contentType': attachment.get('content_type', 'application/octet-stream'),
            'contentBytes': base64.b64encode(attachment['data']).decode('ascii'),
        }

    def _build_message_payload(self, to, subject, body, html=None, cc=None, bcc=None, reply_to_msg=None, attachments=None):
        recipients = [{'emailAddress': {'address': email}} for email in _normalize_recipients(to)]
        cc_recipients = [{'emailAddress': {'address': email}} for email in _normalize_recipients(cc)]
        bcc_recipients = [{'emailAddress': {'address': email}} for email in _normalize_recipients(bcc)]
        headers = []
        reply_msgid = (reply_to_msg or {}).get('message_id', '') if reply_to_msg else ''
        if reply_msgid:
            headers.append({'name': 'In-Reply-To', 'value': reply_msgid})
            headers.append({'name': 'References', 'value': reply_msgid})
        message = {
            'subject': subject,
            'body': {'contentType': 'HTML' if html else 'Text', 'content': html or body},
            'toRecipients': recipients,
        }
        if cc_recipients:
            message['ccRecipients'] = cc_recipients
        if bcc_recipients:
            message['bccRecipients'] = bcc_recipients
        if headers:
            message['internetMessageHeaders'] = headers
        if attachments:
            message['attachments'] = [self._file_attachment_payload(att) for att in attachments]
        return message

    def _create_draft_message(self, message):
        created = self._post('/me/messages', message)
        message_id = created.get('id')
        if not message_id:
            raise RuntimeError('Microsoft draft create did not return a message id')
        return message_id

    def _upload_large_attachment(self, message_id, attachment):
        session = self._post(
            f'/me/messages/{message_id}/attachments/createUploadSession',
            {
                'AttachmentItem': {
                    'attachmentType': 'file',
                    'name': attachment['name'],
                    'size': len(attachment.get('data') or b''),
                }
            },
        )
        upload_url = session.get('uploadUrl')
        if not upload_url:
            raise RuntimeError(f'Upload session missing uploadUrl for {attachment["name"]}')
        data = attachment.get('data') or b''
        total = len(data)
        start = 0
        while start < total:
            end = min(start + _GRAPH_UPLOAD_CHUNK_BYTES, total) - 1
            chunk = data[start:end + 1]
            raw, _headers = self._request_raw(
                upload_url,
                method='PUT',
                data=chunk,
                headers={
                    'Content-Type': 'application/octet-stream',
                    'Content-Length': str(len(chunk)),
                    'Content-Range': f'bytes {start}-{end}/{total}',
                },
                authenticated=False,
            )
            if raw:
                try:
                    response = json.loads(raw)
                except Exception:
                    response = {}
                next_ranges = response.get('nextExpectedRanges') or []
                if next_ranges:
                    next_start = str(next_ranges[0]).split('-')[0]
                    try:
                        start = int(next_start)
                        continue
                    except Exception:
                        pass
            start = end + 1

    def _send_draft_message(self, message_id):
        self._request_raw(
            f'/me/messages/{message_id}/send',
            method='POST',
            data=b'',
            headers={'Content-Length': '0'},
        )

    def send_message(self, to, subject, body, html=None, cc=None, bcc=None, reply_to_msg=None, attachments=None):
        ensure_network_ready()
        attachments = list(attachments or [])
        small_attachments = [
            att for att in attachments
            if len(att.get('data') or b'') < _GRAPH_INLINE_ATTACHMENT_MAX_BYTES
        ]
        large_attachments = [
            att for att in attachments
            if len(att.get('data') or b'') >= _GRAPH_INLINE_ATTACHMENT_MAX_BYTES
        ]
        if not large_attachments:
            self._post('/me/sendMail', {
                'message': self._build_message_payload(
                    to, subject, body, html=html, cc=cc, bcc=bcc,
                    reply_to_msg=reply_to_msg, attachments=small_attachments,
                )
            })
            return
        message_id = self._create_draft_message(
            self._build_message_payload(
                to, subject, body, html=html, cc=cc, bcc=bcc,
                reply_to_msg=reply_to_msg, attachments=small_attachments,
            )
        )
        for attachment in large_attachments:
            self._upload_large_attachment(message_id, attachment)
        self._send_draft_message(message_id)

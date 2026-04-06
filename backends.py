import base64, imaplib, ssl, json, re, smtplib, threading
import time as _time
import urllib.error
import urllib.request
import email as email_parser
from email.header import decode_header as _decode_header_raw
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

import gi
gi.require_version('Goa', '1.0')
gi.require_version('Gio', '2.0')
from gi.repository import Goa, Gio


def get_backends():
    client = Goa.Client.new_sync(None)
    backends = []
    for obj in client.get_accounts():
        acc = obj.get_account()
        if acc.props.mail_disabled:
            continue
        if not obj.get_oauth2_based():
            continue
        provider = acc.props.provider_type
        if provider == 'google':
            backends.append(GmailBackend(obj))
        elif provider == 'ms_graph':
            backends.append(MicrosoftBackend(obj))
    return backends


def _decode_str(value):
    if not value:
        return ''
    parts = _decode_header_raw(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)


def _parse_addrs(header_val):
    if not header_val:
        return []
    return [{'name': n or e, 'email': e}
            for n, e in email_parser.utils.getaddresses([header_val]) if e]


def _normalize_recipients(value):
    if not value:
        return []
    if isinstance(value, str):
        return [addr['email'] for addr in _parse_addrs(value)]
    return [addr['email'] for addr in value if addr.get('email')]


def _imap_folder(name):
    """Quote IMAP folder names that contain spaces."""
    return f'"{name}"' if ' ' in name else name


def network_ready():
    try:
        monitor = Gio.NetworkMonitor.get_default()
        if not monitor.get_network_available():
            return False
        connectivity = monitor.get_connectivity()
        return connectivity != Gio.NetworkConnectivity.LOCAL
    except Exception:
        return True


def ensure_network_ready():
    if not network_ready():
        raise RuntimeError('network not ready')


def _goa_token(goa_obj, account, retries=3, wait=10):
    """Get GOA OAuth2 token, retrying on status-0 errors (network not up after sleep)."""
    for attempt in range(retries):
        if not network_ready():
            if attempt + 1 < retries:
                delay = min(2, max(1, wait * attempt if attempt else 1))
                print(f'GOA preflight waiting for network… retrying in {delay}s')
                _time.sleep(delay)
                continue
            raise RuntimeError('network not ready')
        try:
            account.call_ensure_credentials_sync(None)
            return goa_obj.get_oauth2_based().call_get_access_token_sync(None)[0]
        except Exception as e:
            text = str(e).lower()
            transient = (
                'status 0' in text
                or '((null))' in text
                or 'expected status 200 when requesting access token' in text
                or 'network not ready' in text
            )
            if attempt + 1 < retries and transient:
                delay = wait * (attempt + 1)
                print(f'Token fetch failed, retrying in {delay}s… ({e})')
                _time.sleep(delay)
            else:
                raise
    raise RuntimeError('unreachable')


def is_transient_network_error(exc):
    text = str(exc).lower()
    return any(token in text for token in (
        'status 0',
        '((null))',
        'expected status 200 when requesting access token',
        'temporary failure in name resolution',
        'name resolution',
        'network is unreachable',
        'connection reset',
        'timed out',
        'temporarily unavailable',
        'could not connect',
    ))


def _decode_imap_utf7(s):
    """Decode IMAP modified UTF-7 encoded strings (e.g. '&AOQ-' → 'ä')."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == '&':
            j = s.find('-', i + 1)
            if j == -1:
                result.append(s[i:])
                break
            encoded = s[i+1:j]
            if encoded == '':
                result.append('&')
            else:
                # IMAP modified UTF-7: uses ',' instead of '/'
                b64 = encoded.replace(',', '/')
                # Pad to multiple of 4
                pad = (4 - len(b64) % 4) % 4
                decoded = base64.b64decode(b64 + '=' * pad).decode('utf-16-be')
                result.append(decoded)
            i = j + 1
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


# IMAP special-use flag → logical folder key (matches FOLDERS[*][0])
_SPECIAL_USE_MAP = {
    '\\Sent':      '[Gmail]/Sent Mail',
    '\\Drafts':    '[Gmail]/Drafts',
    '\\Trash':     '[Gmail]/Trash',
    '\\Junk':      '[Gmail]/Spam',
    '\\All':       None,   # exclude from folder list
    '\\Flagged':   None,
    '\\Important': None,
}


class GmailBackend:
    FOLDERS = [
        ('INBOX',               'Inbox',  'mail-inbox-symbolic'),
        ('[Gmail]/Sent Mail',   'Sent',   'mail-send-symbolic'),
        ('[Gmail]/Drafts',      'Drafts', 'accessories-text-editor-symbolic'),
        ('[Gmail]/Trash',       'Trash',  'user-trash-symbolic'),
        ('[Gmail]/Spam',        'Spam',   'mail-mark-junk-symbolic'),
    ]
    _STANDARD_FOLDER_IDS = {f[0] for f in FOLDERS} | {'[Gmail]', '[Google Mail]'}

    def __init__(self, goa_obj):
        self.goa_obj = goa_obj
        self.account = goa_obj.get_account()
        self.identity = self.account.props.presentation_identity
        self.provider = 'gmail'
        self._imap = None
        self._lock = threading.Lock()
        self._special_folders = {}  # logical key → actual IMAP name

    def _token(self):
        return _goa_token(self.goa_obj, self.account)

    def _get_imap(self):
        ensure_network_ready()
        if self._imap is not None:
            try:
                self._imap.noop()
                return self._imap
            except Exception:
                self._imap = None
        token = self._token()
        ctx = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL('imap.gmail.com', ssl_context=ctx)
        auth_str = f'user={self.identity}\x01auth=Bearer {token}\x01\x01'
        imap.authenticate('XOAUTH2', lambda x: auth_str.encode())
        self._imap = imap
        self._detect_special_folders(imap)
        return imap

    def _detect_special_folders(self, imap):
        """Parse IMAP LIST flags to map logical folder keys → actual localized names."""
        try:
            _, items = imap.list()
        except Exception:
            return
        self._special_folders = {}
        for item in items:
            if not isinstance(item, bytes):
                continue
            decoded = item.decode(errors='replace')
            # Extract flags e.g. (\HasNoChildren \Sent)
            flags_m = re.match(r'\(([^)]*)\)', decoded)
            if not flags_m:
                continue
            flags = flags_m.group(1)
            # Extract folder name (last quoted or unquoted token)
            name_m = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', decoded)
            if not name_m:
                continue
            actual = (name_m.group(1) or name_m.group(2)).strip('"')
            for flag, logical_key in _SPECIAL_USE_MAP.items():
                if re.search(re.escape(flag), flags, re.IGNORECASE):
                    if logical_key is not None:
                        self._special_folders[logical_key] = actual
                    # Always record under the flag itself for fetch_all_folders exclusion
                    self._special_folders.setdefault(f'_flag:{flag.lower()}', actual)

    def _resolve_folder(self, folder):
        """Map logical folder ID to actual IMAP name (handles locale variations)."""
        return self._special_folders.get(folder, folder)

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        with self._lock:
            imap = self._get_imap()
            _, items = imap.list()
        # Build set of actual special-folder IMAP names to exclude
        excluded_actuals = set(self._special_folders.values())
        extra = []
        for item in items:
            if not isinstance(item, bytes):
                continue
            decoded = item.decode(errors='replace')
            # Skip folders with \All, \Flagged, \Important flags (noise)
            flags_m = re.match(r'\(([^)]*)\)', decoded)
            if flags_m:
                flags_lower = flags_m.group(1).lower()
                if any(f in flags_lower for f in ('\\all', '\\flagged', '\\important')):
                    continue
            m = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', decoded)
            if not m:
                continue
            name = (m.group(1) or m.group(2)).strip('"')
            if name in self._STANDARD_FOLDER_IDS or name in excluded_actuals:
                continue
            display = _decode_imap_utf7(
                re.sub(r'^\[Gmail\]/', '', re.sub(r'^\[Google Mail\]/', '', name))
            )
            extra.append((name, display, 'folder-symbolic'))
        return extra

    def fetch_messages(self, folder='INBOX', limit=50):
        with self._lock:
            imap = self._get_imap()
            imap.select(_imap_folder(self._resolve_folder(folder)), readonly=True)
            _, data = imap.uid('search', None, 'ALL')
            uids = data[0].split()
            if not uids:
                return []
            uids = uids[-limit:]
            uid_str = b','.join(uids)
            _, fetch_data = imap.uid(
                'fetch', uid_str,
                '(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE CONTENT-TYPE)])'
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
            idx += 1
            parsed = email_parser.message_from_bytes(raw_headers)
            subject = _decode_str(parsed.get('Subject', '(no subject)'))
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
                date = email_parser.utils.parsedate_to_datetime(date_str)
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
            })
        messages.reverse()
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
        """Fetch contacts via Google People API (requires contacts scope on GOA token)."""
        try:
            ensure_network_ready()
            token = self._token()
            url = (
                'https://people.googleapis.com/v1/people/me/connections'
                '?personFields=names,emailAddresses&pageSize=100'
            )
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            contacts = []
            q = query.lower()
            for conn in data.get('connections', []):
                names = conn.get('names', [{}])
                name = names[0].get('displayName', '') if names else ''
                for e in conn.get('emailAddresses', []):
                    addr = e.get('value', '')
                    if addr and (not q or q in addr.lower() or q in name.lower()):
                        contacts.append({'name': name, 'email': addr})
            return contacts[:15]
        except Exception:
            return []

    def send_message(self, to, subject, body, html=None, cc=None, bcc=None):
        ensure_network_ready()
        token = self._token()
        msg = MIMEMultipart('alternative')
        msg['From'] = self.identity
        msg['To'] = to
        msg['Subject'] = subject
        if cc:
            msg['Cc'] = ', '.join(_normalize_recipients(cc))
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if html:
            msg.attach(MIMEText(html, 'html', 'utf-8'))
        auth_str = f'user={self.identity}\x01auth=Bearer {token}\x01\x01'
        smtp = smtplib.SMTP('smtp.gmail.com', 587)
        try:
            smtp.starttls()
            smtp.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth_str.encode()).decode())
            recipients = _normalize_recipients(to) + _normalize_recipients(cc) + _normalize_recipients(bcc)
            smtp.sendmail(self.identity, recipients, msg.as_bytes())
        finally:
            smtp.quit()


class MicrosoftBackend:
    BASE = 'https://graph.microsoft.com/v1.0'
    FOLDERS = [
        ('inbox',        'Inbox',  'mail-inbox-symbolic'),
        ('sentitems',    'Sent',   'mail-send-symbolic'),
        ('drafts',       'Drafts', 'accessories-text-editor-symbolic'),
        ('deleteditems', 'Trash',  'user-trash-symbolic'),
        ('junkemail',    'Spam',   'mail-mark-junk-symbolic'),
    ]
    _STANDARD_NAMES = {'Inbox', 'Sent Items', 'Drafts', 'Deleted Items', 'Junk Email', 'Outbox'}

    def __init__(self, goa_obj):
        self.goa_obj = goa_obj
        self.account = goa_obj.get_account()
        self.identity = self.account.props.presentation_identity
        self.provider = 'microsoft'
        self._cached_token = None
        self._token_expiry = 0

    def _token(self):
        now = _time.monotonic()
        if self._cached_token and now < self._token_expiry:
            return self._cached_token
        token = _goa_token(self.goa_obj, self.account)
        self._cached_token = token
        self._token_expiry = now + 3300  # cache 55 min
        return token

    def _invalidate_token(self):
        self._cached_token = None
        self._token_expiry = 0

    def _request(self, path, method='GET', data=None):
        for attempt in range(2):
            ensure_network_ready()
            token = self._token()
            req = urllib.request.Request(
                f'{self.BASE}{path}',
                data=json.dumps(data).encode() if data is not None else None,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json',
                    **(({'Content-Type': 'application/json'}) if data is not None else {}),
                },
                method=method,
            )
            try:
                with urllib.request.urlopen(req) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0:
                    self._invalidate_token()
                    continue
                raise

    def _get(self, path):
        return self._request(path)

    def _post(self, path, data):
        return self._request(path, 'POST', data)

    def _patch(self, path, data):
        return self._request(path, 'PATCH', data)

    def _delete(self, path):
        for attempt in range(2):
            token = self._token()
            req = urllib.request.Request(
                f'{self.BASE}{path}',
                headers={'Authorization': f'Bearer {token}'},
                method='DELETE',
            )
            try:
                with urllib.request.urlopen(req):
                    return
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0:
                    self._invalidate_token()
                    continue
                raise

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        ensure_network_ready()
        data = self._get('/me/mailFolders?$top=100&$select=id,displayName')
        extra = []
        for f in data.get('value', []):
            if f.get('displayName') not in self._STANDARD_NAMES:
                extra.append((f['id'], f['displayName'], 'folder-symbolic'))
        return extra

    def fetch_messages(self, folder='inbox', limit=50):
        data = self._get(
            f'/me/mailFolders/{folder}/messages'
            f'?$top={limit}&$orderby=receivedDateTime+desc'
            f'&$select=id,subject,from,toRecipients,ccRecipients,'
            f'receivedDateTime,isRead,bodyPreview,hasAttachments'
        )
        messages = []
        for m in data.get('value', []):
            from_info = m.get('from', {}).get('emailAddress', {})
            try:
                date = datetime.fromisoformat(m['receivedDateTime'].replace('Z', '+00:00'))
            except Exception:
                date = datetime.now(timezone.utc)

            def _ms_addrs(key):
                return [{'name': r.get('emailAddress', {}).get('name', ''),
                         'email': r.get('emailAddress', {}).get('address', '')}
                        for r in m.get(key, [])
                        if r.get('emailAddress', {}).get('address')]

            messages.append({
                'uid': m['id'],
                'subject': m.get('subject') or '(no subject)',
                'sender_name': from_info.get('name') or from_info.get('address', 'Unknown'),
                'sender_email': from_info.get('address', ''),
                'to_addrs': _ms_addrs('toRecipients'),
                'cc_addrs': _ms_addrs('ccRecipients'),
                'date': date,
                'is_read': m.get('isRead', True),
                'has_attachments': m.get('hasAttachments', False),
                'snippet': m.get('bodyPreview', ''),
                'folder': folder,
                'backend': 'microsoft',
                'account': self.identity,
                'backend_obj': self,
            })
        return messages

    def fetch_body(self, uid, folder=None):
        data = self._get(f'/me/messages/{uid}?$select=body,hasAttachments')
        body = data.get('body', {})
        content = body.get('content', '')
        html_body = content if body.get('contentType', 'text') == 'html' else None
        text_body = None if html_body else content
        attachments = []
        if data.get('hasAttachments'):
            att_data = self._get(
                f'/me/messages/{uid}/attachments'
                f'?$select=name,size,contentType,contentBytes'
            )
            for a in att_data.get('value', []):
                raw_b64 = a.get('contentBytes', '')
                decoded = base64.b64decode(raw_b64) if raw_b64 else b''
                attachments.append({
                    'name': a.get('name', 'attachment'),
                    'size': a.get('size', len(decoded)),
                    'content_type': a.get('contentType', 'application/octet-stream'),
                    'disposition': 'inline' if a.get('isInline') else 'attachment',
                    'content_id': a.get('contentId'),
                    'data': decoded,
                })
        return html_body, text_body, attachments

    def mark_as_read(self, uid, folder=None):
        self._patch(f'/me/messages/{uid}', {'isRead': True})

    def mark_as_unread(self, uid, folder=None):
        self._patch(f'/me/messages/{uid}', {'isRead': False})

    def delete_message(self, uid, folder=None):
        self._post(f'/me/messages/{uid}/move', {'destinationId': 'deleteditems'})

    def get_unread_count(self, folder='inbox'):
        data = self._get(f'/me/mailFolders/{folder}?$select=unreadItemCount')
        return data.get('unreadItemCount', 0)

    def fetch_contacts(self, query=''):
        """Fetch frequently contacted people via Microsoft Graph /me/people."""
        try:
            ensure_network_ready()
            path = '/me/people?$top=15&$select=displayName,scoredEmailAddresses'
            if query:
                path += f'&$search="{query}"'
            data = self._get(path)
            contacts = []
            q = query.lower()
            for p in data.get('value', []):
                name = p.get('displayName', '')
                for e in p.get('scoredEmailAddresses', []):
                    addr = e.get('address', '')
                    if addr and (not q or q in addr.lower() or q in name.lower()):
                        contacts.append({'name': name, 'email': addr})
            return contacts
        except Exception:
            return []

    def send_message(self, to, subject, body, html=None, cc=None, bcc=None):
        ensure_network_ready()
        recipients = [{'emailAddress': {'address': e}} for e in _normalize_recipients(to)]
        cc_recipients = [{'emailAddress': {'address': e}} for e in _normalize_recipients(cc)]
        bcc_recipients = [{'emailAddress': {'address': e}} for e in _normalize_recipients(bcc)]
        self._post('/me/sendMail', {
            'message': {
                'subject': subject,
                'body': {'contentType': 'HTML' if html else 'Text', 'content': html or body},
                'toRecipients': recipients,
                **({'ccRecipients': cc_recipients} if cc_recipients else {}),
                **({'bccRecipients': bcc_recipients} if bcc_recipients else {}),
            }
        })

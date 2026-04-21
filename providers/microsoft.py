"""Microsoft Graph provider implementation (Outlook / live.se)."""

from __future__ import annotations

import base64
import email.utils
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from ..accounts.auth.oauth_common import OAuthTokenAcquisitionError
    from ..diagnostics.logger import log_event
    from ..sync_state import get_account_state, set_account_state
    from .common import (
        _aware_utc_datetime,
        _parse_addrs,
        BodyFetchError,
        build_count_policy,
        build_sync_notice,
        build_sync_policy,
        classify_http_error,
        classify_oauth_token_error,
        coerce_account_descriptor,
        ensure_network_ready,
        messages_changed,
        network_ready,
        retry_delay_for_http_error,
        SyncHealthState,
    )
except ImportError:
    from accounts.auth.oauth_common import OAuthTokenAcquisitionError
    from diagnostics.logger import log_event
    from sync_state import get_account_state, set_account_state
    from providers.common import (
        _aware_utc_datetime,
        _parse_addrs,
        BodyFetchError,
        build_count_policy,
        build_sync_notice,
        build_sync_policy,
        classify_http_error,
        classify_oauth_token_error,
        coerce_account_descriptor,
        ensure_network_ready,
        messages_changed,
        network_ready,
        retry_delay_for_http_error,
        SyncHealthState,
    )


_MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MS_API_TIMEOUT_SECS = 15
_MS_TOKEN_CACHE_TTL_SECS = 3300
_RECENT_WINDOW = 100
_CACHE_MAX = 500
_MESSAGE_SELECT = ",".join(
    (
        "id",
        "conversationId",
        "subject",
        "bodyPreview",
        "from",
        "toRecipients",
        "ccRecipients",
        "receivedDateTime",
        "sentDateTime",
        "isRead",
        "hasAttachments",
        "internetMessageId",
        "parentFolderId",
    )
)


class MicrosoftGraphBackend:
    FOLDERS = [
        ("inbox", "Inbox", "mail-inbox-symbolic"),
        ("sentitems", "Sent", "mail-send-symbolic"),
        ("drafts", "Drafts", "accessories-text-editor-symbolic"),
        ("deleteditems", "Trash", "user-trash-symbolic"),
        ("junkemail", "Spam", "mail-mark-junk-symbolic"),
    ]

    def __init__(self, account_source):
        descriptor = coerce_account_descriptor(account_source, "microsoft-graph")
        self.account_descriptor = descriptor
        self.source_obj = descriptor.source_obj
        self.account = getattr(self.source_obj, "get_account", lambda: None)()
        self.identity = descriptor.identity
        self.presentation_name = descriptor.presentation_name or descriptor.identity
        self.accent_color = str(
            (descriptor.metadata or {}).get("accent_color") or ""
        ).strip()
        self.provider = "microsoft"
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._cached_token = None
        self._cached_token_expiry = 0.0
        self._folder_cache: dict[str, list] = {}
        self._folder_unread: dict[str, int] = {}
        self._sync_notices: list = []
        self._sync_health = SyncHealthState(
            provider="microsoft",
            account=self.identity,
            primary_label="Microsoft Graph",
            fallback_label="Unavailable",
        )
        self._sync_health.mark_ready("Ready")
        state = get_account_state("microsoft", self.identity)
        for folder, folder_state in (state.get("folders") or {}).items():
            messages = self._deserialize_cached(folder_state.get("messages", []))
            if messages:
                self._folder_cache[folder] = messages

    # ----- token handling -----

    def _token(self):
        now = time.monotonic()
        cached_token = self._cached_token
        cached_expiry = float(self._cached_token_expiry or 0.0)
        if cached_token and cached_expiry > now:
            return cached_token
        try:
            getter = getattr(self.source_obj, "get_access_token", None)
            if not callable(getter):
                raise OAuthTokenAcquisitionError(
                    "OAuth token source is unavailable",
                    stage="source lookup",
                    retryable=False,
                    source="microsoft",
                )
            token = getter(network_ready_fn=network_ready)
        except Exception:
            self._invalidate_token()
            raise
        self._cached_token = token
        self._cached_token_expiry = now + _MS_TOKEN_CACHE_TTL_SECS
        return token

    def _invalidate_token(self):
        self._cached_token = None
        self._cached_token_expiry = 0.0
        invalidator = getattr(self.source_obj, "invalidate_access_token", None)
        if callable(invalidator):
            try:
                invalidator()
            except Exception:
                pass

    # ----- graph request helpers -----

    def _graph_request(
        self,
        path,
        *,
        method="GET",
        params=None,
        json_body=None,
        timeout=_MS_API_TIMEOUT_SECS,
        _retry_auth=True,
    ):
        ensure_network_ready()
        url = _MS_GRAPH_BASE + path
        if params:
            url = url + "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and _retry_auth:
                self._invalidate_token()
                return self._graph_request(
                    path,
                    method=method,
                    params=params,
                    json_body=json_body,
                    timeout=timeout,
                    _retry_auth=False,
                )
            raise

    # ----- message conversion -----

    @staticmethod
    def _parse_datetime(value):
        value = str(value or "").strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return _aware_utc_datetime(parsed)

    @staticmethod
    def _address_parts(addr):
        if not isinstance(addr, dict):
            return "", ""
        inner = addr.get("emailAddress") or {}
        email = str(inner.get("address") or "").strip()
        name = str(inner.get("name") or "").strip()
        return name, email

    @classmethod
    def _address(cls, addr):
        name, email = cls._address_parts(addr)
        if not email:
            return ""
        return f"{name} <{email}>" if name and name != email else email

    def _message_from_graph(self, data, folder):
        if not isinstance(data, dict):
            return None
        uid = str(data.get("id") or "").strip()
        if not uid:
            return None
        sender_name, sender_email = self._address_parts(data.get("from"))
        from_header = self._address(data.get("from"))
        to_parts = [self._address(r) for r in (data.get("toRecipients") or [])]
        cc_parts = [self._address(r) for r in (data.get("ccRecipients") or [])]
        return {
            "uid": uid,
            "folder": folder,
            "provider": "microsoft",
            "account": self.identity,
            "backend_obj": self,
            "subject": str(data.get("subject") or "").strip(),
            "from": from_header,
            "sender_name": sender_name or sender_email or "Unknown sender",
            "sender_email": sender_email,
            "to": ", ".join(part for part in to_parts if part),
            "cc": ", ".join(part for part in cc_parts if part),
            "date": self._parse_datetime(data.get("receivedDateTime")),
            "preview": str(data.get("bodyPreview") or "").strip(),
            "is_read": bool(data.get("isRead")),
            "has_attachments": bool(data.get("hasAttachments")),
            "thread_id": str(data.get("conversationId") or "").strip() or uid,
            "message_id": str(data.get("internetMessageId") or "").strip(),
            "parent_folder_id": str(data.get("parentFolderId") or "").strip(),
        }

    def _serialize_cached(self, messages):
        out = []
        for msg in (messages or [])[:_CACHE_MAX]:
            if not isinstance(msg, dict):
                continue
            date = msg.get("date")
            out.append(
                {
                    "uid": str(msg.get("uid") or ""),
                    "folder": str(msg.get("folder") or ""),
                    "subject": str(msg.get("subject") or ""),
                    "from": str(msg.get("from") or ""),
                    "sender_name": str(msg.get("sender_name") or ""),
                    "sender_email": str(msg.get("sender_email") or ""),
                    "to": str(msg.get("to") or ""),
                    "cc": str(msg.get("cc") or ""),
                    "date": date.isoformat() if isinstance(date, datetime) else "",
                    "preview": str(msg.get("preview") or ""),
                    "is_read": bool(msg.get("is_read")),
                    "has_attachments": bool(msg.get("has_attachments")),
                    "thread_id": str(msg.get("thread_id") or ""),
                    "message_id": str(msg.get("message_id") or ""),
                    "parent_folder_id": str(msg.get("parent_folder_id") or ""),
                }
            )
        return out

    def _deserialize_cached(self, messages):
        out = []
        for msg in (messages or [])[:_CACHE_MAX]:
            if not isinstance(msg, dict):
                continue
            uid = str(msg.get("uid") or "").strip()
            if not uid:
                continue
            date = self._parse_datetime(msg.get("date"))
            from_value = str(msg.get("from") or "")
            sender_name = str(msg.get("sender_name") or "").strip()
            sender_email = str(msg.get("sender_email") or "").strip()
            if not sender_email and from_value:
                parsed_name, parsed_email = email.utils.parseaddr(from_value)
                sender_name = sender_name or parsed_name
                sender_email = parsed_email
            out.append(
                {
                    "uid": uid,
                    "folder": str(msg.get("folder") or ""),
                    "provider": "microsoft",
                    "account": self.identity,
                    "backend_obj": self,
                    "subject": str(msg.get("subject") or ""),
                    "from": from_value,
                    "sender_name": sender_name or sender_email or "Unknown sender",
                    "sender_email": sender_email,
                    "to": str(msg.get("to") or ""),
                    "cc": str(msg.get("cc") or ""),
                    "date": date,
                    "preview": str(msg.get("preview") or ""),
                    "is_read": bool(msg.get("is_read")),
                    "has_attachments": bool(msg.get("has_attachments")),
                    "thread_id": str(msg.get("thread_id") or uid),
                    "message_id": str(msg.get("message_id") or ""),
                    "parent_folder_id": str(msg.get("parent_folder_id") or ""),
                }
            )
        return out

    def _persist_sync_state(self, immediate=False):
        with self._sync_lock:
            folders = {}
            for folder, messages in self._folder_cache.items():
                serialized = self._serialize_cached(messages)
                if serialized:
                    folders[folder] = {"messages": serialized}
            state = {"folders": folders} if folders else {}
        set_account_state("microsoft", self.identity, state)

    # ----- sync notice plumbing -----

    def _set_sync_notice(self, kind, detail=None):
        if isinstance(kind, dict) and detail is None:
            notice = build_sync_notice(kind.get("kind"), kind.get("detail"))
        else:
            notice = build_sync_notice(kind, detail)
        with self._sync_lock:
            self._sync_notices.append(notice)

    def consume_sync_notices(self):
        with self._sync_lock:
            notices = list(self._sync_notices)
            self._sync_notices = []
        return notices

    def consume_sync_notice(self):
        notices = self.consume_sync_notices()
        return notices[0] if notices else None

    # ----- public protocol -----

    def get_folder_list(self):
        return self.FOLDERS

    def fetch_all_folders(self):
        try:
            data = self._graph_request(
                "/me/mailFolders",
                params={"$top": "100"},
            ) or {}
        except Exception:
            return []
        extra = []
        standard_ids = {fid for fid, _n, _i in self.FOLDERS}
        for folder in data.get("value") or []:
            name = str(folder.get("displayName") or "").strip()
            fid = str(folder.get("id") or "").strip()
            if not name or not fid:
                continue
            if name.lower() in standard_ids:
                continue
            extra.append((fid, name, "folder-symbolic"))
        extra.sort(key=lambda item: item[1].lower())
        return extra

    def get_cached_messages(self, folder="inbox", limit=50):
        with self._sync_lock:
            return list(self._folder_cache.get(folder, []))[:limit]

    def fetch_messages(self, folder="inbox", limit=50):
        limit = max(1, min(int(limit), _RECENT_WINDOW))
        path = f"/me/mailFolders/{urllib.parse.quote(folder)}/messages"
        try:
            data = self._graph_request(
                path,
                params={
                    "$select": _MESSAGE_SELECT,
                    "$top": str(limit),
                    "$orderby": "receivedDateTime desc",
                },
            ) or {}
        except urllib.error.HTTPError as exc:
            self._sync_health.mark_warning(
                "Graph list failed",
                code=str(exc.code),
                retryable=exc.code in (429, 500, 502, 503, 504),
                retry_after_seconds=retry_delay_for_http_error(exc),
            )
            self._set_sync_notice(classify_http_error(exc, folder=folder))
            return self.get_cached_messages(folder, limit)
        except OAuthTokenAcquisitionError as exc:
            self._sync_health.mark_warning(
                "Sign-in needs attention", code="auth", retryable=exc.retryable
            )
            self._set_sync_notice(classify_oauth_token_error(exc, folder=folder))
            return self.get_cached_messages(folder, limit)
        messages = []
        for entry in data.get("value") or []:
            msg = self._message_from_graph(entry, folder)
            if msg:
                messages.append(msg)
        messages.sort(
            key=lambda m: m.get("date") or datetime.now(timezone.utc), reverse=True
        )
        with self._sync_lock:
            self._folder_cache[folder] = messages[:_CACHE_MAX]
            # Cap distinct folders held in-memory so accounts with
            # dozens of folders (custom Outlook rules, archive
            # subfolders) don't let the cache grow without bound. The
            # on-disk sync_state is the durable store; evicting an
            # idle folder just means the next visit refetches.
            max_folders = 16
            if len(self._folder_cache) > max_folders:
                overflow = len(self._folder_cache) - max_folders
                for stale in list(self._folder_cache.keys())[:overflow]:
                    if stale == folder or stale == "inbox":
                        continue
                    self._folder_cache.pop(stale, None)
        self._persist_sync_state()
        self._sync_health.mark_ready("Ready")
        return list(messages[:limit])

    def fetch_thread_messages(self, thread_id):
        if not thread_id:
            return []
        quoted = str(thread_id).replace("'", "''")
        try:
            data = self._graph_request(
                "/me/messages",
                params={
                    "$filter": f"conversationId eq '{quoted}'",
                    "$select": _MESSAGE_SELECT,
                    "$orderby": "receivedDateTime asc",
                    "$top": "50",
                },
            ) or {}
        except Exception:
            return []
        messages = []
        for entry in data.get("value") or []:
            folder_id = str(entry.get("parentFolderId") or "").strip()
            msg = self._message_from_graph(entry, folder_id)
            if msg:
                messages.append(msg)
        return messages

    def fetch_body(self, uid, folder=None):
        uid = str(uid or "").strip()
        if not uid:
            raise BodyFetchError("Message identifier is required")
        try:
            data = self._graph_request(
                f"/me/messages/{urllib.parse.quote(uid)}",
                params={"$select": "body,subject,from,toRecipients,ccRecipients,attachments"},
            ) or {}
        except urllib.error.HTTPError as exc:
            raise BodyFetchError(f"Graph body fetch failed with {exc.code}") from exc
        body_obj = data.get("body") or {}
        content = str(body_obj.get("content") or "")
        content_type = str(body_obj.get("contentType") or "text").lower()
        attachments = []
        for att in data.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            attachments.append(
                {
                    "id": str(att.get("id") or ""),
                    "name": str(att.get("name") or ""),
                    "size": int(att.get("size") or 0),
                    "content_type": str(att.get("contentType") or ""),
                    "is_inline": bool(att.get("isInline")),
                }
            )
        html = content if content_type == "html" else ""
        text = content if content_type != "html" else ""
        return html, text, attachments

    def fetch_attachment_data(self, uid, attachment, folder=None):
        uid = str(uid or "").strip()
        attachment_id = str((attachment or {}).get("id") or "").strip()
        if not uid or not attachment_id:
            raise BodyFetchError("Attachment identifier is required")
        data = self._graph_request(
            f"/me/messages/{urllib.parse.quote(uid)}/attachments/{urllib.parse.quote(attachment_id)}"
        ) or {}
        content_b64 = str(data.get("contentBytes") or "")
        if not content_b64:
            return b""
        try:
            return base64.b64decode(content_b64)
        except Exception as exc:
            raise BodyFetchError("Attachment payload is corrupt") from exc

    def _patch_read_flag(self, uid, is_read):
        uid = str(uid or "").strip()
        if not uid:
            return False
        try:
            self._graph_request(
                f"/me/messages/{urllib.parse.quote(uid)}",
                method="PATCH",
                json_body={"isRead": bool(is_read)},
            )
        except Exception as exc:
            log_event(
                "ms-graph-patch-failed",
                level="warning",
                message=str(exc),
                context={"uid": uid, "is_read": bool(is_read)},
            )
            return False
        return True

    def mark_as_read(self, uid, folder=None):
        if self._patch_read_flag(uid, True):
            self.update_cached_message_read_state(folder or "inbox", uid, True)
            return True
        return False

    def mark_as_unread(self, uid, folder=None):
        if self._patch_read_flag(uid, False):
            self.update_cached_message_read_state(folder or "inbox", uid, False)
            return True
        return False

    def delete_message(self, uid, folder=None):
        uid = str(uid or "").strip()
        if not uid:
            return False
        try:
            self._graph_request(
                f"/me/messages/{urllib.parse.quote(uid)}/move",
                method="POST",
                json_body={"destinationId": "deleteditems"},
            )
        except Exception:
            return False
        self.remove_cached_message(folder or "inbox", uid)
        return True

    def get_unread_count(self, folder="inbox", force_primary=False):
        try:
            data = self._graph_request(
                f"/me/mailFolders/{urllib.parse.quote(folder)}",
                params={"$select": "unreadItemCount"},
            ) or {}
        except Exception:
            return self._folder_unread.get(folder, 0)
        count = int(data.get("unreadItemCount") or 0)
        self._folder_unread[folder] = count
        return count

    def get_unread_count_policy(self, folder="inbox", force_primary=False):
        return build_count_policy(
            "microsoft",
            "Graph mailFolders unreadItemCount",
            "Cached unread count",
            "Graph mailFolders unreadItemCount",
            route="primary",
            source="graph",
        )

    def fetch_contacts(self, query=""):
        return []

    def send_message(
        self,
        to,
        subject,
        body,
        html=None,
        cc=None,
        bcc=None,
        reply_to_msg=None,
        attachments=None,
    ):
        def _wrap(addresses):
            out = []
            for addr in addresses or []:
                email = ""
                name = ""
                if isinstance(addr, dict):
                    email = str(addr.get("email") or "").strip()
                    name = str(addr.get("name") or "").strip()
                elif isinstance(addr, str):
                    for parsed in _parse_addrs(addr):
                        email = str(parsed.get("email") or "").strip()
                        name = str(parsed.get("name") or email).strip()
                        break
                if not email:
                    continue
                entry = {"emailAddress": {"address": email}}
                if name and name != email:
                    entry["emailAddress"]["name"] = name
                out.append(entry)
            return out

        if isinstance(to, (str, dict)):
            to = [to]
        to_list = _wrap(to)
        cc_list = _wrap(cc)
        bcc_list = _wrap(bcc)
        content_type = "HTML" if html else "Text"
        content = html if html else (body or "")
        payload = {
            "message": {
                "subject": str(subject or ""),
                "body": {"contentType": content_type, "content": content},
                "toRecipients": to_list,
            },
            "saveToSentItems": True,
        }
        if cc_list:
            payload["message"]["ccRecipients"] = cc_list
        if bcc_list:
            payload["message"]["bccRecipients"] = bcc_list
        if attachments:
            att_payload = []
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                data_bytes = att.get("data") or b""
                if isinstance(data_bytes, str):
                    data_bytes = data_bytes.encode("utf-8")
                att_payload.append(
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": str(att.get("name") or "attachment"),
                        "contentBytes": base64.b64encode(data_bytes).decode("ascii"),
                        "contentType": str(
                            att.get("content_type") or "application/octet-stream"
                        ),
                    }
                )
            if att_payload:
                payload["message"]["attachments"] = att_payload
        self._graph_request("/me/sendMail", method="POST", json_body=payload)
        return True

    def check_background_updates(self, tracked_folders=None, reconcile_counts=False):
        folders = list(tracked_folders or []) or ["inbox"]
        changed = set()
        new_messages = []
        counts = {}
        for folder in folders:
            previous = list(self._folder_cache.get(folder, []))
            previous_uids = {msg.get("uid") for msg in previous}
            try:
                refreshed = self.fetch_messages(folder, limit=_RECENT_WINDOW)
            except Exception as exc:
                self._set_sync_notice(
                    classify_http_error(exc, folder=folder)
                    if isinstance(exc, urllib.error.HTTPError)
                    else build_sync_notice("error", "Sync issue")
                )
                continue
            if messages_changed(previous, refreshed):
                changed.add(folder)
            for msg in refreshed:
                if msg.get("uid") not in previous_uids and not msg.get("is_read"):
                    new_messages.append(msg)
            if reconcile_counts:
                counts[folder] = self.get_unread_count(folder)
        return {
            "account": self.identity,
            "provider": "microsoft",
            "changed_folders": changed,
            "new_messages": new_messages,
            "counts": counts,
            "notice": self.consume_sync_notice(),
        }

    def update_cached_message_read_state(self, folder, uid, is_read):
        changed = False
        with self._sync_lock:
            for msg in self._folder_cache.get(folder or "inbox", []):
                if msg.get("uid") == uid:
                    msg["is_read"] = bool(is_read)
                    changed = True
                    break
        if changed:
            self._persist_sync_state()
        return changed

    def remove_cached_message(self, folder, uid):
        removed = False
        with self._sync_lock:
            bucket = self._folder_cache.get(folder or "inbox")
            if bucket:
                before = len(bucket)
                self._folder_cache[folder or "inbox"] = [
                    msg for msg in bucket if msg.get("uid") != uid
                ]
                removed = len(self._folder_cache[folder or "inbox"]) != before
        if removed:
            self._persist_sync_state()
        return removed

    def get_sync_health(self):
        return self._sync_health.as_sidebar_status()

    def get_sync_policy(self):
        return build_sync_policy(
            "microsoft",
            "Microsoft Graph mail endpoints",
            "No transport fallback",
            "Graph list refresh with cached unread counts",
            notes=(
                "Microsoft Graph is the only transport. Cached state is shown if "
                "Graph is temporarily unavailable."
            ),
        )

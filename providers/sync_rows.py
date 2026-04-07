"""Shared helpers for persisted provider sync rows."""

from datetime import datetime, timezone

try:
    from .common import _aware_utc_datetime
except ImportError:
    from providers.common import _aware_utc_datetime


_COMMON_MESSAGE_KEYS = (
    'uid',
    'subject',
    'sender_name',
    'sender_email',
    'to_addrs',
    'cc_addrs',
    'is_read',
    'has_attachments',
    'snippet',
    'message_id',
)


def serialize_sync_messages(messages, *, limit, default_folder, default_thread_source, extra_keys=()):
    serial = []
    for msg in (messages or [])[:limit]:
        row = {
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
            'folder': msg.get('folder', default_folder),
            'thread_id': msg.get('thread_id', ''),
            'thread_source': msg.get('thread_source', default_thread_source),
            'message_id': msg.get('message_id', ''),
        }
        for key in extra_keys:
            row[key] = msg.get(key, '')
        serial.append(row)
    return serial


def deserialize_sync_messages(
    messages,
    *,
    limit,
    default_folder,
    provider_name,
    identity,
    backend_obj,
    default_thread_source,
    extra_keys=(),
):
    restored = []
    for msg in messages or []:
        try:
            date = _aware_utc_datetime(
                datetime.fromisoformat(msg.get('date')) if msg.get('date') else None
            )
        except Exception:
            date = datetime.now(timezone.utc)
        row = {
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
            'folder': msg.get('folder', default_folder),
            'backend': provider_name,
            'account': identity,
            'backend_obj': backend_obj,
            'thread_id': msg.get('thread_id', ''),
            'thread_source': msg.get('thread_source', default_thread_source),
            'message_id': msg.get('message_id', ''),
        }
        for key in extra_keys:
            row[key] = msg.get(key, '')
        restored.append(row)
    restored.sort(key=lambda item: _aware_utc_datetime(item.get('date')), reverse=True)
    return restored[:limit]

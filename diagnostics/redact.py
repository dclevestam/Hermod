"""Privacy-first redaction helpers for diagnostics."""

from __future__ import annotations

import hashlib
import re


_EMAIL_RE = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.IGNORECASE)
_BEARER_RE = re.compile(r'\bBearer\s+[A-Za-z0-9._~+/=-]+\b', re.IGNORECASE)
_TOKEN_PARAM_RE = re.compile(
    r'(?P<key>(?:access|refresh|id)?_?token|code)=([^&\s]+)',
    re.IGNORECASE,
)
_MESSAGE_ID_RE = re.compile(r'<[^<>\s@]+@[^<>\s]+>')
_MAX_STRING_LEN = 512

_SENSITIVE_KEY_TOKENS = (
    'token',
    'secret',
    'password',
    'cookie',
    'authorization',
    'uid',
    'message_id',
    'thread_id',
    'attachment_id',
    'history_id',
    'delta_link',
    'identity',
    'account',
)


def _hash_text(value):
    text = str(value or '')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:10]


def redact_text(value):
    text = str(value or '')
    if not text:
        return ''
    text = _BEARER_RE.sub('Bearer <redacted>', text)
    text = _TOKEN_PARAM_RE.sub(lambda match: f"{match.group('key')}=<redacted>", text)
    text = _MESSAGE_ID_RE.sub('<message-id:redacted>', text)
    text = _EMAIL_RE.sub(lambda match: f'<email:{_hash_text(match.group(0))}>', text)
    if len(text) > _MAX_STRING_LEN:
        text = text[:_MAX_STRING_LEN] + '…'
    return text


def redact_value(value, key=None):
    key_text = str(key or '').lower()
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item, key=key) for item in value]
    if value is None:
        return None
    if any(token in key_text for token in _SENSITIVE_KEY_TOKENS):
        return f'<redacted:{_hash_text(value)}>'
    if isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)

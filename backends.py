"""Compatibility façade for provider construction and shared backend helpers."""

try:
    from .accounts.registry import ProviderRegistry
    from .accounts.sources.goa import get_goa_account_descriptors
    from .providers.common import (
        _aware_utc_datetime,
        ensure_network_ready,
        is_transient_network_error,
        network_ready,
    )
    from .providers.gmail import GmailBackend
    from .providers.microsoft_graph import (
        MicrosoftBackend,
        _GRAPH_INLINE_ATTACHMENT_MAX_BYTES,
        _GRAPH_REQUEST_TIMEOUT_SECS,
        _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT,
        _GRAPH_SYNC_RETENTION_DAYS,
        _GRAPH_UPLOAD_CHUNK_BYTES,
        _SYNC_RECENT_MESSAGES_LIMIT,
    )
except ImportError:
    from accounts.registry import ProviderRegistry
    from accounts.sources.goa import get_goa_account_descriptors
    from providers.common import (
        _aware_utc_datetime,
        ensure_network_ready,
        is_transient_network_error,
        network_ready,
    )
    from providers.gmail import GmailBackend
    from providers.microsoft_graph import (
        MicrosoftBackend,
        _GRAPH_INLINE_ATTACHMENT_MAX_BYTES,
        _GRAPH_REQUEST_TIMEOUT_SECS,
        _GRAPH_SYNC_CUSTOM_FOLDER_LIMIT,
        _GRAPH_SYNC_RETENTION_DAYS,
        _GRAPH_UPLOAD_CHUNK_BYTES,
        _SYNC_RECENT_MESSAGES_LIMIT,
    )


def get_backends():
    return _PROVIDER_REGISTRY.create_backends(get_goa_account_descriptors())


_PROVIDER_REGISTRY = ProviderRegistry()
_PROVIDER_REGISTRY.register('gmail', GmailBackend)
_PROVIDER_REGISTRY.register('microsoft-graph', MicrosoftBackend)


__all__ = [
    'GmailBackend',
    'MicrosoftBackend',
    'get_backends',
    'network_ready',
    'ensure_network_ready',
    'is_transient_network_error',
    '_aware_utc_datetime',
    '_GRAPH_REQUEST_TIMEOUT_SECS',
    '_GRAPH_INLINE_ATTACHMENT_MAX_BYTES',
    '_GRAPH_UPLOAD_CHUNK_BYTES',
    '_SYNC_RECENT_MESSAGES_LIMIT',
    '_GRAPH_SYNC_RETENTION_DAYS',
    '_GRAPH_SYNC_CUSTOM_FOLDER_LIMIT',
]

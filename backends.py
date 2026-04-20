"""Compatibility façade for provider construction and shared backend helpers."""

try:
    from .accounts.registry import ProviderRegistry
    from .accounts.account_prefs import prune_account_preferences
    from .accounts.native_store import get_native_account_descriptors, list_native_account_records
    from .diagnostics.logger import log_event
    from .providers.common import (
        _aware_utc_datetime,
        ensure_network_ready,
        is_transient_network_error,
        network_ready,
    )
    from .providers.gmail import GmailBackend
    from .providers.imap_smtp import IMAPSMTPBackend
    from .providers.microsoft import MicrosoftGraphBackend
    from .sync_state import prune_account_states
except ImportError:
    from accounts.registry import ProviderRegistry
    from accounts.account_prefs import prune_account_preferences
    from accounts.native_store import get_native_account_descriptors, list_native_account_records
    from diagnostics.logger import log_event
    from providers.common import (
        _aware_utc_datetime,
        ensure_network_ready,
        is_transient_network_error,
        network_ready,
    )
    from providers.gmail import GmailBackend
    from providers.imap_smtp import IMAPSMTPBackend
    from providers.microsoft import MicrosoftGraphBackend
    from sync_state import prune_account_states


_SYNC_STATE_PROVIDER_KEYS = {
    'gmail': 'gmail',
    'microsoft-graph': 'microsoft',
    'imap-smtp': 'imap',
}
_ACTIVE_PROVIDER_KINDS = frozenset({'gmail', 'imap-smtp', 'microsoft-graph'})


def reconcile_account_inventory(descriptors=None):
    inventory = list(list_native_account_records())
    descriptors = list(descriptors or get_native_account_descriptors())
    descriptors = [
        descriptor for descriptor in descriptors
        if str(getattr(descriptor, 'provider_kind', '') or '').strip().lower() in _ACTIVE_PROVIDER_KINDS
    ]
    descriptors_by_provider = {}
    active_pref_keys = []
    for descriptor in descriptors:
        provider_kind = str(getattr(descriptor, 'provider_kind', '') or '').strip().lower()
        state_provider = _SYNC_STATE_PROVIDER_KEYS.get(provider_kind)
        if not state_provider:
            continue
        descriptors_by_provider.setdefault(state_provider, set()).add(str(descriptor.identity or '').strip())
    for record in inventory:
        provider_kind = str(getattr(record, 'provider_kind', '') or '').strip().lower()
        identity = str(getattr(record, 'identity', '') or '').strip()
        if provider_kind and identity:
            active_pref_keys.append(('native', provider_kind, identity))

    removed_by_provider = {}
    for state_provider in _SYNC_STATE_PROVIDER_KEYS.values():
        active_accounts = descriptors_by_provider.get(state_provider, set())
        removed = prune_account_states(state_provider, active_accounts)
        if removed:
            removed_by_provider[state_provider] = removed

    removed_prefs = prune_account_preferences(active_pref_keys)

    inactive_accounts = [
        {
            'provider_kind': record.provider_kind,
            'identity': record.identity,
            'enabled': bool(record.enabled),
        }
        for record in inventory
        if str(getattr(record, 'provider_kind', '') or '').strip().lower() not in _ACTIVE_PROVIDER_KINDS
    ]
    disabled_accounts = [
        {
            'provider_kind': record.provider_kind,
            'identity': record.identity,
        }
        for record in inventory
        if not bool(getattr(record, 'enabled', True))
    ]

    if removed_by_provider or inactive_accounts or disabled_accounts or removed_prefs:
        log_event(
            'accounts-reconciled',
            level='info',
            message='Accounts reconciled at startup',
            context={
                'providers': {
                    provider: len(descriptors_by_provider.get(provider, set()))
                    for provider in _SYNC_STATE_PROVIDER_KEYS.values()
                },
                'inventory': {
                    'discovered': len(inventory),
                    'active': len(descriptors),
                    'disabled': len(disabled_accounts),
                    'inactive': len(inactive_accounts),
                },
                'disabled_accounts': disabled_accounts,
                'inactive_accounts': inactive_accounts,
                'removed': removed_by_provider,
                'removed_prefs': [
                    {
                        'source': record.source,
                        'provider_kind': record.provider_kind,
                        'identity': record.identity,
                    }
                    for record in removed_prefs
                ],
            },
            persist=True,
        )

    return descriptors, removed_by_provider


def get_backends():
    descriptors, _removed = reconcile_account_inventory()
    return _PROVIDER_REGISTRY.create_backends(descriptors)


def describe_sync_policies(backends=None):
    policies = []
    for backend in list(backends or get_backends()):
        getter = getattr(backend, 'get_sync_policy', None)
        if not callable(getter):
            continue
        try:
            policy = getter()
        except Exception:
            continue
        count_policy = None
        count_getter = getattr(backend, 'get_unread_count_policy', None)
        if callable(count_getter):
            try:
                count_policy = count_getter()
            except Exception:
                count_policy = None
        policies.append({
            'account': getattr(backend, 'identity', ''),
            'provider': getattr(backend, 'provider', ''),
            'policy': policy,
            'count_policy': count_policy,
        })
    return policies


_PROVIDER_REGISTRY = ProviderRegistry()
_PROVIDER_REGISTRY.register('gmail', GmailBackend)
_PROVIDER_REGISTRY.register('imap-smtp', IMAPSMTPBackend)
_PROVIDER_REGISTRY.register('microsoft-graph', MicrosoftGraphBackend)


__all__ = [
    'GmailBackend',
    'IMAPSMTPBackend',
    'MicrosoftGraphBackend',
    'get_backends',
    'network_ready',
    'ensure_network_ready',
    'is_transient_network_error',
    '_aware_utc_datetime',
    'reconcile_account_inventory',
    'describe_sync_policies',
]

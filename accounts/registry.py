"""Provider registry for constructing backends from normalized descriptors."""

from collections.abc import Iterable
from typing import Callable

try:
    from .descriptors import AccountDescriptor
    from ..providers.base import MailProvider
except ImportError:
    from accounts.descriptors import AccountDescriptor
    from providers.base import MailProvider


ProviderFactory = Callable[[AccountDescriptor], MailProvider]


class LazyMailProvider:
    def __init__(self, descriptor: AccountDescriptor, factory: ProviderFactory):
        self._descriptor = descriptor
        self._factory = factory
        self._backend = None
        self.account_descriptor = descriptor
        self.identity = descriptor.identity
        self.presentation_name = descriptor.presentation_name or descriptor.identity
        self.accent_color = str((descriptor.metadata or {}).get('accent_color') or '').strip()
        self.provider = self._provider_name_for_kind(descriptor.provider_kind)
        self.FOLDERS = getattr(factory, 'FOLDERS', getattr(descriptor, 'FOLDERS', []))

    def _provider_name_for_kind(self, provider_kind):
        kind = str(provider_kind or '').strip().lower()
        if kind == 'microsoft-graph':
            return 'microsoft'
        if kind == 'imap-smtp':
            return 'imap'
        return kind or 'unknown'

    @property
    def is_loaded(self):
        return self._backend is not None

    def _ensure_backend(self):
        if self._backend is None:
            self._backend = self._factory(self._descriptor)
            for attr in ('identity', 'provider', 'FOLDERS'):
                if hasattr(self._backend, attr):
                    setattr(self, attr, getattr(self._backend, attr))
            for attr in ('presentation_name', 'accent_color', 'account_descriptor'):
                if hasattr(self._backend, attr):
                    setattr(self, attr, getattr(self._backend, attr))
        return self._backend

    def __getattr__(self, name):
        backend = self._ensure_backend()
        return getattr(backend, name)


class ProviderRegistry:
    def __init__(self):
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, provider_kind: str, factory: ProviderFactory):
        key = str(provider_kind or '').strip().lower()
        if not key:
            raise ValueError('provider_kind is required')
        if key in self._factories:
            raise ValueError(f'Provider already registered: {provider_kind}')
        self._factories[key] = factory

    def create_backend(self, descriptor: AccountDescriptor) -> MailProvider:
        key = str(descriptor.provider_kind or '').strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            raise LookupError(f'No provider registered for {descriptor.provider_kind}')
        return factory(descriptor)

    def create_backends(self, descriptors: Iterable[AccountDescriptor]) -> list[MailProvider]:
        backends = []
        for descriptor in descriptors:
            key = str(descriptor.provider_kind or '').strip().lower()
            factory = self._factories.get(key)
            if factory is None:
                raise LookupError(f'No provider registered for {descriptor.provider_kind}')
            backends.append(LazyMailProvider(descriptor, factory))
        return backends

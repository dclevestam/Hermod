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
        return [self.create_backend(descriptor) for descriptor in descriptors]

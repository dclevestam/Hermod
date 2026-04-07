"""Account sources, auth providers, and normalized account descriptors."""

from .descriptors import AccountDescriptor
from .registry import ProviderRegistry

__all__ = ['AccountDescriptor', 'ProviderRegistry']

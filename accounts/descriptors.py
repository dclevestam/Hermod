"""Normalized account descriptors used to construct mail providers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AccountDescriptor:
    source: str
    provider_kind: str
    identity: str
    presentation_name: str = ''
    auth_kind: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)
    source_obj: Any | None = None

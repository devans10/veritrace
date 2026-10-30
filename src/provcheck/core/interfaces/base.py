"""Types shared by every provider interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    ok: bool
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """The provider was reached but returned an error or unusable data."""


class ProviderUnavailable(ProviderError):
    """The provider could not be reached (network, timeout, 5xx, rate limit)."""


class NotConfigured(ProviderError):
    """The provider is disabled or missing credentials."""

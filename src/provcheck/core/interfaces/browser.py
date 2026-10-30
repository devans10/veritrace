from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth


@dataclass(frozen=True)
class CaptureOptions:
    timeout_s: float = 45.0
    full_page: bool = True
    max_screenshot_height: int = 12000


@dataclass
class CaptureResult:
    requested_url: str
    final_url: str
    status_code: int | None
    headers: dict[str, str]
    redirect_chain: list[str]
    title: str | None
    html: bytes
    text: str
    screenshot_png: bytes
    claimed_published_at: str | None
    fetched_at: datetime
    blocked_requests: list[str] = field(default_factory=list)
    truncated: bool = False


@runtime_checkable
class BrowserCapture(Protocol):
    """Renders an untrusted page in the sandboxed browser and returns a snapshot."""

    name: str

    async def capture(self, url: str, opts: CaptureOptions | None = None) -> CaptureResult: ...

    async def health(self) -> ProviderHealth: ...

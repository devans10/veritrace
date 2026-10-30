from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth

# ----- EXIF ---------------------------------------------------------------


@dataclass(frozen=True)
class ExifReading:
    mime: str
    width: int | None
    height: int | None
    camera_make: str | None
    camera_model: str | None
    datetime_original: datetime | None
    datetime_digitized: datetime | None
    software: str | None
    gps_present: bool
    raw: dict[str, Any] = field(default_factory=dict)
    has_exif: bool = False


@runtime_checkable
class ExifReader(Protocol):
    name: str

    async def read(self, data: bytes, *, mime: str | None = None) -> ExifReading: ...

    async def health(self) -> ProviderHealth: ...


# ----- C2PA ---------------------------------------------------------------

C2PAState = Literal["valid", "invalid", "missing", "unverified"]


@dataclass(frozen=True)
class C2PAReading:
    state: C2PAState
    manifests: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@runtime_checkable
class C2PAVerifier(Protocol):
    name: str

    async def verify(self, data: bytes, *, mime: str | None = None) -> C2PAReading: ...

    async def health(self) -> ProviderHealth: ...


# ----- ELA ----------------------------------------------------------------


@dataclass(frozen=True)
class ELAReading:
    """Error-Level Analysis output.

    Informational only in v0.1 — the scorer weights `media.ela` at 0 because
    ELA produces false positives on compressed JPEGs. The output is stored as
    an artifact (an image diff) and shown in the report.
    """

    diff_png: bytes
    mean_abs_difference: float
    max_abs_difference: int


@runtime_checkable
class ELAProvider(Protocol):
    name: str

    async def analyze(self, jpeg: bytes) -> ELAReading: ...

    async def health(self) -> ProviderHealth: ...


# ----- AI-generation detector --------------------------------------------


@dataclass(frozen=True)
class DetectorResult:
    label: str  # "real" | "synthetic" | "uncertain"
    score: float
    model: str


@runtime_checkable
class Detector(Protocol):
    """AI-generation detector. None is enabled in v0.1 — detectors are
    unreliable, and the plan treats their outputs as weak signals only."""

    name: str

    async def classify(self, data: bytes, *, mime: str | None = None) -> DetectorResult: ...

    async def health(self) -> ProviderHealth: ...

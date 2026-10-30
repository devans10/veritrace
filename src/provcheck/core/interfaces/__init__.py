"""Every external dependency is reached only through these interfaces."""

from provcheck.core.interfaces.archive import (
    ArchiveCapture,
    ArchiveJob,
    ArchiveJobStatus,
    ArchiveProvider,
)
from provcheck.core.interfaces.base import (
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.interfaces.browser import BrowserCapture, CaptureOptions, CaptureResult
from provcheck.core.interfaces.domaininfo import DomainInfo, DomainInfoProvider
from provcheck.core.interfaces.embedding import EmbeddingProvider, EmbedInputType
from provcheck.core.interfaces.factcheck import ClaimReviewHit, FactCheckProvider
from provcheck.core.interfaces.llm import LLMClient, LLMResult, LLMUsage
from provcheck.core.interfaces.media import (
    C2PAReading,
    C2PAState,
    C2PAVerifier,
    Detector,
    DetectorResult,
    ELAProvider,
    ELAReading,
    ExifReader,
    ExifReading,
)
from provcheck.core.interfaces.objectstore import ObjectStore, StoredObject
from provcheck.core.interfaces.queue import JobRef, JobStatus, Queue
from provcheck.core.interfaces.reverse_image import ImageMatch, ReverseImageProvider
from provcheck.core.interfaces.search import SearchProvider, SearchResult, TimeRange

__all__ = [
    "ArchiveCapture",
    "ArchiveJob",
    "ArchiveJobStatus",
    "ArchiveProvider",
    "BrowserCapture",
    "C2PAReading",
    "C2PAState",
    "C2PAVerifier",
    "CaptureOptions",
    "CaptureResult",
    "ClaimReviewHit",
    "Detector",
    "DetectorResult",
    "DomainInfo",
    "DomainInfoProvider",
    "ELAProvider",
    "ELAReading",
    "EmbedInputType",
    "EmbeddingProvider",
    "ExifReader",
    "ExifReading",
    "FactCheckProvider",
    "ImageMatch",
    "JobRef",
    "JobStatus",
    "LLMClient",
    "LLMResult",
    "LLMUsage",
    "NotConfigured",
    "ObjectStore",
    "ProviderError",
    "ProviderHealth",
    "ProviderUnavailable",
    "Queue",
    "ReverseImageProvider",
    "SearchProvider",
    "SearchResult",
    "StoredObject",
    "TimeRange",
]

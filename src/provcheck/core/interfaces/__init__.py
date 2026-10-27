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
from provcheck.core.interfaces.embedding import EmbeddingProvider, EmbedInputType
from provcheck.core.interfaces.factcheck import ClaimReviewHit, FactCheckProvider
from provcheck.core.interfaces.llm import LLMClient, LLMResult, LLMUsage
from provcheck.core.interfaces.objectstore import ObjectStore, StoredObject
from provcheck.core.interfaces.queue import JobRef, JobStatus, Queue
from provcheck.core.interfaces.search import SearchProvider, SearchResult, TimeRange

__all__ = [
    "ArchiveCapture",
    "ArchiveJob",
    "ArchiveJobStatus",
    "ArchiveProvider",
    "BrowserCapture",
    "CaptureOptions",
    "CaptureResult",
    "ClaimReviewHit",
    "EmbedInputType",
    "EmbeddingProvider",
    "FactCheckProvider",
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
    "SearchProvider",
    "SearchResult",
    "StoredObject",
    "TimeRange",
]

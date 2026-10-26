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
from provcheck.core.interfaces.objectstore import ObjectStore, StoredObject
from provcheck.core.interfaces.queue import JobRef, JobStatus, Queue

__all__ = [
    "ArchiveCapture",
    "ArchiveJob",
    "ArchiveJobStatus",
    "ArchiveProvider",
    "BrowserCapture",
    "CaptureOptions",
    "CaptureResult",
    "JobRef",
    "JobStatus",
    "NotConfigured",
    "ObjectStore",
    "ProviderError",
    "ProviderHealth",
    "ProviderUnavailable",
    "Queue",
    "StoredObject",
]

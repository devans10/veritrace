"""API-facing schemas for cases and captures."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, min_length=1, max_length=2048)
    text: str | None = Field(
        default=None,
        min_length=1,
        max_length=50_000,
        description="Submit raw text instead of a URL. Skips capture; goes straight to extraction.",
    )
    archive: bool = Field(
        default=True,
        description="Submit the URL to the Internet Archive. Saving makes the URL public.",
    )
    refresh: bool = Field(default=False, description="Ignore a cached recent result.")

    @model_validator(mode="after")
    def _one_input(self) -> CaseCreate:
        if (self.url is None) == (self.text is None):
            raise ValueError("exactly one of `url` or `text` must be supplied")
        return self


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    sha256: str
    size: int
    mime: str
    href: str


class CaptureOut(BaseModel):
    requested_url: str
    final_url: str
    redirect_chain: list[Any]
    http_status: int | None
    fetched_at: datetime
    title: str | None
    claimed_published_at: str | None
    text_sha256: str | None
    truncated: bool
    blocked_requests: int
    archive: dict[str, Any]
    artifacts: list[ArtifactOut]


class CaseOut(BaseModel):
    id: uuid.UUID
    status: str
    input_type: str
    input_url: str | None
    normalized_url: str | None
    created_at: datetime
    updated_at: datetime
    error: str | None
    cached: bool = False
    captures: list[CaptureOut] = []
    notice: str = "Provenance Check produces a credibility assessment with evidence, not a verdict."


class AuditEntryOut(BaseModel):
    id: int
    ts: datetime
    actor: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class AuditOut(BaseModel):
    case_id: uuid.UUID
    entries_valid: bool
    chain_valid: bool
    chain_detail: str
    entries: list[AuditEntryOut]

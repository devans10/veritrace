"""SQLAlchemy models. Enumerations are VARCHAR + CHECK (portable, easy to migrate)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from provcheck.config import get_settings

CASE_STATUSES = (
    "queued",
    "capturing",
    "extracting",
    "investigating",
    "scoring",
    "complete",
    "failed",
)
INPUT_TYPES = ("url", "text", "upload")
DEPTHS = ("quick", "full")
ARTIFACT_KINDS = ("html", "screenshot", "text", "media", "ela", "report_json")
ARCHIVE_STATUSES = ("saved", "pending", "failed", "disabled", "not_configured", "opted_out")
RUN_STATUSES = ("queued", "running", "done", "failed", "skipped")
CLAIM_TYPES = ("event", "statistic", "quote", "attribution", "other")
DIRECTIONS = ("supports", "undermines", "neutral")
CATEGORIES = ("fabricated", "misleading_context", "satire", "corroborated")
UNVERIFIED_REASONS = (
    "not_configured",
    "provider_unavailable",
    "no_results",
    "blocked_by_policy",
    "timeout",
    "unsupported_platform",
    "error",
)
BANDS = ("low_risk", "uncertain", "high_risk")
CONFIDENCE_LEVELS = ("low", "medium", "high")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(_in("status", CASE_STATUSES), name="ck_cases_status"),
        CheckConstraint(_in("input_type", INPUT_TYPES), name="ck_cases_input_type"),
        CheckConstraint(_in("depth", DEPTHS), name="ck_cases_depth"),
        Index("ix_cases_cache_key_created", "cache_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    input_type: Mapped[str] = mapped_column(String(16))
    input_url: Mapped[str | None] = mapped_column(Text)
    normalized_url: Mapped[str | None] = mapped_column(Text)
    input_text: Mapped[str | None] = mapped_column(Text)
    upload_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    depth: Mapped[str] = mapped_column(String(8), default="full")
    archive_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str | None] = mapped_column(String(64))
    # Snapshot of state.INVESTIGATORS at the moment fan_out ran, so a release
    # that grows the investigator list doesn't strand mid-flight cases.
    expected_investigators: Mapped[list[Any] | None] = mapped_column(nullable=True)

    captures: Mapped[list[Capture]] = relationship(
        back_populates="case", order_by="Capture.fetched_at"
    )
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="case")
    runs: Mapped[list[InvestigatorRun]] = relationship(back_populates="case")


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (CheckConstraint(_in("kind", ARTIFACT_KINDS), name="ck_artifacts_kind"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(128))
    phash: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _now()

    case: Mapped[Case] = relationship(back_populates="artifacts")


class Capture(Base):
    __tablename__ = "captures"
    __table_args__ = (
        CheckConstraint(_in("archive_status", ARCHIVE_STATUSES), name="ck_captures_archive_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    requested_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    redirect_chain: Mapped[list[Any]] = mapped_column(default=list)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    claimed_published_at: Mapped[str | None] = mapped_column(Text)
    html_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    screenshot_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    text_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    blocked_requests: Mapped[list[Any]] = mapped_column(default=list)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_status: Mapped[str] = mapped_column(String(16), default="disabled")
    archive_url: Mapped[str | None] = mapped_column(Text)
    archive_job_id: Mapped[str | None] = mapped_column(Text)
    archive_error: Mapped[str | None] = mapped_column(Text)
    archive_attempts: Mapped[int] = mapped_column(Integer, default=0)

    case: Mapped[Case] = relationship(back_populates="captures")


class InvestigatorRun(Base):
    __tablename__ = "investigator_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
        Index("uq_runs_case_investigator", "case_id", "investigator", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    investigator: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    job_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    providers: Mapped[dict[str, Any]] = mapped_column(default=dict)

    case: Mapped[Case] = relationship(back_populates="runs")


EMBED_DIM = get_settings().embed_dim


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(_in("claim_type", CLAIM_TYPES), name="ck_claims_type"),
        Index("ix_claims_case_ordinal", "case_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source_span: Mapped[str] = mapped_column(Text)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    claim_type: Mapped[str] = mapped_column(String(16), default="other")
    checkworthiness: Mapped[float | None] = mapped_column()
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    extractor_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _now()


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint(_in("direction", DIRECTIONS), name="ck_evidence_direction"),
        CheckConstraint("strength BETWEEN 0 AND 1", name="ck_evidence_strength"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_evidence_confidence"),
        Index("ix_evidence_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigator_runs.id", ondelete="CASCADE")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))
    investigator: Mapped[str] = mapped_column(String(32))
    signal: Mapped[str] = mapped_column(String(64))
    finding: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = _now()
    direction: Mapped[str] = mapped_column(String(16))
    strength: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    category_hint: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    source_content_sha256: Mapped[str | None] = mapped_column(String(64))


class UnverifiedItem(Base):
    __tablename__ = "unverified_items"
    __table_args__ = (
        CheckConstraint(_in("reason", UNVERIFIED_REASONS), name="ck_unverified_reason"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigator_runs.id", ondelete="CASCADE")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))
    investigator: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        CheckConstraint(_in("band", BANDS), name="ck_scores_band"),
        CheckConstraint(_in("confidence_level", CONFIDENCE_LEVELS), name="ck_scores_confidence"),
        Index("uq_scores_case_id", "case_id", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    scorer_version: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    band: Mapped[str] = mapped_column(String(16))
    confidence_level: Mapped[str] = mapped_column(String(8))
    raw_logit: Mapped[float] = mapped_column()
    coverage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    category_breakdown: Mapped[list[Any]] = mapped_column(default=list)
    contributions: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = _now()


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scores.id", ondelete="SET NULL"))
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    """Append-only, hash-chained. UPDATE/DELETE/TRUNCATE are rejected by a DB trigger."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)

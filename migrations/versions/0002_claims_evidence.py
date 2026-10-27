"""claims, evidence_items, unverified_items (+ pgvector)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

import re

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from provcheck.config import get_settings
from provcheck.core.db.models import CLAIM_TYPES, DIRECTIONS, UNVERIFIED_REASONS

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    settings = get_settings()
    dim = settings.embed_dim

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "claims",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source_span", sa.Text, nullable=False),
        sa.Column("span_start", sa.Integer),
        sa.Column("span_end", sa.Integer),
        sa.Column("claim_type", sa.String(16), nullable=False, server_default="other"),
        sa.Column("checkworthiness", sa.Float),
        sa.Column("embedding", Vector(dim)),
        sa.Column("extractor_model", sa.String(128)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("claim_type", CLAIM_TYPES), name="ck_claims_type"),
    )
    op.create_index("ix_claims_case_id", "claims", ["case_id"])
    op.create_index("ix_claims_case_ordinal", "claims", ["case_id", "ordinal"])

    op.create_table(
        "evidence_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "run_id", UUID, sa.ForeignKey("investigator_runs.id", ondelete="CASCADE")
        ),
        sa.Column("claim_id", UUID, sa.ForeignKey("claims.id", ondelete="SET NULL")),
        sa.Column("investigator", sa.String(32), nullable=False),
        sa.Column("signal", sa.String(64), nullable=False),
        sa.Column("finding", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("source_title", sa.Text),
        sa.Column("source_published_at", TS),
        sa.Column("retrieved_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("strength", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("category_hint", sa.String(32)),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("source_content_sha256", sa.String(64)),
        sa.CheckConstraint(_in("direction", DIRECTIONS), name="ck_evidence_direction"),
    )
    op.create_index("ix_evidence_items_case_id", "evidence_items", ["case_id"])
    op.create_index("ix_evidence_case", "evidence_items", ["case_id"])

    op.create_table(
        "unverified_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "run_id", UUID, sa.ForeignKey("investigator_runs.id", ondelete="CASCADE")
        ),
        sa.Column("claim_id", UUID, sa.ForeignKey("claims.id", ondelete="SET NULL")),
        sa.Column("investigator", sa.String(32), nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("reason", UNVERIFIED_REASONS), name="ck_unverified_reason"),
    )
    op.create_index("ix_unverified_items_case_id", "unverified_items", ["case_id"])

    app_role = settings.db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app_role):
        raise ValueError(f"unsafe DB_USER role name: {app_role!r}")
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON claims, evidence_items,
              unverified_items TO "{app_role}";
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for t in ("unverified_items", "evidence_items", "claims"):
        op.drop_table(t)
    # pgvector extension left in place; harmless if nothing else uses it.

"""scores, reports

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from provcheck.config import get_settings
from provcheck.core.db.models import BANDS, CONFIDENCE_LEVELS

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scorer_version", sa.String(64), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("confidence_level", sa.String(8), nullable=False),
        sa.Column("raw_logit", sa.Float, nullable=False),
        sa.Column("coverage", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("category_breakdown", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("contributions", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("band", BANDS), name="ck_scores_band"),
        sa.CheckConstraint(
            _in("confidence_level", CONFIDENCE_LEVELS), name="ck_scores_confidence"
        ),
    )
    op.create_index("ix_scores_case_id", "scores", ["case_id"])

    op.create_table(
        "reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score_id", UUID, sa.ForeignKey("scores.id", ondelete="SET NULL")),
        sa.Column("artifact_id", UUID, sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reports_case_id", "reports", ["case_id"])
    op.create_index("ix_reports_sha256", "reports", ["sha256"])

    app_role = get_settings().db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app_role):
        raise ValueError(f"unsafe DB_USER role name: {app_role!r}")
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON scores, reports TO "{app_role}";
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for t in ("reports", "scores"):
        op.drop_table(t)

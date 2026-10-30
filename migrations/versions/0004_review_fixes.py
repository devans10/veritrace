"""review fixes: scores UNIQUE, evidence CHECK, cases.expected_investigators

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29

Three correctness fixes from the v0.1 PR review:

* `UNIQUE (case_id)` on `scores` so a mid-stage crash + retry can't create a
  second score row for the same case; the retry path in ``run_score`` detects
  the existing row and resumes.
* `CHECK (strength BETWEEN 0 AND 1)` and `CHECK (confidence BETWEEN 0 AND 1)`
  on `evidence_items`. Pydantic enforces this on extraction; the DB-level
  constraint protects against a buggy future investigator writing a raw row.
* `cases.expected_investigators JSONB` records the investigator list the
  fan-out used, so a release that grows ``state.INVESTIGATORS`` doesn't
  strand mid-flight cases waiting for an investigator that never ran.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_scores_case_id", "scores", ["case_id"], unique=True)

    op.create_check_constraint(
        "ck_evidence_strength",
        "evidence_items",
        "strength BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_evidence_confidence",
        "evidence_items",
        "confidence BETWEEN 0 AND 1",
    )

    op.add_column(
        "cases",
        sa.Column("expected_investigators", pg.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cases", "expected_investigators")
    op.drop_constraint("ck_evidence_confidence", "evidence_items", type_="check")
    op.drop_constraint("ck_evidence_strength", "evidence_items", type_="check")
    op.drop_index("uq_scores_case_id", table_name="scores")

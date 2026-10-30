"""cases, artifacts, captures, investigator_runs, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from provcheck.config import get_settings
from provcheck.core.db.models import (
    ARCHIVE_STATUSES,
    ARTIFACT_KINDS,
    CASE_STATUSES,
    DEPTHS,
    INPUT_TYPES,
    RUN_STATUSES,
)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("input_type", sa.String(16), nullable=False),
        sa.Column("input_url", sa.Text),
        sa.Column("normalized_url", sa.Text),
        sa.Column("input_text", sa.Text),
        sa.Column("upload_artifact_id", UUID),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("depth", sa.String(8), nullable=False, server_default="full"),
        sa.Column("archive_opt_out", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text),
        sa.Column("cache_key", sa.String(64)),
        sa.CheckConstraint(_in("status", CASE_STATUSES), name="ck_cases_status"),
        sa.CheckConstraint(_in("input_type", INPUT_TYPES), name="ck_cases_input_type"),
        sa.CheckConstraint(_in("depth", DEPTHS), name="ck_cases_depth"),
    )
    op.create_index("ix_cases_cache_key_created", "cases", ["cache_key", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("phash", sa.String(32)),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("kind", ARTIFACT_KINDS), name="ck_artifacts_kind"),
    )
    op.create_index("ix_artifacts_case_id", "artifacts", ["case_id"])
    op.create_index("ix_artifacts_sha256", "artifacts", ["sha256"])

    op.create_table(
        "captures",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_url", sa.Text, nullable=False),
        sa.Column("final_url", sa.Text, nullable=False),
        sa.Column("redirect_chain", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("http_status", sa.Integer),
        sa.Column("response_headers", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("fetched_at", TS, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("claimed_published_at", sa.Text),
        sa.Column("html_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("screenshot_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("text_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("text_sha256", sa.String(64)),
        sa.Column("blocked_requests", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("truncated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("archive_status", sa.String(16), nullable=False, server_default="disabled"),
        sa.Column("archive_url", sa.Text),
        sa.Column("archive_job_id", sa.Text),
        sa.Column("archive_error", sa.Text),
        sa.Column("archive_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint(
            _in("archive_status", ARCHIVE_STATUSES), name="ck_captures_archive_status"
        ),
    )
    op.create_index("ix_captures_case_id", "captures", ["case_id"])

    op.create_table(
        "investigator_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("investigator", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("job_id", sa.Text),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("error", sa.Text),
        sa.Column("providers", pg.JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
    )
    op.create_index("ix_investigator_runs_case_id", "investigator_runs", ["case_id"])
    op.create_index(
        "uq_runs_case_investigator", "investigator_runs", ["case_id", "investigator"], unique=True
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", TS, nullable=False),
        sa.Column("case_id", UUID),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_audit_log_case_id", "audit_log", ["case_id"])

    op.execute(
        """
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP;
        END $$;
        CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
          FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
          FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
        """
    )

    app_role = get_settings().db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app_role):
        raise ValueError(f"unsafe DB_USER role name: {app_role!r}")
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON cases, artifacts, captures,
              investigator_runs TO "{app_role}";
            GRANT SELECT, INSERT ON audit_log TO "{app_role}";
            GRANT USAGE ON SEQUENCE audit_log_id_seq TO "{app_role}";
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    for t in ("audit_log", "investigator_runs", "captures", "artifacts", "cases"):
        op.drop_table(t)

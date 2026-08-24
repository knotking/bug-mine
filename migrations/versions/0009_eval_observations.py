"""Eval observations — the ingestion surface for probe results.

Evals were the fourth origin in the design and the only one with no way in: the runner existed
but ran in-process, so a probe suite running anywhere else had nothing to report to. This adds
the table that holds submitted evidence.

Evidence, not records. Nothing here is visible in the catalog; promotion to a bug record is a
separate decision made against pooled evidence, because one run of forty saying "four failed"
does not establish a defect.

RLS follows bug_record and source: public evidence is visible to everyone, tenant evidence only
to its owner. That matters more here than elsewhere — a tenant's eval results describe their own
prompts and their own traffic patterns.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CTX = "NULLIF(current_setting('bugmine.tenant_id', true), '')"
SCOPE = f"privacy_scope IN ('public', 'subscriber') OR tenant_id = {CTX}::uuid"


def upgrade() -> None:
    op.create_table(
        "eval_observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column(
            "subject_domain",
            postgresql.ENUM(name="subject_domain", create_type=False),
            nullable=False,
        ),
        sa.Column("component_ref", sa.String(length=200), nullable=False),
        sa.Column("target_revision", sa.String(length=200), nullable=True),
        sa.Column("probe", sa.String(length=160), nullable=False),
        sa.Column("probe_version", sa.String(length=40), nullable=False),
        sa.Column("prompt_digest", sa.String(length=64), nullable=True),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("samples", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "privacy_scope",
            postgresql.ENUM(name="privacy_scope", create_type=False),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_eval_observation_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_observation")),
        # Idempotency. A runner retrying after a timeout carries the same run_id, and without
        # this a retry storm manufactures corroboration out of one bad afternoon.
        sa.UniqueConstraint(
            "run_id", "tenant_id", name="eval_observation_run_is_unique_per_owner"
        ),
        sa.CheckConstraint(
            "(privacy_scope = 'tenant') = (tenant_id IS NOT NULL)",
            name=op.f("ck_eval_observation_eval_observation_tenant_scope_requires_tenant"),
        ),
        sa.CheckConstraint(
            "runs > 0", name=op.f("ck_eval_observation_eval_observation_runs_positive")
        ),
        sa.CheckConstraint(
            "failures >= 0 AND errors >= 0 AND failures + errors <= runs",
            name=op.f("ck_eval_observation_eval_observation_counts_are_consistent"),
        ),
    )
    # The pooling key. Every column is part of "the same question asked of the same thing";
    # drop one and results answering different questions get summed together.
    op.create_index(
        "ix_eval_observation_pool",
        "eval_observation",
        [
            "subject_domain",
            "component_ref",
            "target_revision",
            "probe",
            "probe_version",
            "privacy_scope",
            "tenant_id",
        ],
    )

    op.execute("ALTER TABLE eval_observation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_observation FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY eval_observation_scope ON eval_observation USING ({SCOPE})")
    op.execute(
        f"CREATE POLICY eval_observation_write ON eval_observation "
        f"FOR INSERT WITH CHECK ({SCOPE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS eval_observation_write ON eval_observation")
    op.execute("DROP POLICY IF EXISTS eval_observation_scope ON eval_observation")
    op.drop_index("ix_eval_observation_pool", table_name="eval_observation")
    op.drop_table("eval_observation")

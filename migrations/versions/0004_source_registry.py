"""Persisted crawl sources.

Until now every crawl was a manual trigger with the URL in the request body, so nothing
survived a restart and nothing ran on a schedule. A source is now a row, which is what lets a
sweep ask "what is due" and what lets staleness be measured at all.

RLS follows the same shape as bug_record: public and subscriber sources are visible to
everyone, tenant sources only to their owner.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CTX = "NULLIF(current_setting('bugmine.tenant_id', true), '')"
SCOPE = f"privacy_scope IN ('public', 'subscriber') OR tenant_id = {CTX}::uuid"


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "subject_domain",
            postgresql.ENUM(name="subject_domain", create_type=False),
            nullable=False,
        ),
        sa.Column("component_ref", sa.String(length=200), nullable=True),
        sa.Column("ecosystem", sa.String(length=50), nullable=True),
        sa.Column(
            "privacy_scope",
            postgresql.ENUM(name="privacy_scope", create_type=False),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_source_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
        sa.UniqueConstraint("url", "tenant_id", name="source_is_unique_per_owner"),
        sa.CheckConstraint(
            "(privacy_scope = 'tenant') = (tenant_id IS NOT NULL)",
            name=op.f("ck_source_source_tenant_scope_requires_tenant"),
        ),
    )
    op.create_index("ix_source_due", "source", ["enabled", "last_attempt_at"])

    op.execute("ALTER TABLE source ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY source_scope ON source USING ({SCOPE})")
    op.execute(f"CREATE POLICY source_write ON source FOR INSERT WITH CHECK ({SCOPE})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS source_write ON source")
    op.execute("DROP POLICY IF EXISTS source_scope ON source")
    op.drop_index("ix_source_due", table_name="source")
    op.drop_table("source")

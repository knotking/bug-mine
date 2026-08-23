"""Per-principal spending quotas.

Distinct from `job.budget_tokens`, which bounds one job against a runaway loop. This bounds what
a tenant, team or user can spend in a month — a thousand well-behaved jobs each inside its own
ceiling can still exceed what a customer agreed to.

RLS is the plain tenant pattern: a quota always belongs to a tenant, so there is no system-row
exception of the kind jobs and usage events need.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CTX = "NULLIF(current_setting('bugmine.tenant_id', true), '')"


def upgrade() -> None:
    op.create_table(
        "quota",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("limit_micros", sa.BigInteger(), nullable=True),
        sa.Column("limit_tokens", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One limit per principal: two rows for the same team would make the effective limit
        # depend on row order.
        sa.UniqueConstraint(
            "tenant_id", "team_id", "user_id", name="quota_is_unique_per_principal"
        ),
        # A row narrowing to both a team and a user would be ambiguous under most-specific-wins.
        sa.CheckConstraint(
            "NOT (team_id IS NOT NULL AND user_id IS NOT NULL)",
            name="quota_narrows_to_at_most_one_principal",
        ),
    )
    op.create_index("ix_quota_tenant", "quota", ["tenant_id"])

    op.execute("ALTER TABLE quota ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE quota FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY quota_own ON quota USING (tenant_id = {CTX}::uuid) "
        f"WITH CHECK (tenant_id = {CTX}::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS quota_own ON quota")
    op.drop_index("ix_quota_tenant", table_name="quota")
    op.drop_table("quota")

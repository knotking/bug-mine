"""The usage ledger.

Append-only. Rows carry their price at the rate card in effect when written, so a historical
invoice is reproducible rather than recomputed.

RLS follows the tenant pattern with the system exception jobs already have: system-borne spend
has no tenant and is visible only to a session with no tenant context.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CTX = "NULLIF(current_setting('bugmine.tenant_id', true), '')"
SYSTEM_OR_OWN = f"(tenant_id IS NULL AND {CTX} IS NULL) OR tenant_id = {CTX}::uuid"

_ENUMS = {
    "cost_bearer": ("system", "tenant"),
    "usage_purpose": ("extract", "scan_judge", "advise", "eval"),
}


def upgrade() -> None:
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "usage_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "cost_bearer", postgresql.ENUM(name="cost_bearer", create_type=False), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "purpose", postgresql.ENUM(name="usage_purpose", create_type=False), nullable=False
        ),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("inference_tier", sa.String(length=40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("rate_card_version", sa.String(length=40), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["job.id"], name=op.f("fk_usage_event_job_id_job"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_usage_event_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_usage_event_team_id_team"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_usage_event_user_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_event")),
    )
    op.create_index("ix_usage_tenant_time", "usage_event", ["tenant_id", "occurred_at"])
    op.create_index("ix_usage_bearer_time", "usage_event", ["cost_bearer", "occurred_at"])
    op.create_index("ix_usage_job", "usage_event", ["job_id"])

    op.execute("ALTER TABLE usage_event ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE usage_event FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY usage_system_or_own ON usage_event USING ({SYSTEM_OR_OWN})")
    op.execute(f"CREATE POLICY usage_write ON usage_event FOR INSERT WITH CHECK ({SYSTEM_OR_OWN})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS usage_write ON usage_event")
    op.execute("DROP POLICY IF EXISTS usage_system_or_own ON usage_event")
    op.drop_table("usage_event")
    for name in _ENUMS:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

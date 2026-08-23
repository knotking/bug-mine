"""Allow system-borne jobs, which have no tenant.

A global crawl is not done on behalf of any customer — `job.tenant_id` is null for it, and the
cost bearer is the system. Migration 0002 gave `job` a WITH CHECK requiring a matching tenant,
which refuses precisely those rows, so the crawl worker could not record its own work.

The predicate here keeps system rows visible only to sessions that have no tenant context —
the workers and the operator. A tenant session sees its own jobs and nothing else, so this does
not turn operational activity into something customers can enumerate.

This is the fifth policy of this shape to need correcting. The recurring mistake is writing the
tenant predicate first and discovering the exception afterwards; the exceptions are api_key
(establishes the tenant), invite (redeemed before the tenant is known), and now job (may have
no tenant at all).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CTX = "NULLIF(current_setting('bugmine.tenant_id', true), '')"
SYSTEM_OR_OWN = f"(tenant_id IS NULL AND {CTX} IS NULL) OR tenant_id = {CTX}::uuid"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS job_tenant_isolation ON job")
    op.execute("DROP POLICY IF EXISTS job_write ON job")
    op.execute(f"CREATE POLICY job_system_or_own ON job USING ({SYSTEM_OR_OWN})")
    op.execute(f"CREATE POLICY job_write ON job FOR INSERT WITH CHECK ({SYSTEM_OR_OWN})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS job_write ON job")
    op.execute("DROP POLICY IF EXISTS job_system_or_own ON job")
    op.execute(f"CREATE POLICY job_tenant_isolation ON job USING (tenant_id = {CTX}::uuid)")
    op.execute(f"CREATE POLICY job_write ON job FOR INSERT WITH CHECK (tenant_id = {CTX}::uuid)")

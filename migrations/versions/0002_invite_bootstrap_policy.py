"""Let an invite be redeemed before the tenant is known.

`accept_invite` is handed a token and must discover which tenant it belongs to — the same
chicken-and-egg `api_key` already has. A policy keyed on the tenant being known hides the row
needed to learn it, so redemption fails with an RLS violation rather than an auth error.

**Only `invite` gets this.** The token is the credential and is stored only as a hash, so
reading the row without tenant context is no weaker than the API-key path.

`team` and `membership` deliberately do *not*. An earlier draft extended the same relaxation to
them for symmetry, which would have let any session with no tenant context enumerate every
tenant's teams and every member's email address. Provisioning writes those rows inside a
tenant-scoped session instead, which is both correct and unnecessary to relax.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOTSTRAP = (
    "NULLIF(current_setting('bugmine.tenant_id', true), '') IS NULL "
    "OR tenant_id = NULLIF(current_setting('bugmine.tenant_id', true), '')::uuid"
)
OWN_TENANT = "tenant_id = NULLIF(current_setting('bugmine.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS invite_tenant_isolation ON invite")
    op.execute(f"CREATE POLICY invite_bootstrap_or_own ON invite USING ({BOOTSTRAP})")
    # USING governs reads; an INSERT needs WITH CHECK or it is refused outright.
    op.execute(f"CREATE POLICY invite_write ON invite FOR INSERT WITH CHECK ({BOOTSTRAP})")

    # These keep strict isolation on read, and gain the WITH CHECK their inserts need.
    for table in ("team", "membership", "scan", "job"):
        op.execute(f"CREATE POLICY {table}_write ON {table} FOR INSERT WITH CHECK ({OWN_TENANT})")

    op.execute(f"CREATE POLICY api_key_write ON api_key FOR INSERT WITH CHECK ({OWN_TENANT})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS api_key_write ON api_key")
    for table in ("team", "membership", "scan", "job"):
        op.execute(f"DROP POLICY IF EXISTS {table}_write ON {table}")
    op.execute("DROP POLICY IF EXISTS invite_write ON invite")
    op.execute("DROP POLICY IF EXISTS invite_bootstrap_or_own ON invite")
    op.execute(
        "CREATE POLICY invite_tenant_isolation ON invite USING "
        "(tenant_id = NULLIF(current_setting('bugmine.tenant_id', true), '')::uuid)"
    )

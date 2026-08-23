"""Record manifests a scan could not resolve.

A repository declaring dependencies in `pyproject.toml` with no lockfile yields no versions —
deliberately, since an unpinned range cannot be matched against applicability. Without this
column the scan reports zero findings and zero uncovered components, which is exactly what a
genuinely clean repository reports. "We found nothing" and "we could not read your dependencies"
are different answers and FR-39 forbids presenting the second as the first.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scan",
        sa.Column("unresolved_manifests", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("scan", "unresolved_manifests")

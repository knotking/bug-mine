"""Persist how many matches reachability removed.

The count is computed on every scan and returned in the worker's HTTP response, which Cloud
Tasks consumes and discards — so the number that justifies the entire product was being thrown
away. Without it a scan reporting three findings is indistinguishable from one that had three
matches, and the difference is the whole claim.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scan",
        sa.Column("suppressed_unreachable", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan", sa.Column("dependencies_scanned", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("scan", "dependencies_scanned")
    op.drop_column("scan", "suppressed_unreachable")

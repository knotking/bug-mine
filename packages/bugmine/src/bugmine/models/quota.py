"""Spending limits for a tenant, a team, or a user.

Distinct from `Job.budget_tokens`, which is a per-job ceiling that stops one runaway extraction.
That protects against a bug; this protects against a *bill*. A thousand well-behaved jobs each
inside its ceiling can still spend more than a customer agreed to.

**The limit is denominated in cost, not tokens.** Tokens are not comparable across models — the
same count against a larger model costs several times as much — so a token budget silently means
different things depending on which model served the request. A token limit is offered as well,
for teams who think in tokens, but cost is the one that bounds what can actually be owed.

Resolution is most-specific-wins: a user limit governs that user, otherwise their team's,
otherwise the tenant's. A team without its own limit is bounded by the tenant's rather than
unbounded, because the failure of a missing limit should be restrictive.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow


class Quota(Base):
    """One limit, for one principal, per calendar month.

    Monthly and calendar-aligned on purpose: a rolling window makes "how much is left" a
    question nobody can answer without running a query, and a limit a user cannot predict is one
    they cannot plan around.
    """

    __tablename__ = "quota"

    id: Mapped[uuid.UUID] = pk_uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    """Both null means the limit is the tenant's. Exactly one non-null narrows it."""

    limit_micros: Mapped[int | None] = mapped_column(BigInteger)
    """Monthly spend ceiling in cost micros. The unit that bounds what can be owed."""
    limit_tokens: Mapped[int | None] = mapped_column(BigInteger)
    """Optional monthly token ceiling, for teams who budget in tokens. Both may be set; the
    first to be reached stops the spend."""

    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # One limit per principal. Without this, two rows for the same team would make the
        # effective limit depend on row order.
        UniqueConstraint("tenant_id", "team_id", "user_id", name="quota_is_unique_per_principal"),
        Index("ix_quota_tenant", "tenant_id"),
    )

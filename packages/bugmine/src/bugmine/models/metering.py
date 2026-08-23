"""The usage ledger.

Every model call writes one row, at the time of the call. This is not analytics: the ceiling
has to be enforced while a job runs, because by the time an aggregation pipeline sees the
events the tokens are spent and the money is gone.

The constraint that shapes everything: **a generation cannot be interrupted once it starts.**
So the cap is not a check during generation — it is `max_output_tokens` on each call, derived
from what remains, plus a running-total check between calls. The first stops a runaway
generation, the second stops a runaway loop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.types import pg_enum


class CostBearer(StrEnum):
    """Who pays.

    A property of the job's origin, never of the worker. The same extraction worker serves a
    global crawl and a tenant-triggered one; reading the bearer from the worker would
    misattribute half the spend.
    """

    SYSTEM = "system"
    TENANT = "tenant"


class UsagePurpose(StrEnum):
    """Cost per capability, not only per tenant."""

    EXTRACT = "extract"
    SCAN_JUDGE = "scan_judge"
    ADVISE = "advise"
    EVAL = "eval"


class UsageEvent(Base):
    __tablename__ = "usage_event"

    id: Mapped[uuid.UUID] = pk_uuid()
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job.id", ondelete="SET NULL"))

    cost_bearer: Mapped[CostBearer] = mapped_column(
        pg_enum(CostBearer, "cost_bearer"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    purpose: Mapped[UsagePurpose] = mapped_column(
        pg_enum(UsagePurpose, "usage_purpose"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    inference_tier: Mapped[str] = mapped_column(String(40), nullable=False, default="hosted")
    """NFR-21 lets a tenant pin to self-hosted inference. That tier consumes GPU-seconds rather
    than API tokens, so token counts and cost are separate columns — one is not derivable from
    the other."""

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Billed at a different rate. Counting total input over-bills exactly the workloads that
    reuse a cached prefix, which are the ones that benefit most from doing so."""

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    succeeded: Mapped[bool] = mapped_column(nullable=False, default=True)
    """A call that errored after generating tokens still cost money. Counting only successes
    under-accounts, and the gap widens precisely when the system is unhealthy."""

    rate_card_version: Mapped[str] = mapped_column(String(40), nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    """Priced when written, never recomputed. Repricing a March invoice with August rates
    produces a number matching neither what was charged nor what it cost — and it still looks
    like a plausible invoice, which is what makes it dangerous."""

    occurred_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_usage_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_usage_bearer_time", "cost_bearer", "occurred_at"),
        Index("ix_usage_job", "job_id"),
    )

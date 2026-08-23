"""Cost metering and ceiling enforcement.

Sits on the critical path of every job that spends, not in a downstream rollup. A system that
bills on a quantity it cannot cap is unbounded by construction — and these workers are
model-driven loops over content nobody controls, so one pathological input is all it takes.

Enforcement has two halves because a generation cannot be interrupted:

1. `remaining_output_budget()` gives the value for `max_output_tokens` on the next call. This
   is the only hard cap that exists; it stops one runaway generation.
2. `check()` compares the running total between calls. It stops a runaway *loop* — a thousand
   individually-small calls that the first check would never notice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine.models import CostBearer, UsageEvent, UsagePurpose
from bugmine.models.jobs import Job

RATE_CARD_VERSION = "2026-08"

# Micros per token. Verify against current provider pricing before relying on invoices — these
# are a starting point, not a quoted rate.
RATE_CARD: dict[str, dict[str, float]] = {
    "gemini-2.0-flash": {"input": 0.15, "output": 0.60, "cached_input": 0.0375},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.075},
    "default": {"input": 0.50, "output": 2.00, "cached_input": 0.125},
}


class BudgetExceeded(Exception):
    """Raised when a job has spent its reservation.

    A distinct exception, not a generic failure: a user who hit a ceiling needs to know it was
    a limit rather than a crash, because the remedies have nothing in common.
    """


@dataclass(frozen=True)
class Spend:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_input_tokens


def price(model_id: str, spend: Spend) -> int:
    """Cost in micros, at the rate card in effect now.

    A model absent from the card raises rather than defaulting to zero: a silently free model
    is a billing hole that grows quietly, and it looks like success.
    """
    rates = RATE_CARD.get(model_id)
    if rates is None:
        raise KeyError(f"No rate card entry for {model_id!r}. Add one before using it.")
    return round(
        spend.input_tokens * rates["input"]
        + spend.output_tokens * rates["output"]
        + spend.cached_input_tokens * rates["cached_input"]
    )


def reserve(session: Session, job: Job, tokens: int) -> None:
    """Set a job's allowance before it starts."""
    job.budget_tokens = tokens
    job.spent_tokens = 0


def remaining(job: Job) -> int:
    if job.budget_tokens is None:
        return 2**31  # unbounded, and deliberately obvious in a log
    return max(0, job.budget_tokens - job.spent_tokens)


def remaining_output_budget(job: Job, *, model_max: int = 8192) -> int:
    """The value to pass as `max_output_tokens` on the next call.

    Deriving it from what remains, rather than from a constant, is what makes the ceiling real:
    the provider enforces it, and nothing else can once generation has begun.
    """
    return max(1, min(model_max, remaining(job)))


def check(job: Job) -> None:
    """Raise if the reservation is exhausted. Call between model calls, never after the loop."""
    if job.budget_tokens is not None and job.spent_tokens >= job.budget_tokens:
        raise BudgetExceeded(
            f"Reservation of {job.budget_tokens} tokens exhausted after {job.spent_tokens}."
        )


def record(
    session: Session,
    *,
    job: Job | None,
    spend: Spend,
    model_id: str,
    purpose: UsagePurpose,
    cost_bearer: CostBearer,
    tenant_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    inference_tier: str = "hosted",
    attempt: int = 1,
    succeeded: bool = True,
) -> UsageEvent:
    """Write one ledger row and add the spend to the job's running total.

    Called for failed attempts too. A call that errored after generating tokens still cost
    money, and omitting those under-accounts exactly when the system is unhealthy.
    """
    if (cost_bearer is CostBearer.TENANT) != (tenant_id is not None):
        raise ValueError("a tenant-borne cost needs a tenant, and a tenant needs tenant bearing")

    event = UsageEvent(
        job_id=job.id if job else None,
        cost_bearer=cost_bearer,
        tenant_id=tenant_id,
        team_id=team_id,
        user_id=user_id,
        purpose=purpose,
        model_id=model_id,
        inference_tier=inference_tier,
        input_tokens=spend.input_tokens,
        output_tokens=spend.output_tokens,
        cached_input_tokens=spend.cached_input_tokens,
        attempt=attempt,
        succeeded=succeeded,
        rate_card_version=RATE_CARD_VERSION,
        cost_micros=price(model_id, spend),
    )
    session.add(event)

    if job is not None:
        job.spent_tokens += spend.total

    session.flush()
    return event


def spend_by_bearer(session: Session) -> dict[str, int]:
    """Cost in micros, split by who bears it.

    The split that matters commercially: system spend scales with how much of the world the
    catalog covers, not with how many customers read it, so it does not fall per tenant as
    customers are added.
    """
    rows = session.execute(
        select(UsageEvent.cost_bearer, func.sum(UsageEvent.cost_micros)).group_by(
            UsageEvent.cost_bearer
        )
    ).all()
    return {bearer.value: int(total or 0) for bearer, total in rows}

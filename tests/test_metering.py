"""Cost metering and ceiling enforcement.

The property under test throughout is that spend cannot run away. A system that bills on a
quantity it cannot cap is unbounded by construction, and these workers are model-driven loops
over content nobody controls.
"""

from __future__ import annotations

import uuid

import pytest
from bugmine import metering
from bugmine.db import tenant_session
from bugmine.metering import BudgetExceeded, Spend
from bugmine.models import CostBearer, JobType, UsagePurpose
from bugmine.models.jobs import Job
from sqlalchemy import Engine

MODEL = "gemini-2.0-flash"


def _job(session, tenant_id=None) -> Job:  # type: ignore[no-untyped-def]
    job = Job(job_type=JobType.EXTRACT, tenant_id=tenant_id, payload={})
    session.add(job)
    session.flush()
    return job


class TestPricing:
    def test_cached_input_is_cheaper_than_fresh_input(self) -> None:
        """Counting total input over-bills exactly the workloads that reuse a prefix."""
        fresh = metering.price(MODEL, Spend(input_tokens=1000))
        cached = metering.price(MODEL, Spend(cached_input_tokens=1000))
        assert cached < fresh

    def test_an_unknown_model_refuses_rather_than_pricing_at_zero(self) -> None:
        """A silently free model is a billing hole that grows quietly and looks like success."""
        with pytest.raises(KeyError, match="rate card"):
            metering.price("some-new-model", Spend(input_tokens=10))

    def test_price_is_stored_not_recomputed(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            job = _job(s)
            ev = metering.record(
                s,
                job=job,
                spend=Spend(1000, 500),
                model_id=MODEL,
                purpose=UsagePurpose.EXTRACT,
                cost_bearer=CostBearer.SYSTEM,
            )
            assert ev.cost_micros > 0
            assert ev.rate_card_version == metering.RATE_CARD_VERSION


class TestCeilings:
    def test_max_output_tokens_shrinks_as_budget_is_spent(self) -> None:
        """The only hard cap that exists — a generation cannot be stopped once begun."""
        job = Job(job_type=JobType.EXTRACT, payload={}, budget_tokens=1000, spent_tokens=0)
        assert metering.remaining_output_budget(job, model_max=8192) == 1000
        job.spent_tokens = 900
        assert metering.remaining_output_budget(job, model_max=8192) == 100

    def test_it_never_returns_zero(self) -> None:
        """Zero would be rejected by the provider as invalid rather than refused by us."""
        job = Job(job_type=JobType.EXTRACT, payload={}, budget_tokens=100, spent_tokens=500)
        assert metering.remaining_output_budget(job) >= 1

    def test_check_raises_once_the_reservation_is_gone(self, engine: Engine) -> None:
        """Stops a runaway loop, which max_output_tokens alone would never notice."""
        with tenant_session(engine, None) as s:
            job = _job(s)
            metering.reserve(s, job, 100)
            metering.record(
                s,
                job=job,
                spend=Spend(60, 50),
                model_id=MODEL,
                purpose=UsagePurpose.EXTRACT,
                cost_bearer=CostBearer.SYSTEM,
            )
            with pytest.raises(BudgetExceeded, match="exhausted"):
                metering.check(job)

    def test_a_job_within_budget_passes(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            job = _job(s)
            metering.reserve(s, job, 10_000)
            metering.record(
                s,
                job=job,
                spend=Spend(100, 50),
                model_id=MODEL,
                purpose=UsagePurpose.EXTRACT,
                cost_bearer=CostBearer.SYSTEM,
            )
            metering.check(job)


class TestAttribution:
    def test_failed_calls_are_still_recorded(self, engine: Engine) -> None:
        """A call that errored after generating tokens still cost money.

        Omitting them under-accounts precisely when the system is unhealthy.
        """
        with tenant_session(engine, None) as s:
            job = _job(s)
            ev = metering.record(
                s,
                job=job,
                spend=Spend(500, 200),
                model_id=MODEL,
                purpose=UsagePurpose.EXTRACT,
                cost_bearer=CostBearer.SYSTEM,
                succeeded=False,
                attempt=3,
            )
            assert ev.succeeded is False and ev.attempt == 3
            assert job.spent_tokens == 700, "a failed call must still consume budget"

    def test_tenant_bearing_requires_a_tenant(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            job = _job(s)
            with pytest.raises(ValueError, match="tenant"):
                metering.record(
                    s,
                    job=job,
                    spend=Spend(1, 1),
                    model_id=MODEL,
                    purpose=UsagePurpose.EXTRACT,
                    cost_bearer=CostBearer.TENANT,
                    tenant_id=None,
                )

    def test_system_and_tenant_spend_are_separable(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The split that matters commercially: system spend does not fall per tenant."""
        a, _ = two_tenants
        with tenant_session(engine, None) as s:
            metering.record(
                s,
                job=_job(s),
                spend=Spend(1000, 100),
                model_id=MODEL,
                purpose=UsagePurpose.EXTRACT,
                cost_bearer=CostBearer.SYSTEM,
            )
        with tenant_session(engine, a) as s:
            metering.record(
                s,
                job=_job(s, tenant_id=a),
                spend=Spend(500, 50),
                model_id=MODEL,
                purpose=UsagePurpose.SCAN_JUDGE,
                cost_bearer=CostBearer.TENANT,
                tenant_id=a,
            )
        with tenant_session(engine, None, commit=False) as s:
            by_bearer = metering.spend_by_bearer(s)
        assert by_bearer.get("system", 0) > 0

    def test_a_tenant_cannot_see_another_tenants_spend(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        with tenant_session(engine, a) as s:
            metering.record(
                s,
                job=_job(s, tenant_id=a),
                spend=Spend(1000, 500),
                model_id=MODEL,
                purpose=UsagePurpose.SCAN_JUDGE,
                cost_bearer=CostBearer.TENANT,
                tenant_id=a,
            )
        with tenant_session(engine, b, commit=False) as s:
            assert metering.spend_by_bearer(s).get("tenant", 0) == 0

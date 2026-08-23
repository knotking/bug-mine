"""Per-principal spending limits.

Distinct from the per-job ceiling, which stops one runaway loop. This bounds what a tenant,
team or user can spend in a month — a thousand well-behaved jobs each inside its own ceiling can
still exceed what a customer agreed to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bugmine.db import tenant_session
from bugmine.metering import (
    QuotaExceeded,
    check_quota,
    month_start,
    quota_status,
    resolve_quota,
)
from bugmine.models import CostBearer, Quota, Team, UsageEvent, UsagePurpose
from sqlalchemy.engine import Engine


def _spend(
    engine: Engine,
    tenant: uuid.UUID,
    *,
    micros: int,
    tokens: int = 0,
    team_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    when: datetime | None = None,
) -> None:
    with tenant_session(engine, tenant) as s:
        event = UsageEvent(
            cost_bearer=CostBearer.TENANT,
            tenant_id=tenant,
            team_id=team_id,
            user_id=user_id,
            purpose=UsagePurpose.EXTRACT,
            model_id="gemini-3.7-flash",
            input_tokens=tokens,
            output_tokens=0,
            rate_card_version="v1",
            cost_micros=micros,
        )
        if when is not None:
            event.occurred_at = when
        s.add(event)


def _team(engine: Engine, tenant: uuid.UUID) -> uuid.UUID:
    """The two_tenants fixture creates tenants, not teams."""
    with tenant_session(engine, tenant) as s:
        team = Team(tenant_id=tenant, name=f"team-{uuid.uuid4().hex[:6]}")
        s.add(team)
        s.flush()
        return team.id


def _quota(
    engine: Engine,
    tenant: uuid.UUID,
    *,
    micros: int | None = None,
    tokens: int | None = None,
    team_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    with tenant_session(engine, tenant) as s:
        s.add(
            Quota(
                tenant_id=tenant,
                team_id=team_id,
                user_id=user_id,
                limit_micros=micros,
                limit_tokens=tokens,
            )
        )


class TestEnforcement:
    def test_no_quota_means_no_limit(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        tenant, _ = two_tenants
        _spend(engine, tenant, micros=10_000_000)
        with tenant_session(engine, tenant, commit=False) as s:
            status = check_quota(s, tenant_id=tenant)
        assert status.scope == "none"

    def test_spending_under_the_limit_passes(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        tenant, _ = two_tenants
        _quota(engine, tenant, micros=1_000)
        _spend(engine, tenant, micros=400)
        with tenant_session(engine, tenant, commit=False) as s:
            assert check_quota(s, tenant_id=tenant).scope == "tenant"

    def test_reaching_the_limit_raises(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        tenant, _ = two_tenants
        _quota(engine, tenant, micros=1_000)
        _spend(engine, tenant, micros=1_000)
        with tenant_session(engine, tenant, commit=False) as s, pytest.raises(QuotaExceeded):
            check_quota(s, tenant_id=tenant)

    def test_a_token_limit_is_enforced_too(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        """Both units may be set; whichever is reached first stops the spend."""
        tenant, _ = two_tenants
        _quota(engine, tenant, tokens=500)
        _spend(engine, tenant, micros=1, tokens=500)
        with tenant_session(engine, tenant, commit=False) as s, pytest.raises(QuotaExceeded) as exc:
            check_quota(s, tenant_id=tenant)
        assert exc.value.unit == "tokens"


class TestResolution:
    def test_the_most_specific_limit_wins(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        tenant, _ = two_tenants
        team_id = _team(engine, tenant)
        _quota(engine, tenant, micros=1_000_000)
        _quota(engine, tenant, micros=50, team_id=team_id)
        with tenant_session(engine, tenant, commit=False) as s:
            resolved = resolve_quota(s, tenant_id=tenant, team_id=team_id)
        assert resolved is not None and resolved.limit_micros == 50

    def test_a_team_without_its_own_limit_falls_back_to_the_tenants(
        self, engine: Engine, two_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        """A missing limit must be restrictive, not permissive — otherwise adding a team is a
        way to escape the tenant's ceiling."""
        tenant, _ = two_tenants
        _quota(engine, tenant, micros=100)
        team_id = _team(engine, tenant)
        with tenant_session(engine, tenant, commit=False) as s:
            resolved = resolve_quota(s, tenant_id=tenant, team_id=team_id)
        assert resolved is not None and resolved.limit_micros == 100

    def test_a_team_limit_counts_the_whole_teams_spend(
        self, engine: Engine, two_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        """Measured at the scope the limit is set at. Counting only the calling user's spend
        would give every member the team's full allowance."""
        tenant, _ = two_tenants
        team_id = _team(engine, tenant)
        _quota(engine, tenant, micros=100, team_id=team_id)
        # Two separate charges against the same team. Who made them is not the point — the
        # limit is the team's, so both count against it.
        _spend(engine, tenant, micros=60, team_id=team_id)
        _spend(engine, tenant, micros=60, team_id=team_id)
        with tenant_session(engine, tenant, commit=False) as s, pytest.raises(QuotaExceeded):
            check_quota(s, tenant_id=tenant, team_id=team_id)


class TestPeriod:
    def test_last_months_spend_does_not_count(self, engine: Engine, two_tenants) -> None:  # type: ignore[no-untyped-def]
        """Calendar-aligned: a limit resets, or it is a lifetime cap wearing a monthly label."""
        tenant, _ = two_tenants
        _quota(engine, tenant, micros=100)
        _spend(engine, tenant, micros=5_000, when=month_start() - timedelta(days=1))
        with tenant_session(engine, tenant, commit=False) as s:
            status = quota_status(s, tenant_id=tenant)
        assert status.spent_micros == 0
        assert not status.exceeded

    def test_month_start_is_utc_midnight_on_the_first(self) -> None:
        start = month_start(datetime(2026, 8, 23, 15, 4, 5, tzinfo=UTC))
        assert (start.day, start.hour, start.minute) == (1, 0, 0)


class TestIsolation:
    def test_a_tenants_spend_does_not_consume_anothers_quota(
        self, engine: Engine, two_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        a, b = two_tenants
        _quota(engine, a, micros=100)
        _spend(engine, b, micros=10_000)
        with tenant_session(engine, a, commit=False) as s:
            assert not quota_status(s, tenant_id=a).exceeded

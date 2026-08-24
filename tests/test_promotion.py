"""Promotion: one tenant's observation becoming shared knowledge.

The most dangerous path in the system, and the tests are written against the two reasons
sanitization alone is insufficient. Privacy: a single-tenant observation can identify its source
even with the code stripped. Quality: a scan-derived candidate is an inference about code read
once, and promoting it directly would let one error reach every tenant wearing a citation.
"""

from __future__ import annotations

import uuid

import pytest
from bugmine.db import tenant_session
from bugmine.models import (
    BugRecord,
    BugType,
    LifecycleState,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)
from bugmine.promotion import CORROBORATION_THRESHOLD, promote
from sqlalchemy import select
from sqlalchemy.engine import Engine

# Promotion is the one path that legitimately reads across tenants, so it runs from a session
# that bypasses RLS. Testing it through a normal session would prove only that RLS works.


def _candidate(engine: Engine, tenant, identity: str, ref: str) -> None:  # type: ignore[no-untyped-def]
    """A scan-derived candidate, as a scan would leave it: tenant-scoped and not yet shared."""
    from bugmine.catalog import components

    with tenant_session(engine, tenant) as s:
        component = components.get_or_create(
            s, subject_domain=SubjectDomain.REPO_LIBRARY, ref=ref, ecosystem="pypi"
        )
        s.add(
            BugRecord(
                component_id=component.id,
                subject_domain=SubjectDomain.REPO_LIBRARY,
                bug_type=BugType.FUNCTIONAL,
                applicability={"kind": "version_range", "scheme": "generic",
                               "introduced_in": "1.0.0", "fixed_in": None},
                origin=RecordOrigin.SCAN_DERIVED,
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=tenant,
                lifecycle_state=LifecycleState.CANDIDATE,
                identity_key=identity,
            )
        )


@pytest.fixture
def three_tenants(engine: Engine):  # type: ignore[no-untyped-def]
    from bugmine.models import Tenant

    ids = []
    with tenant_session(engine, None) as s:
        for _ in range(3):
            slug = f"t{uuid.uuid4().hex[:8]}"
            tenant = Tenant(name=slug, slug=slug)
            s.add(tenant)
            s.flush()
            ids.append(tenant.id)
    return ids


class TestCorroboration:
    def test_one_observer_is_not_enough(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        """A single-tenant observation can identify its source even with the code stripped."""
        identity = f"id-{uuid.uuid4().hex[:8]}"
        _candidate(engine, three_tenants[0], identity, f"promo{uuid.uuid4().hex[:6]}")
        with tenant_session(owner_engine, None) as s:
            result = promote(s)
        assert identity not in result.promoted
        assert result.held.get(identity) == 1

    def test_the_threshold_promotes(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        identity = f"id-{uuid.uuid4().hex[:8]}"
        ref = f"promo{uuid.uuid4().hex[:6]}"
        for tenant in three_tenants[:CORROBORATION_THRESHOLD]:
            _candidate(engine, tenant, identity, ref)
        with tenant_session(owner_engine, None) as s:
            result = promote(s)
        assert identity in result.promoted

    def test_one_tenant_observing_repeatedly_does_not_corroborate_itself(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        """Counted on distinct tenants, not rows. Counting rows would let a single customer's
        repeated scans promote their own candidate into everyone else's catalog."""
        identity = f"id-{uuid.uuid4().hex[:8]}"
        ref = f"promo{uuid.uuid4().hex[:6]}"
        for _ in range(5):
            _candidate(engine, three_tenants[0], identity, ref)
        with tenant_session(owner_engine, None) as s:
            result = promote(s)
        assert identity not in result.promoted
        assert result.held.get(identity) == 1


class TestWhatCrossesTheBoundary:
    def test_a_promoted_record_carries_no_tenant(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:  # type: ignore[no-untyped-def]
        """FR-45. What crosses is a fact about software; the tenant it was seen at does not."""
        identity = f"id-{uuid.uuid4().hex[:8]}"
        ref = f"promo{uuid.uuid4().hex[:6]}"
        for tenant in three_tenants:
            _candidate(engine, tenant, identity, ref)
        with tenant_session(owner_engine, None) as s:
            promote(s)
            promoted = s.execute(
                select(BugRecord).where(
                    BugRecord.identity_key == identity,
                    BugRecord.privacy_scope == PrivacyScope.PUBLIC,
                )
            ).scalars().all()
        assert promoted
        assert all(r.tenant_id is None for r in promoted)
        assert all(r.lifecycle_state is LifecycleState.ACTIVE for r in promoted)

    def test_a_crawled_record_is_not_a_promotion_candidate(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:
        """Promotion is for scan-derived observations. A crawled record is already shared or
        already decided, and running it through corroboration would change its meaning."""
        from bugmine.catalog import IncomingBug, write

        ref = f"promo{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            write(
                s,
                IncomingBug(
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    component_ref=ref,
                    ecosystem="pypi",
                    bug_type=BugType.SECURITY,
                    applicability={"kind": "version_range", "scheme": "generic",
                                   "introduced_in": "1.0", "fixed_in": None},
                    title="crawled record",
                ),
                origin=RecordOrigin.CRAWLED,
            )
        with tenant_session(owner_engine, None) as s:
            result = promote(s)
        assert not any(ref in k for k in result.promoted)


class TestHeldCandidatesAreVisible:
    def test_short_candidates_are_reported_with_their_count(
        self, engine: Engine, owner_engine: Engine, three_tenants
    ) -> None:
        """A candidate stuck below the threshold forever is a signal about the threshold.
        Silence would make it invisible."""
        identity = f"id-{uuid.uuid4().hex[:8]}"
        ref = f"promo{uuid.uuid4().hex[:6]}"
        for tenant in three_tenants[:2]:
            _candidate(engine, tenant, identity, ref)
        with tenant_session(owner_engine, None) as s:
            result = promote(s)
        assert result.held.get(identity) == 2

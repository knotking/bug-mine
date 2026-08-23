"""The M0 gate: tenant B cannot read tenant A's rows.

These assert a *security* property, so they are written as adversarial cases rather than
happy-path ones. Every test here tries to see something it should not.
"""

from __future__ import annotations

import uuid

from bugmine.db import tenant_session
from bugmine.models import (
    BugRecord,
    BugType,
    Component,
    Job,
    JobType,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)
from sqlalchemy import Engine, select


def _component(session, ref: str) -> Component:
    c = Component(subject_domain=SubjectDomain.REPO_LIBRARY, canonical_ref=ref, ecosystem="pypi")
    session.add(c)
    session.flush()
    return c


class TestJobIsolation:
    def test_tenant_cannot_see_another_tenants_jobs(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants

        with tenant_session(engine, a) as s:
            s.add(Job(job_type=JobType.NOOP, tenant_id=a, payload={"secret": "a"}))

        with tenant_session(engine, b) as s:
            visible = s.execute(select(Job)).scalars().all()

        assert visible == [], "tenant B saw tenant A's job"

    def test_tenant_sees_its_own_jobs(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        with tenant_session(engine, a) as s:
            s.add(Job(job_type=JobType.NOOP, tenant_id=a, payload={}))
        with tenant_session(engine, a) as s:
            assert len(s.execute(select(Job)).scalars().all()) >= 1

    def test_anonymous_session_sees_no_tenant_jobs(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The public path. RLS makes this true without any handler-side filter.

        Asserted against a marker rather than an empty result: system-borne jobs legitimately
        have no tenant and *are* visible here, so an empty-list assertion would conflate "no
        tenant data leaked" with "no rows at all" and break as soon as anything else writes a
        system job.
        """
        a, _ = two_tenants
        marker = uuid.uuid4().hex
        with tenant_session(engine, a) as s:
            s.add(Job(job_type=JobType.NOOP, tenant_id=a, payload={"marker": marker}))
        with tenant_session(engine, None) as s:
            leaked = [
                j
                for j in s.execute(select(Job)).scalars().all()
                if j.payload.get("marker") == marker
            ]
        assert leaked == [], "a tenant's job was visible to an anonymous session"


class TestCatalogScope:
    def test_public_records_are_visible_to_everyone(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        with tenant_session(engine, None) as s:
            c = _component(s, f"pub-{uuid.uuid4().hex[:8]}")
            s.add(
                BugRecord(
                    component_id=c.id,
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    bug_type=BugType.BREAKING_CHANGE,
                    applicability={"kind": "version_range", "introduced_in": "1.0.0"},
                    origin=RecordOrigin.CRAWLED,
                    privacy_scope=PrivacyScope.PUBLIC,
                )
            )
        for viewer in (a, b, None):
            with tenant_session(engine, viewer) as s:
                found = (
                    s.execute(
                        select(BugRecord).where(BugRecord.privacy_scope == PrivacyScope.PUBLIC)
                    )
                    .scalars()
                    .all()
                )
                assert found, f"public record invisible to {viewer}"

    def test_tenant_record_is_invisible_to_other_tenants(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The finding that must never leak: a bug observed in one customer's code."""
        a, b = two_tenants
        with tenant_session(engine, a) as s:
            c = _component(s, f"priv-{uuid.uuid4().hex[:8]}")
            s.add(
                BugRecord(
                    component_id=c.id,
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    bug_type=BugType.FUNCTIONAL,
                    applicability={"kind": "version_range"},
                    origin=RecordOrigin.SCAN_DERIVED,
                    privacy_scope=PrivacyScope.TENANT,
                    tenant_id=a,
                )
            )

        with tenant_session(engine, b) as s:
            leaked = (
                s.execute(select(BugRecord).where(BugRecord.privacy_scope == PrivacyScope.TENANT))
                .scalars()
                .all()
            )
        assert leaked == [], "tenant B saw tenant A's private record"

        with tenant_session(engine, None) as s:
            leaked_public = (
                s.execute(select(BugRecord).where(BugRecord.privacy_scope == PrivacyScope.TENANT))
                .scalars()
                .all()
            )
        assert leaked_public == [], "a tenant record reached the anonymous public path"

    def test_tenant_sees_its_own_private_record(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        with tenant_session(engine, a) as s:
            c = _component(s, f"own-{uuid.uuid4().hex[:8]}")
            s.add(
                BugRecord(
                    component_id=c.id,
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    bug_type=BugType.FUNCTIONAL,
                    applicability={"kind": "version_range"},
                    origin=RecordOrigin.SCAN_DERIVED,
                    privacy_scope=PrivacyScope.TENANT,
                    tenant_id=a,
                )
            )
        with tenant_session(engine, a) as s:
            own = (
                s.execute(select(BugRecord).where(BugRecord.privacy_scope == PrivacyScope.TENANT))
                .scalars()
                .all()
            )
        assert own, "tenant A cannot see its own private record"


class TestScopeInvariant:
    def test_rls_refuses_a_tenant_record_from_an_anonymous_session(self, engine: Engine) -> None:
        """Defence in depth: RLS rejects the write before any constraint is consulted."""
        import pytest

        with pytest.raises(Exception) as exc, tenant_session(engine, None) as s:
            c = _component(s, f"bad-{uuid.uuid4().hex[:8]}")
            s.add(
                BugRecord(
                    component_id=c.id,
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    bug_type=BugType.FUNCTIONAL,
                    applicability={"kind": "version_range"},
                    origin=RecordOrigin.SCAN_DERIVED,
                    privacy_scope=PrivacyScope.TENANT,
                    tenant_id=None,
                )
            )
        assert "row-level security" in str(exc.value)

    def test_check_constraint_rejects_tenant_scope_without_a_tenant(
        self, owner_engine: Engine
    ) -> None:
        """A tenant-scoped record with no tenant is a bug, not a default.

        Tested as the owner, because RLS would otherwise refuse the write first and mask
        whether the constraint exists at all.
        """
        import psycopg
        import pytest
        from sqlalchemy.orm import Session

        with pytest.raises(Exception) as exc, Session(owner_engine) as s:
            c = _component(s, f"ck-{uuid.uuid4().hex[:8]}")
            s.add(
                BugRecord(
                    component_id=c.id,
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    bug_type=BugType.FUNCTIONAL,
                    applicability={"kind": "version_range"},
                    origin=RecordOrigin.SCAN_DERIVED,
                    privacy_scope=PrivacyScope.TENANT,
                    tenant_id=None,
                )
            )
            s.commit()
        assert isinstance(exc.value.__cause__, psycopg.errors.CheckViolation)
        assert "tenant_scope_requires_tenant" in str(exc.value)


class TestSystemJobs:
    """Jobs with no tenant — global crawls and backfills.

    The cost bearer for these is the system, not a customer, so `tenant_id` is null. The
    policy has to permit that without making operational activity visible to tenants.
    """

    def test_a_system_job_can_be_written_without_a_tenant(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            s.add(Job(job_type=JobType.CRAWL, tenant_id=None, payload={"url": "https://x.test"}))

    def test_a_tenant_cannot_see_system_jobs(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        marker = uuid.uuid4().hex
        with tenant_session(engine, None) as s:
            s.add(Job(job_type=JobType.CRAWL, tenant_id=None, payload={"marker": marker}))
        with tenant_session(engine, a) as s:
            found = [
                j
                for j in s.execute(select(Job)).scalars().all()
                if j.payload.get("marker") == marker
            ]
        assert found == [], "a tenant enumerated system operational activity"

    def test_the_system_can_see_its_own_jobs(self, engine: Engine) -> None:
        marker = uuid.uuid4().hex
        with tenant_session(engine, None) as s:
            s.add(Job(job_type=JobType.CRAWL, tenant_id=None, payload={"marker": marker}))
        with tenant_session(engine, None) as s:
            found = [
                j
                for j in s.execute(select(Job)).scalars().all()
                if j.payload.get("marker") == marker
            ]
        assert len(found) == 1

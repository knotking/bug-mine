"""Catalog write and retrieval.

The two tests that matter most here are the ones an LLM-backed system fails silently:
a re-crawl must not create a version, and an uncovered component must report *not covered*
rather than nothing.
"""

from __future__ import annotations

import uuid

import pytest
from bugmine.catalog import IncomingBug, Query, retrieve, write
from bugmine.db import tenant_session
from bugmine.models import (
    BugType,
    BugVersion,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)
from sqlalchemy import Engine, func, select


def _bug(ref: str, *, title: str = "Retry semantics changed", **kw) -> IncomingBug:
    return IncomingBug(
        subject_domain=SubjectDomain.REPO_LIBRARY,
        component_ref=ref,
        ecosystem="pypi",
        bug_type=BugType.BREAKING_CHANGE,
        applicability={
            "kind": "version_range",
            "scheme": "semver",
            "introduced_in": "0.3.0",
            "fixed_in": "0.3.4",
        },
        title=title,
        evidence_url="https://example.invalid/releases/v0.3.0",
        **kw,
    )


class TestDedup:
    def test_recrawling_unchanged_content_creates_no_version(self, engine: Engine) -> None:
        """The gate that bounds catalog growth.

        Without it, versioning tracks crawl frequency rather than reality — roughly 20x the
        extraction bill, growing with how often we look rather than how often things change.
        """
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, None) as s:
            first = write(s, _bug(ref), origin=RecordOrigin.CRAWLED)
        assert first.created_record and first.created_version

        for _ in range(3):
            with tenant_session(engine, None) as s:
                again = write(s, _bug(ref), origin=RecordOrigin.CRAWLED)
            assert again.deduplicated, "a re-crawl created a version"
            assert again.record_id == first.record_id

        with tenant_session(engine, None) as s:
            count = s.execute(
                select(func.count(BugVersion.id)).where(BugVersion.record_id == first.record_id)
            ).scalar_one()
        assert count == 1

    def test_changed_content_creates_a_new_version(self, engine: Engine) -> None:
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, None) as s:
            first = write(s, _bug(ref), origin=RecordOrigin.CRAWLED)
        with tenant_session(engine, None) as s:
            second = write(
                s,
                _bug(ref, title="Retry semantics changed (corrected)"),
                origin=RecordOrigin.CRAWLED,
            )

        assert second.created_version
        assert second.record_id == first.record_id, "a correction must not fork the record"
        with tenant_session(engine, None) as s:
            versions = (
                s.execute(
                    select(BugVersion.version_no)
                    .where(BugVersion.record_id == first.record_id)
                    .order_by(BugVersion.version_no)
                )
                .scalars()
                .all()
            )
        assert versions == [1, 2]


class TestComponentAliases:
    def test_aliases_resolve_to_one_component(self, engine: Engine) -> None:
        """postgres / postgresql / PostgreSQL are one thing.

        A mismatch here throws no error — it returns an empty result that reads as good news.
        """
        ref = f"postgresql-{uuid.uuid4().hex[:6]}"
        alias = ref.replace("postgresql", "postgres")
        with tenant_session(engine, None) as s:
            write(
                s,
                IncomingBug(
                    subject_domain=SubjectDomain.DATABASE,
                    component_ref=ref,
                    bug_type=BugType.PERFORMANCE,
                    applicability={"kind": "version_range", "introduced_in": "14.0"},
                    title="Planner regression",
                    aliases=(alias, ref.upper()),
                ),
                origin=RecordOrigin.CRAWLED,
            )

        for lookup in (ref, alias, ref.upper()):
            with tenant_session(engine, None) as s:
                matches, missing = retrieve(
                    s, Query(SubjectDomain.DATABASE, lookup, version="15.0")
                )
            assert missing is None, f"{lookup!r} did not resolve"
            assert matches, f"{lookup!r} resolved but matched nothing"


class TestRetrieval:
    @pytest.fixture
    def seeded(self, engine: Engine) -> str:
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, None) as s:
            write(s, _bug(ref), origin=RecordOrigin.CRAWLED)
        return ref

    @pytest.mark.parametrize(
        ("version", "expected"),
        [("0.3.0", True), ("0.3.3", True), ("0.2.9", False), ("0.3.4", False), ("1.0.0", False)],
    )
    def test_version_boundaries(
        self, engine: Engine, seeded: str, version: str, expected: bool
    ) -> None:
        """Off-by-one here is a quiet false positive — indistinguishable from a real finding."""
        with tenant_session(engine, None) as s:
            matches, _ = retrieve(
                s, Query(SubjectDomain.REPO_LIBRARY, seeded, version=version, ecosystem="pypi")
            )
        assert bool(matches) is expected

    def test_uncovered_component_reports_not_covered(self, engine: Engine) -> None:
        """The test an LLM-backed system fails silently.

        An empty result and 'we have no coverage' are different answers, and only one of them
        is honest when the catalog is thin.
        """
        with tenant_session(engine, None) as s:
            matches, missing = retrieve(
                s, Query(SubjectDomain.REPO_LIBRARY, f"absent-{uuid.uuid4().hex}", version="1.0.0")
            )
        assert matches == []
        assert missing is not None
        assert missing.reason == "component_not_in_catalog"

    def test_unknown_version_matches_but_is_unconfirmed(self, engine: Engine, seeded: str) -> None:
        with tenant_session(engine, None) as s:
            matches, _ = retrieve(
                s, Query(SubjectDomain.REPO_LIBRARY, seeded, version=None, ecosystem="pypi")
            )
        assert matches, "an unknown version must widen, not exclude"
        assert all(not m.version_confirmed for m in matches)


class TestScopeOnWrite:
    def test_tenant_scope_requires_a_tenant(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s, pytest.raises(ValueError, match="tenant scope"):
            write(
                s,
                _bug(f"lib-{uuid.uuid4().hex[:8]}"),
                origin=RecordOrigin.SCAN_DERIVED,
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=None,
            )

    def test_tenant_record_is_not_retrievable_by_others(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        ref = f"internal-{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, a) as s:
            write(
                s,
                _bug(ref, title="our sdk breaks on retry"),
                origin=RecordOrigin.SCAN_DERIVED,
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=a,
            )

        with tenant_session(engine, a) as s:
            mine, _ = retrieve(
                s, Query(SubjectDomain.REPO_LIBRARY, ref, version="0.3.1", ecosystem="pypi")
            )
        assert mine, "tenant A cannot retrieve its own record"

        with tenant_session(engine, b) as s:
            theirs, _ = retrieve(
                s, Query(SubjectDomain.REPO_LIBRARY, ref, version="0.3.1", ecosystem="pypi")
            )
        assert theirs == [], "tenant B retrieved tenant A's private record"

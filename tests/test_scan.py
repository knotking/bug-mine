"""Scan analysis: catalog matching narrowed by reachability.

The number that matters here is what gets *suppressed*. A scan that reports every catalog match
for every resolved dependency is a Dependabot report, and BugMine's argument is that most of
those matches never touch the project. So the tests check both directions: that an untouched
defect is narrowed away, and that an undetermined one never is.
"""

from __future__ import annotations

import uuid

import pytest
from bugmine.catalog import IncomingBug, write
from bugmine.db import tenant_session
from bugmine.inventory.models import Dependency
from bugmine.models import BugType, Job, JobType, RecordOrigin, Scan, SubjectDomain
from bugmine.scan import analyse
from sqlalchemy.engine import Engine

USES_SYMBOL = "from pydantic import eval_type_backport\n\neval_type_backport()\n"
IGNORES_SYMBOL = "from pydantic import BaseModel\n\n\nclass M(BaseModel):\n    pass\n"


def _seed(
    engine: Engine,
    *,
    title: str,
    ref: str = "pydantic",
    introduced: str = "2.0.0",
    ecosystem: str = "pypi",
) -> None:
    with tenant_session(engine, None) as s:
        write(
            s,
            IncomingBug(
                subject_domain=SubjectDomain.REPO_LIBRARY,
                component_ref=ref,
                ecosystem=ecosystem,
                bug_type=BugType.BREAKING_CHANGE,
                applicability={
                    "kind": "version_range",
                    "scheme": "generic",
                    "introduced_in": introduced,
                    "fixed_in": None,
                },
                title=title,
                evidence_url="https://example.invalid/rel",
            ),
            origin=RecordOrigin.CRAWLED,
        )


@pytest.fixture
def scan_id(engine: Engine, two_tenants) -> uuid.UUID:  # type: ignore[no-untyped-def]
    tenant, _ = two_tenants
    with tenant_session(engine, tenant) as s:
        job = Job(tenant_id=tenant, job_type=JobType.SCAN_ANALYZE, payload={})
        s.add(job)
        s.flush()
        scan = Scan(job_id=job.id, tenant_id=tenant, repo_ref="git@example.invalid:acme/app.git")
        s.add(scan)
        s.flush()
        return scan.id


def _analyse(engine: Engine, scan_id: uuid.UUID, sources: dict[str, str], *, version="2.5.0"):
    with tenant_session(engine, None) as s:
        return analyse(
            s,
            scan_id=scan_id,
            dependencies=[Dependency(ecosystem="pypi", name="pydantic", version=version)],
            sources=sources,
        )


class TestNarrowing:
    def test_an_untouched_defect_is_suppressed(self, engine: Engine, scan_id) -> None:  # type: ignore[no-untyped-def]
        """The claim the product rests on: the dependency is affected, the project is not."""
        _seed(engine, title="Remove support for `eval_type_backport()`")
        outcome = _analyse(engine, scan_id, {"app.py": IGNORES_SYMBOL})
        assert outcome.findings == []
        assert outcome.suppressed == 1

    def test_a_touched_defect_is_reported_as_reachable(self, engine: Engine, scan_id) -> None:  # type: ignore[no-untyped-def]
        _seed(engine, title="Remove support for `eval_type_backport()`")
        outcome = _analyse(engine, scan_id, {"app.py": USES_SYMBOL})
        assert len(outcome.findings) == 1
        finding = outcome.findings[0]
        assert finding.reachable is True
        assert finding.confidence == 1.0
        assert "app.py:" in (finding.detail or "")

    def test_every_finding_carries_a_citation(self, engine: Engine, scan_id) -> None:  # type: ignore[no-untyped-def]
        """ADR-0002 — grounding is structural. A finding built without a record is a bug."""
        _seed(engine, title="Remove support for `eval_type_backport()`")
        outcome = _analyse(engine, scan_id, {"app.py": USES_SYMBOL})
        assert all(f.citations for f in outcome.findings)


class TestUncertaintyIsReportedNotSuppressed:
    def test_a_record_naming_no_symbol_is_reported_undetermined(
        self, engine: Engine, scan_id
    ) -> None:  # type: ignore[no-untyped-def]
        _seed(engine, title="Drop support for Python 3.9")
        outcome = _analyse(engine, scan_id, {"app.py": IGNORES_SYMBOL})
        assert outcome.suppressed == 0
        assert len(outcome.findings) == 1
        finding = outcome.findings[0]
        assert finding.reachable is None
        assert finding.confidence == pytest.approx(0.5)

    def test_no_source_at_all_is_undetermined_not_clean(
        self, engine: Engine, scan_id
    ) -> None:  # type: ignore[no-untyped-def]
        """A manifest-only scan knows nothing about reachability. Reporting it as narrowed
        would turn the absence of analysis into a clean bill of health."""
        _seed(engine, title="Remove support for `eval_type_backport()`")
        outcome = _analyse(engine, scan_id, {})
        assert outcome.suppressed == 0
        assert outcome.findings[0].reachable is None

    def test_findings_are_never_stored_as_unreachable(
        self, engine: Engine, scan_id
    ) -> None:  # type: ignore[no-untyped-def]
        """A confirmed negative is suppressed, so a stored False could only be a falsehood."""
        _seed(engine, title="Remove support for `eval_type_backport()`")
        for sources in ({"app.py": USES_SYMBOL}, {"app.py": IGNORES_SYMBOL}, {}):
            outcome = _analyse(engine, scan_id, sources)
            assert all(f.reachable is not False for f in outcome.findings)


class TestCoverage:
    def test_an_absent_component_is_reported_uncovered(
        self, engine: Engine, scan_id
    ) -> None:  # type: ignore[no-untyped-def]
        """FR-39. An empty result and "we know nothing about this" are different answers, and
        the catalog is thin enough that conflating them would be actively misleading."""
        with tenant_session(engine, None) as s:
            outcome = analyse(
                s,
                scan_id=scan_id,
                # A component no other test seeds: records persist across tests in this module,
                # so reusing one would make this pass or fail on execution order.
                dependencies=[Dependency(ecosystem="pypi", name="never-catalogued", version="1.0")],
                sources={"app.py": USES_SYMBOL},
            )
        assert [d.name for d in outcome.uncovered] == ["never-catalogued"]
        assert outcome.findings == []

    def test_an_unanalysable_ecosystem_is_named(self, engine: Engine, scan_id) -> None:  # type: ignore[no-untyped-def]
        """Swift: catalogued but with no reachability analyser. An ecosystem without one must
        still be named, rather than silently reported as undetermined with no explanation.

        This test has now been retargeted twice — npm, then maven — as each gained an analyser.
        That is the test doing its job: it asserts the *reporting* of a gap, so it has to point
        at a gap that still exists."""
        _seed(
            engine,
            title="Remove support for `eval_type_backport()`",
            ref="swift-only-widget",
            ecosystem="swift",
        )
        with tenant_session(engine, None) as s:
            outcome = analyse(
                s,
                scan_id=scan_id,
                dependencies=[
                    Dependency(ecosystem="swift", name="swift-only-widget", version="2.5.0")
                ],
                sources={"app.py": USES_SYMBOL},
            )
        assert outcome.unanalysed_ecosystems == {"swift"}
        assert outcome.findings[0].reachable is None

"""The advisor: what you will run into, before you build it.

One requirement shapes the whole thing. FR-26: "no known problems" and "not enough information
to say" are different answers, and the second must never be presented as the first. A confident
empty report from a thin profile is the most damaging output this could produce — it reads as
reassurance and is actually ignorance.
"""

from __future__ import annotations

import uuid

from bugmine.advisor import Advice, StackComponent, StackProfile, advise, interactions
from bugmine.catalog import IncomingBug, write
from bugmine.db import tenant_session
from bugmine.models import BugType, RecordOrigin, SubjectDomain
from sqlalchemy.engine import Engine


def _seed(engine: Engine, ref: str, *, title: str, bug_type=BugType.BREAKING_CHANGE,
          introduced="1.0.0", description=None) -> None:  # type: ignore[no-untyped-def]
    with tenant_session(engine, None) as s:
        write(
            s,
            IncomingBug(
                subject_domain=SubjectDomain.REPO_LIBRARY,
                component_ref=ref,
                ecosystem="pypi",
                bug_type=bug_type,
                applicability={"kind": "version_range", "scheme": "generic",
                               "introduced_in": introduced, "fixed_in": None},
                title=title,
                description=description,
            ),
            origin=RecordOrigin.CRAWLED,
        )


class TestNotEnoughInformation:
    def test_an_empty_profile_says_so_rather_than_reporting_nothing(self, engine: Engine) -> None:
        """The failure that matters: an empty report reads as a clean bill of health."""
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile())
        assert advice.grounded == []
        assert advice.unknowns, "an empty profile must say what it is missing"
        assert not advice.has_enough_to_say

    def test_a_missing_version_is_named_specifically(self, engine: Engine) -> None:
        """FR-27 — the specific missing fact, not a generic request for more detail."""
        ref = f"advlib{uuid.uuid4().hex[:6]}"
        _seed(engine, ref, title="Removed the old client")
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref),)))
        assert any(ref in u and "version" in u.lower() for u in advice.unknowns)

    def test_a_scale_sensitive_record_needs_the_stated_scale(self, engine: Engine) -> None:
        """FR-31 — a bug that only bites at scale is advice only if the stated scale reaches it."""
        ref = f"advperf{uuid.uuid4().hex[:6]}"
        _seed(engine, ref, title="Slow under load", bug_type=BugType.PERFORMANCE)
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        assert any("scale" in u.lower() for u in advice.unknowns)


class TestGrounding:
    def test_every_finding_cites_a_record(self, engine: Engine) -> None:
        """FR-32. An assertion without a record is the thing that makes the rest untrustworthy."""
        ref = f"advcite{uuid.uuid4().hex[:6]}"
        _seed(engine, ref, title="Dropped Python 3.9")
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        assert advice.grounded
        assert all(f["citations"] for f in advice.grounded)

    def test_an_uncatalogued_component_is_reported_not_covered(self, engine: Engine) -> None:
        """Absence of coverage, not absence of defects."""
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(
                s, StackProfile(components=(StackComponent(ref=f"nope{uuid.uuid4().hex[:8]}"),))
            )
        assert advice.not_covered
        assert advice.grounded == []


class TestRanking:
    def test_breaking_changes_outrank_security_for_a_stack_being_chosen(
        self, engine: Engine
    ) -> None:
        """FR-33 — ranked for this stack, not by the bug's general severity. Someone choosing
        what to build can still avoid a component that will break under them."""
        ref = f"advrank{uuid.uuid4().hex[:6]}"
        _seed(engine, ref, title="Security hole", bug_type=BugType.SECURITY)
        _seed(engine, ref, title="Removed the API", bug_type=BugType.BREAKING_CHANGE)
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        types = [f["bug_type"] for f in advice.grounded]
        assert types.index(BugType.BREAKING_CHANGE) < types.index(BugType.SECURITY)


class TestInteractions:
    def test_only_interactions_the_catalog_records_are_reported(self, engine: Engine) -> None:
        """FR-30, held narrow on purpose: inferring a link the catalog does not record is the
        ungrounded assertion FR-32 forbids, and plausible-sounding output is hardest to doubt."""
        a = f"adva{uuid.uuid4().hex[:6]}"
        b = f"advb{uuid.uuid4().hex[:6]}"
        _seed(engine, a, title=f"Breaks when used with {b}", description=f"conflicts with {b}")
        _seed(engine, b, title="Unrelated change")
        profile = StackProfile(components=(
            StackComponent(ref=a, version="2.0.0"), StackComponent(ref=b, version="2.0.0"),
        ))
        with tenant_session(engine, None, commit=False) as s:
            found = interactions(s, profile)
        assert any(sorted([a, b]) == f["components"] for f in found)

    def test_a_single_component_has_no_interactions(self, engine: Engine) -> None:
        with tenant_session(engine, None, commit=False) as s:
            assert interactions(s, StackProfile(components=(StackComponent(ref="solo"),))) == []


class TestPartialAdvice:
    def test_a_known_component_still_reports_while_another_is_unknown(
        self, engine: Engine
    ) -> None:
        """FR-28 — partial advice scoped to what is known, rather than refusing wholesale."""
        known = f"advk{uuid.uuid4().hex[:6]}"
        _seed(engine, known, title="Removed a helper")
        profile = StackProfile(components=(
            StackComponent(ref=known, version="2.0.0"),
            StackComponent(ref=f"unknown{uuid.uuid4().hex[:6]}"),
        ))
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, profile)
        assert advice.grounded, "a known component must still produce advice"
        assert advice.not_covered, "and the unknown one must still be named"


def test_advice_defaults_are_empty_not_optimistic() -> None:
    """A fresh Advice must not claim to have enough to say."""
    assert Advice().grounded == []


class TestRelevanceCap:
    def test_a_component_with_many_records_is_capped(self, engine: Engine) -> None:
        """109 matches for one component is a dump, not advice. Every one is technically correct
        — a breaking change introduced in 0.9 does affect 2.0.40 — but somebody choosing 2.0.40
        starts after those changes rather than living through them."""
        from bugmine.advisor import PER_COMPONENT_LIMIT

        ref = f"advcap{uuid.uuid4().hex[:6]}"
        for i in range(PER_COMPONENT_LIMIT + 5):
            _seed(engine, ref, title=f"Removed thing number {i}", introduced=f"1.{i}.0")
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        assert len(advice.grounded) == PER_COMPONENT_LIMIT

    def test_what_was_omitted_is_reported(self, engine: Engine) -> None:
        """A truncated list that looks complete is worse than a long one."""
        from bugmine.advisor import PER_COMPONENT_LIMIT

        ref = f"advom{uuid.uuid4().hex[:6]}"
        for i in range(PER_COMPONENT_LIMIT + 3):
            _seed(engine, ref, title=f"Removed thing number {i}", introduced=f"1.{i}.0")
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        assert advice.omitted.get(ref) == 3

    def test_the_cap_is_per_component_not_overall(self, engine: Engine) -> None:
        """One noisy dependency must not crowd out every other component's findings."""
        from bugmine.advisor import PER_COMPONENT_LIMIT

        noisy = f"advnoisy{uuid.uuid4().hex[:5]}"
        quiet = f"advquiet{uuid.uuid4().hex[:5]}"
        for i in range(PER_COMPONENT_LIMIT + 6):
            _seed(engine, noisy, title=f"Removed thing {i}", introduced=f"1.{i}.0")
        _seed(engine, quiet, title="One quiet removal")
        profile = StackProfile(components=(
            StackComponent(ref=noisy, version="2.0.0"),
            StackComponent(ref=quiet, version="2.0.0"),
        ))
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, profile)
        assert any(f["component"] == quiet for f in advice.grounded)

    def test_observed_at_does_not_leak_into_the_answer(self, engine: Engine) -> None:
        """It is used for ranking only — it says when we saw the record, not when it stopped
        mattering, and presenting it would invite that reading."""
        ref = f"advleak{uuid.uuid4().hex[:6]}"
        _seed(engine, ref, title="Removed a helper")
        with tenant_session(engine, None, commit=False) as s:
            advice = advise(s, StackProfile(components=(StackComponent(ref=ref, version="2.0.0"),)))
        assert all("observed_at" not in f for f in advice.grounded)

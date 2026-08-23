"""Applicability matching.

The boundary cases here are the ones that produce *silent* wrongness: a version just outside
an affected range that matches anyway is a false positive indistinguishable from a real
finding, and an unknown version treated as "no match" is a false negative the user never sees.
"""

from datetime import UTC, datetime

import pytest
from bugmine.models import BuildRange, ModelRevision, TimeWindow, VersionRange


class TestVersionRange:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("7.0.0", True),
            ("7.1.5", True),
            ("7.2.3", True),
            ("6.9.9", False),  # predates introduced_in
            ("7.2.4", False),  # the fix
            ("8.0.0", False),  # well past the fix
        ],
    )
    def test_range_boundaries(self, version: str, expected: bool) -> None:
        r = VersionRange(introduced_in="7.0.0", fixed_in="7.2.4")
        result = r.matches(version)
        assert result.matched is expected
        assert result.confirmed is True

    def test_version_just_outside_does_not_match(self) -> None:
        """Plan verification 3d. Off-by-one here is a quiet false positive."""
        r = VersionRange(introduced_in="2.0.0", fixed_in="2.5.0")
        assert r.matches("1.9.9").matched is False
        assert r.matches("2.5.0").matched is False, "fixed_in is exclusive of the affected set"
        assert r.matches("2.4.9").matched is True

    def test_unknown_version_widens_but_is_not_confirmed(self) -> None:
        """FR-26: 'we cannot tell' must not be reported as 'nothing found'."""
        r = VersionRange(introduced_in="1.0.0", fixed_in="2.0.0")
        result = r.matches(None)
        assert result.matched is True, "unknown version must widen, not exclude"
        assert result.confirmed is False, "and must never be presented as confirmed"

    def test_unorderable_version_is_unconfirmed_not_excluded(self) -> None:
        r = VersionRange(introduced_in="1.0.0", fixed_in="2.0.0")
        result = r.matches("not-a-version")
        assert result.matched is True
        assert result.confirmed is False

    def test_open_ended_range(self) -> None:
        r = VersionRange(introduced_in="3.0.0")  # no known fix
        assert r.matches("99.0.0").matched is True

    def test_no_lower_bound(self) -> None:
        r = VersionRange(fixed_in="1.5.0")  # affected from the beginning
        assert r.matches("0.0.1").matched is True
        assert r.matches("1.5.0").matched is False


class TestBuildRange:
    def test_set_membership_not_comparison(self) -> None:
        b = BuildRange(builds=["5.15.0-89", "5.15.0-91"])
        assert b.matches("5.15.0-89").matched is True
        assert b.matches("5.15.0-90").matched is False, "partial order: 90 is not implied"
        assert b.matches(None).confirmed is False


class TestTimeWindow:
    """SaaS platforms ship no version. Applicability is a date range."""

    def test_containment(self) -> None:
        w = TimeWindow(
            observed_from=datetime(2026, 3, 1, tzinfo=UTC),
            observed_until=datetime(2026, 4, 1, tzinfo=UTC),
        )
        assert w.matches(None, at=datetime(2026, 3, 15, tzinfo=UTC)).matched is True
        assert w.matches(None, at=datetime(2026, 2, 1, tzinfo=UTC)).matched is False
        assert w.matches(None, at=datetime(2026, 5, 1, tzinfo=UTC)).matched is False

    def test_ongoing_window_has_no_end(self) -> None:
        w = TimeWindow(observed_from=datetime(2026, 1, 1, tzinfo=UTC))
        assert w.matches(None, at=datetime(2030, 1, 1, tzinfo=UTC)).matched is True

    def test_missing_time_is_unconfirmed(self) -> None:
        w = TimeWindow(observed_from=datetime(2026, 1, 1, tzinfo=UTC))
        assert w.matches(None).confirmed is False


class TestModelRevision:
    """An identifier is not enough: a floating alias repoints without changing."""

    def test_identifier_must_match(self) -> None:
        m = ModelRevision(model_id="gemini-2.0-flash")
        assert m.matches("gemini-2.0-flash").matched is True
        assert m.matches("gemini-1.5-pro").matched is False

    def test_behavior_change_is_date_bounded(self) -> None:
        m = ModelRevision(model_id="gpt-4-turbo", effective_from=datetime(2026, 6, 1, tzinfo=UTC))
        after = m.matches("gpt-4-turbo", at=datetime(2026, 7, 1, tzinfo=UTC))
        before = m.matches("gpt-4-turbo", at=datetime(2026, 5, 1, tzinfo=UTC))
        assert after.matched is True
        assert before.matched is False

    def test_date_bounded_without_a_time_is_unconfirmed(self) -> None:
        m = ModelRevision(model_id="gpt-4-turbo", effective_from=datetime(2026, 6, 1, tzinfo=UTC))
        assert m.matches("gpt-4-turbo").confirmed is False

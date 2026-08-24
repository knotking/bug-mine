"""Corroboration by failure rate.

FR-55 corroborates eval-derived candidates by n reproducing runs, and the requirements flag the
weakness themselves: LLM defects are usually probabilistic. These tests exist to pin the
consequence — a consecutive-runs rule keeps the deterministic defects, which are the easy ones
nobody needed an eval to find, and discards the probabilistic ones, which are the reason evals
exist at all.
"""

from __future__ import annotations

from bugmine.evals.rate import TOLERATED_FAILURE_RATE, runs_needed, wilson


class TestProbabilisticDefects:
    def test_a_four_percent_defect_is_corroborated(self) -> None:
        """The case that motivated the whole approach. Four failures in a hundred runs is a real
        and expensive defect; reproducing it five times consecutively has odds of about one in
        ten million, so a consecutive-runs rule would never accept it."""
        assert wilson(4, 100).corroborated

    def test_the_same_defect_would_almost_never_reproduce_consecutively(self) -> None:
        """Stated as arithmetic rather than left implicit."""
        assert 0.04**5 < 1e-6

    def test_a_deterministic_defect_is_corroborated_quickly(self) -> None:
        assert wilson(10, 10).corroborated


class TestTheLowerBoundIsWhatDecides:
    def test_a_small_sample_that_looks_bad_is_not_enough(self) -> None:
        """One failure in three is 33% observed, and the interval reaches low enough that
        reporting a defect would be a claim the data does not support."""
        rate = wilson(1, 3)
        assert rate.observed > 0.3
        assert rate.lower < 0.3

    def test_more_runs_narrow_the_interval(self) -> None:
        many, few = wilson(40, 1000), wilson(4, 100)
        assert (many.upper - many.lower) < (few.upper - few.lower)


class TestAbsenceIsNotIgnorance:
    def test_a_clean_short_run_is_undetermined_not_refuted(self) -> None:
        """Twenty clean runs do not show a 1-in-100 defect is absent — they are consistent with
        one. Calling that "refuted" is how a system starts reporting ignorance as reassurance."""
        rate = wilson(0, 20)
        assert rate.undetermined
        assert not rate.refuted

    def test_a_long_clean_run_refutes(self) -> None:
        assert wilson(0, 500).refuted

    def test_corroborated_and_refuted_are_never_both_true(self) -> None:
        for failures, runs in ((0, 10), (0, 500), (4, 100), (1, 3), (50, 50)):
            rate = wilson(failures, runs)
            assert not (rate.corroborated and rate.refuted)

    def test_no_runs_says_nothing(self) -> None:
        rate = wilson(0, 0)
        assert rate.undetermined
        assert rate.upper == 1.0


class TestWilsonOverNormal:
    def test_zero_failures_does_not_produce_a_zero_width_interval(self) -> None:
        """The normal approximation gives [0, 0] at zero failures, which would let a single
        clean run prove a defect absent. This is why evals cannot use it."""
        assert wilson(0, 20).upper > 0.0


class TestScheduling:
    def test_a_rare_defect_needs_many_runs_and_the_number_is_knowable(self) -> None:
        """So eval scheduling is a decision rather than a guess. A suite scheduled without this
        will conclude "no defect" from evidence that could never have shown one."""
        assert runs_needed(0.04) < runs_needed(0.02)
        assert runs_needed(0.02) > 100

    def test_a_defect_at_the_tolerated_rate_is_undetectable(self) -> None:
        """Honest rather than optimistic: at or below the noise floor no sample size separates
        it from noise, so the answer is zero runs, not a large number."""
        assert runs_needed(TOLERATED_FAILURE_RATE) == 0

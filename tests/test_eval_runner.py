"""Running an eval suite and turning the result into records.

This is the origin that can find a defect nobody has reported — and therefore the origin with
nothing external to check it against. What keeps it honest is that only corroborated failures
become records, and every record carries enough to re-run the question.
"""

from __future__ import annotations

import itertools

from bugmine.evals.probe import DEFAULT_SUITE, Probe
from bugmine.evals.runner import run_suite, to_records

ALWAYS_OK = Probe(name="always-ok", version="1", prompt="p", check=lambda r: True)
ALWAYS_BAD = Probe(name="always-bad", version="1", prompt="p", check=lambda r: False)
# A probe whose verdict depends on the response, so a flaky call produces a flaky rate.
EXPECTS_GOOD = Probe(name="expects-good", version="1", prompt="p", check=lambda r: r == "good")


def _flaky(fail_one_in: int):  # type: ignore[no-untyped-def]
    counter = itertools.count()
    return lambda _prompt: "bad" if next(counter) % fail_one_in == 0 else "good"


class TestDetection:
    def test_a_probabilistic_defect_is_found(self) -> None:
        """The case a consecutive-runs rule cannot see: one failure in ten."""
        result = run_suite((EXPECTS_GOOD,), target="m", call=_flaky(10), runs=60)
        assert result.rates["expects-good"].corroborated

    def test_a_clean_model_produces_no_records(self) -> None:
        result = run_suite((ALWAYS_OK,), target="m", call=lambda _p: "fine", runs=40)
        assert to_records(result, probes=(ALWAYS_OK,)) == []

    def test_a_short_clean_run_is_undetermined_not_refuted(self) -> None:
        """Forty clean runs are consistent with a 1-in-100 defect. Reporting that as "no defect"
        would turn the sample size into a claim about the model."""
        result = run_suite((ALWAYS_OK,), target="m", call=lambda _p: "fine", runs=40)
        assert result.rates["always-ok"].undetermined


class TestOnlyCorroboratedBecomesARecord:
    def test_an_undetermined_rate_writes_nothing(self) -> None:
        """The undetermined zone is narrower than it first looks, and worth stating.

        With a 1% noise floor, even one failure in twelve runs corroborates — Wilson's lower
        bound is 1.5%, which clears it, and that is right: seeing a failure at all in twelve
        runs is strong evidence the true rate is above one in a hundred. So the undetermined
        case is essentially *zero failures on a sample too small to rule a defect out*, which is
        exactly when writing a record would assert something the evidence cannot support.
        """
        result = run_suite((EXPECTS_GOOD,), target="m", call=lambda _p: "good", runs=20)
        rate = result.rates["expects-good"]
        assert rate.undetermined, "zero failures in twenty runs shows neither presence nor absence"
        assert to_records(result, probes=(EXPECTS_GOOD,)) == []

    def test_a_corroborated_rate_writes_one_record(self) -> None:
        result = run_suite((ALWAYS_BAD,), target="m", call=lambda _p: "bad", runs=40)
        records = to_records(result, probes=(ALWAYS_BAD,))
        assert len(records) == 1
        assert "100.0%" in records[0].title


class TestReproducibility:
    def test_the_record_identifies_probe_version_and_target(self) -> None:
        """FR-52. Re-running the same question against the same model is the same record rather
        than a new one, so a defect that persists does not multiply."""
        result = run_suite((ALWAYS_BAD,), target="gemini-x", call=lambda _p: "bad", runs=40)
        record = to_records(result, probes=(ALWAYS_BAD,))[0]
        assert record.identity_key == "eval:always-bad:1:gemini-x"

    def test_the_record_uses_model_revision_applicability(self) -> None:
        """A model has no versions, so a version range cannot describe it."""
        result = run_suite((ALWAYS_BAD,), target="gemini-x", call=lambda _p: "bad", runs=40)
        record = to_records(result, probes=(ALWAYS_BAD,))[0]
        assert record.applicability["kind"] == "model_revision"
        assert record.applicability["model_id"] == "gemini-x"

    def test_the_description_states_the_interval(self) -> None:
        """The interval is what the claim rests on, so it belongs in the record rather than only
        in the decision that produced it."""
        result = run_suite((ALWAYS_BAD,), target="m", call=lambda _p: "bad", runs=40)
        record = to_records(result, probes=(ALWAYS_BAD,))[0]
        assert "95% CI" in record.description


class TestTransportIsNotBehaviour:
    def test_transport_errors_do_not_count_as_failures(self) -> None:
        """A timeout is not the model being wrong. Counting them would manufacture defects out
        of a bad network — and a manufactured defect gets a citation like any other."""
        def dies(_prompt):  # type: ignore[no-untyped-def]
            raise TimeoutError("gone")

        result = run_suite((ALWAYS_OK,), target="m", call=dies, runs=20)
        assert result.outcomes[0].errors == 20
        assert result.outcomes[0].failures == 0
        assert to_records(result, probes=(ALWAYS_OK,)) == []


class TestBudget:
    def test_the_ceiling_stops_the_run_and_says_so(self) -> None:
        """A truncated suite whose result reads as complete would report a defect absent on
        evidence that was never gathered."""
        calls = itertools.count()

        def ceiling() -> None:
            if next(calls) > 5:
                raise RuntimeError("budget exhausted")

        result = run_suite(DEFAULT_SUITE, target="m", call=lambda _p: "x", runs=100,
                           budget_check=ceiling)
        assert result.stopped_early
        assert "budget exhausted" in result.stopped_early

    def test_the_budget_is_checked_between_calls(self) -> None:
        """A generation cannot be interrupted once started, so before the next call is the only
        place a ceiling is real."""
        import inspect

        from bugmine.evals import runner

        source = inspect.getsource(runner.run_suite)
        assert source.index("budget_check()") < source.index("call(probe.prompt)")

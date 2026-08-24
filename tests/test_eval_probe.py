"""Eval probes.

An eval-derived record is the only kind that asserts a defect nobody has reported, so the claim
rests entirely on somebody else running the same thing and seeing the same result. That makes
reproducibility (FR-52) the property these tests are about.
"""

from __future__ import annotations

from bugmine.evals.probe import (
    DEFAULT_SUITE,
    INSTRUCTION_ADHERENCE,
    STRUCTURED_OUTPUT,
    ProbeOutcome,
    json_has_keys,
    valid_json,
)


class TestValidJson:
    def test_plain_json_passes(self) -> None:
        assert valid_json('{"a": 1}')

    def test_malformed_json_fails(self) -> None:
        assert not valid_json('{"a": 1,}')

    def test_a_code_fence_is_a_wrapper_not_malformed_json(self) -> None:
        """Treating a fence as failure would measure our parsing rather than the model."""
        assert valid_json('```json\n{"a": 1}\n```')

    def test_prose_fails(self) -> None:
        assert not valid_json("Here is your JSON: it has one key.")


class TestRequiredKeys:
    def test_missing_keys_fail_even_though_it_parses(self) -> None:
        """The quieter failure: a caller reading result["answer"] gets a KeyError rather than a
        parse error, so it surfaces as an application bug rather than a model one."""
        check = json_has_keys("name", "count")
        assert not check('{"name": "example"}')
        assert check('{"name": "example", "count": 3}')

    def test_a_json_array_is_not_an_object(self) -> None:
        assert not json_has_keys("a")('[{"a": 1}]')


class TestOutcomeAccounting:
    def test_transport_errors_are_not_behavioural_failures(self) -> None:
        """A timeout is not the model being wrong. Pooling them would manufacture defects out of
        a bad network — and the failure rate is what decides whether a record gets written."""
        outcome = ProbeOutcome(probe="p", probe_version="1", target="m")
        outcome.record(None, ok=False)
        outcome.record('{"a":1}', ok=True)
        assert outcome.errors == 1
        assert outcome.failures == 0
        assert outcome.runs == 2

    def test_failing_samples_are_kept_but_capped(self) -> None:
        """Evidence for the record, without carrying thousands of copies of one wrongness."""
        outcome = ProbeOutcome(probe="p", probe_version="1", target="m")
        for i in range(10):
            outcome.record(f"bad {i}", ok=False)
        assert outcome.failures == 10
        assert len(outcome.samples) == ProbeOutcome.MAX_SAMPLES


class TestReproducibility:
    def test_every_probe_carries_a_version(self) -> None:
        """Results from different probe versions answer different questions and must never be
        pooled — otherwise a change in what we asked looks like a change in the model."""
        assert all(p.version for p in DEFAULT_SUITE)

    def test_an_outcome_records_what_it_asked_and_of_what(self) -> None:
        outcome = ProbeOutcome(probe=STRUCTURED_OUTPUT.name, probe_version="1", target="m")
        assert outcome.probe and outcome.probe_version and outcome.target

    def test_probes_check_deterministically(self) -> None:
        """A check that itself called a model would make failures unattributable between the
        two."""
        for _ in range(5):
            assert STRUCTURED_OUTPUT.check('{"name": "example", "count": 3}')
            assert INSTRUCTION_ADHERENCE.check("ok")

"""Eval intake: what evidence establishes, and the ways it must not be allowed to.

An eval-derived record is the only kind that asserts a defect nobody has published. There is no
vendor advisory to check it against, so everything downstream rests on this module refusing to
promote evidence that does not support the claim. These tests are mostly about that refusal.
"""

from __future__ import annotations

import uuid

import pytest
from bugmine.db import tenant_session
from bugmine.evals import intake
from bugmine.evals.intake import Submission
from bugmine.models import (
    BugRecord,
    EvalObservation,
    LifecycleState,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)
from sqlalchemy import Engine, select


def _sub(**kw) -> Submission:
    base = dict(
        run_id=f"run-{uuid.uuid4().hex[:10]}",
        subject_domain=SubjectDomain.LLM_MODEL,
        component_ref=f"model-{uuid.uuid4().hex[:6]}",
        probe="structured-output",
        probe_version="1",
        runs=40,
        failures=0,
    )
    base.update(kw)
    return Submission(**base)  # type: ignore[arg-type]


class TestWhatEvidenceEstablishes:
    def test_one_clean_run_establishes_nothing(self, engine: Engine) -> None:
        """Forty clean runs is not proof of absence. The interval still reaches past 1%."""
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=40, failures=0))
        assert v.state == "undetermined"
        assert v.record_id is None

    def test_enough_clean_runs_refute(self, engine: Engine) -> None:
        """At 500 runs the upper bound finally drops below the tolerated rate.

        The pair with the test above is the point: the same observation — no failures — supports
        a different conclusion at a different sample size, and a system that cannot tell those
        apart reports ignorance as reassurance.
        """
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=500, failures=0))
        assert v.state == "refuted"

    def test_a_small_bad_sample_is_judged_on_its_lower_bound(self, engine: Engine) -> None:
        """3 of 10 is 30% observed, but the interval is [10.8%, 60.3%].

        10.8% still clears a 1% tolerated floor, so this does corroborate — but what it
        corroborates is "worse than 1%", not "30%". That is the whole point of deciding on the
        lower bound: the catalog states the claim the evidence supports, not the more dramatic
        one the point estimate suggests.
        """
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=10, failures=3))
        assert v.state == "corroborated"
        assert v.rate.lower > 0.01
        assert v.rate.lower < 0.15, "the lower bound should be far below the 30% observed"

    def test_a_small_sample_leaves_a_uselessly_wide_interval(self) -> None:
        """The same 3-of-10 evidence bounds the truth between 10.8% and 60.3%.

        Pure-function check, no database: it isolates the decision rule from everything else.
        A rate reported without that width attached would be a fabricated precision.
        """
        from bugmine.evals.rate import wilson

        rate = wilson(3, 10)
        assert rate.observed == 0.3
        # The interval spans 10.8% to 60.3% — a range so wide it is barely a measurement.
        assert rate.lower < 0.15, "a 3-of-10 observation cannot establish worse-than-15%"
        assert rate.upper > 0.5, "and it cannot rule out something far worse either"

    def test_a_realistic_intermittent_defect_corroborates(self, engine: Engine) -> None:
        """4% failure over 100 runs — the case a consecutive-runs rule rejects outright."""
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=100, failures=4))
        assert v.state == "corroborated"
        assert v.record_id is not None

    def test_transport_errors_are_excluded_from_the_rate(self, engine: Engine) -> None:
        """A timeout is not the target being wrong. Counting it as a pass understates the
        defect; counting it as a failure manufactures one."""
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=100, failures=4, errors=50))
        assert v.behavioural_runs == 50
        assert v.rate.observed == pytest.approx(4 / 50)


class TestPoolingBoundaries:
    def test_runs_accumulate_across_submissions(self, engine: Engine) -> None:
        """The reason submission and promotion are separate steps at all."""
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            first = intake.submit(s, _sub(component_ref=ref, runs=20, failures=1))
            assert first.state == "undetermined"
            second = intake.submit(s, _sub(component_ref=ref, runs=200, failures=12))
        assert second.pooled_runs == 220
        assert second.pooled_failures == 13
        assert second.state == "corroborated"

    def test_a_new_probe_version_does_not_pool_with_the_old(self, engine: Engine) -> None:
        """A probe whose prompt changed is asking a different question.

        Pooling across the change would let a change in what we asked look like a change in how
        the target behaved — and that record would be indistinguishable from a real regression.
        """
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            intake.submit(s, _sub(component_ref=ref, probe_version="1", runs=200, failures=20))
            v = intake.submit(s, _sub(component_ref=ref, probe_version="2", runs=20, failures=0))
        assert v.pooled_runs == 20, "results from two probe versions were pooled"

    def test_a_different_target_revision_does_not_pool(self, engine: Engine) -> None:
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            intake.submit(s, _sub(component_ref=ref, target_revision="a", runs=200, failures=20))
            v = intake.submit(s, _sub(component_ref=ref, target_revision="b", runs=20, failures=1))
        assert v.pooled_runs == 20

    def test_an_unpinned_revision_pools_with_itself_only(self, engine: Engine) -> None:
        """None is a real value in the key, not a wildcard that matches every revision."""
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            intake.submit(s, _sub(component_ref=ref, target_revision=None, runs=50, failures=5))
            pinned = intake.submit(
                s, _sub(component_ref=ref, target_revision="2026-08", runs=10, failures=0)
            )
        assert pinned.pooled_runs == 10


class TestIdempotency:
    def test_resubmitting_a_run_does_not_double_count(self, engine: Engine) -> None:
        """A runner retrying after a timeout must not manufacture evidence.

        Without this, a retry storm turns one bad afternoon into corroboration, and the record
        it produces looks exactly like a real finding.
        """
        sub = _sub(runs=100, failures=4)
        with tenant_session(engine, None) as s:
            first = intake.submit(s, sub)
            repeat = intake.submit(s, sub)
        assert first.accepted and not repeat.accepted
        assert repeat.pooled_runs == first.pooled_runs == 100

    def test_a_retry_gets_the_same_answer_not_an_error(self, engine: Engine) -> None:
        """The correct response to a retry is the answer again. An error would push the runner
        into retrying harder, which is the opposite of what should happen."""
        sub = _sub(runs=100, failures=4)
        with tenant_session(engine, None) as s:
            first = intake.submit(s, sub)
            repeat = intake.submit(s, sub)
        assert repeat.state == first.state
        assert repeat.record_id == first.record_id


class TestPromotionAndRetraction:
    def test_a_promoted_record_is_marked_eval_derived(self, engine: Engine) -> None:
        """FR-40. A reader weighing an eval-derived claim needs to know that is what it is —
        it rests on a probe we ran, not on a vendor saying so."""
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(component_ref=ref, runs=100, failures=4))
            rec = s.get(BugRecord, v.record_id)
            assert rec is not None
            assert rec.origin is RecordOrigin.EVAL_DERIVED

    def test_more_evidence_does_not_mint_a_second_record(self, engine: Engine) -> None:
        """Identity comes from the pooling key, not the title.

        The title carries the observed percentage, which moves with every submission — identity
        keyed on it would mint a fresh record per run, which is the exact failure the catalog's
        dedup exists to prevent, arriving through a different door.
        """
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            a = intake.submit(s, _sub(component_ref=ref, runs=100, failures=6))
            b = intake.submit(s, _sub(component_ref=ref, runs=100, failures=3))
            assert a.record_id == b.record_id
            n = len(
                s.execute(
                    select(BugRecord).where(BugRecord.identity_key.like(f"eval:{ref}:%"))
                ).scalars().all()
            )
        assert n == 1

    def test_accumulating_clean_evidence_retracts_a_promoted_record(self, engine: Engine) -> None:
        """The half that is easy to leave out.

        A record whose evidence has evaporated goes on grounding findings, wearing a citation
        that says it should be believed. Promotion without retraction is a ratchet.
        """
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            promoted = intake.submit(s, _sub(component_ref=ref, runs=100, failures=4))
            assert promoted.state == "corroborated"
            later = intake.submit(s, _sub(component_ref=ref, runs=20_000, failures=4))
            assert later.state == "refuted"
            assert later.retracted
            rec = s.get(BugRecord, later.record_id)
            assert rec is not None
            assert rec.lifecycle_state is LifecycleState.RETRACTED

    def test_undetermined_neither_writes_nor_retracts(self, engine: Engine) -> None:
        """Both actions would assert something the evidence does not support — one a defect,
        the other its absence."""
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(component_ref=ref, runs=30, failures=0))
            assert v.state == "undetermined"
            assert not v.retracted
            found = s.execute(
                select(BugRecord).where(BugRecord.identity_key.like(f"eval:{ref}:%"))
            ).scalars().all()
        assert found == []


class TestApplicability:
    def test_a_model_gets_a_model_revision_not_a_version_range(self, engine: Engine) -> None:
        """A hosted model has no versions, which is the case the tagged union exists for."""
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=100, failures=4, target_revision="2026-08-01"))
            rec = s.get(BugRecord, v.record_id)
            assert rec is not None
            assert rec.applicability["kind"] == "model_revision"

    def test_versioned_software_gets_a_version_range(self, engine: Engine) -> None:
        """Evals are not an LLM-only mechanism. Anything whose behaviour can be exercised can
        be probed, and for versioned software a version range does apply."""
        with tenant_session(engine, None) as s:
            v = intake.submit(
                s,
                _sub(
                    subject_domain=SubjectDomain.DATABASE,
                    component_ref=f"db-{uuid.uuid4().hex[:6]}",
                    target_revision="16.2",
                    runs=100,
                    failures=4,
                ),
            )
            rec = s.get(BugRecord, v.record_id)
            assert rec is not None
            assert rec.applicability["kind"] == "version_range"
            assert rec.applicability["introduced_in"] == "16.2"

    def test_unversioned_non_model_software_gets_a_time_window(self, engine: Engine) -> None:
        """What we actually know is that the behaviour was there when we looked. Inventing a
        range we never established would be a fabricated citation."""
        with tenant_session(engine, None) as s:
            v = intake.submit(
                s,
                _sub(
                    subject_domain=SubjectDomain.SAAS_PLATFORM,
                    component_ref=f"saas-{uuid.uuid4().hex[:6]}",
                    target_revision=None,
                    runs=100,
                    failures=4,
                ),
            )
            rec = s.get(BugRecord, v.record_id)
            assert rec is not None
            assert rec.applicability["kind"] == "time_window"


class TestRejectedSubmissions:
    @pytest.mark.parametrize(
        "kw",
        [
            {"runs": 0},
            {"failures": -1},
            {"runs": 10, "failures": 11},
            {"runs": 10, "failures": 6, "errors": 6},
        ],
    )
    def test_incoherent_counts_are_refused(self, engine: Engine, kw: dict) -> None:
        """Refused rather than clamped. A submission whose numbers cannot describe a real run
        is a broken runner, and quietly repairing it hides that from whoever must fix it."""
        with tenant_session(engine, None) as s, pytest.raises(ValueError):
            intake.submit(s, _sub(**kw))

    def test_tenant_scope_requires_a_tenant(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s, pytest.raises(ValueError):
            intake.submit(s, _sub(), privacy_scope=PrivacyScope.TENANT, tenant_id=None)


class TestSamples:
    def test_samples_are_capped(self, engine: Engine) -> None:
        """A probe run a thousand times would otherwise carry a thousand copies of the same
        wrongness, and the evidence is not improved by the repetition."""
        with tenant_session(engine, None) as s:
            intake.submit(s, _sub(samples=tuple(f"bad-{i}" for i in range(50)), runs=10))
            from bugmine.models import EvalObservation

            row = s.execute(
                select(EvalObservation).order_by(EvalObservation.submitted_at.desc())
            ).scalars().first()
            assert row is not None
            assert len(row.samples) == intake.MAX_SAMPLES


class TestVerdictWithoutSubmitting:
    def test_asking_does_not_add_evidence(self, engine: Engine) -> None:
        """A runner should be able to ask whether more runs are worth paying for without the
        asking changing the answer."""
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, None) as s:
            intake.submit(s, _sub(component_ref=ref, runs=40, failures=1))
            key = _sub(component_ref=ref, runs=1, failures=0)
            before = intake.verdict_for(s, key)
            after = intake.verdict_for(s, key)
        assert before.pooled_runs == after.pooled_runs == 40

    def test_runs_to_decide_guides_scheduling(self, engine: Engine) -> None:
        """A suite that stops at forty and reports nothing found has, for a 1-in-100 defect,
        gathered evidence that could never have shown one — and called that reassurance."""
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=40, failures=1))
        assert v.state == "undetermined"
        assert v.runs_to_decide > 40

    def test_a_clean_undetermined_run_is_not_told_it_is_finished(self, engine: Engine) -> None:
        """Found against production, not in this suite.

        40 clean runs came back `undetermined` with `runs_to_decide: 0` — a contradiction that
        reads as "stop, you are done" to the exact caller this field exists to warn. With no
        failures observed there is no rate to project from, so the honest number is how many
        clean runs it would take to refute: 452, not 0.
        """
        with tenant_session(engine, None) as s:
            v = intake.submit(s, _sub(runs=40, failures=0))
        assert v.state == "undetermined"
        assert v.runs_to_decide > 40, "an undetermined verdict must never report 0 runs to go"

    def test_a_decided_verdict_reports_nothing_left_to_run(self, engine: Engine) -> None:
        """0 means decided, in both directions. That is the only meaning it may carry."""
        with tenant_session(engine, None) as s:
            refuted = intake.submit(s, _sub(runs=600, failures=0))
            corroborated = intake.submit(s, _sub(runs=100, failures=4))
        assert refuted.state == "refuted" and refuted.runs_to_decide == 0
        assert corroborated.state == "corroborated" and corroborated.runs_to_decide == 0


class TestTheDatabaseIsWhatEnforcesIsolation:
    """Where the tenant boundary for eval evidence actually lives.

    Removing the scope and tenant filters from the pooling query changes nothing observable —
    I checked. That is not because they are wrong; it is because row-level security has already
    made the rows invisible. Which means these tests, not those filters, are what stands between
    one customer's probe results and another's.
    """

    def test_eval_observation_has_row_level_security(self, engine: Engine) -> None:
        """FORCE, not merely ENABLE. Without FORCE the table owner bypasses every policy, and
        the application role is the owner in more deployments than people expect."""
        from sqlalchemy import text

        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'eval_observation'"
                )
            ).one()
        assert row[0] is True, "row-level security is not enabled on eval_observation"
        assert row[1] is True, "row-level security is not FORCEd on eval_observation"

    def test_one_tenants_evidence_is_invisible_to_another(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        tenant, other = two_tenants
        ref = f"model-{uuid.uuid4().hex[:6]}"
        sub = _sub(component_ref=ref, runs=100, failures=4)
        with tenant_session(engine, tenant) as s:
            intake.submit(s, sub, privacy_scope=PrivacyScope.TENANT, tenant_id=tenant)
        with tenant_session(engine, other, commit=False) as s:
            rows = s.execute(
                select(EvalObservation).where(EvalObservation.component_ref == ref)
            ).scalars().all()
        assert rows == [], "another tenant could read private eval evidence"

    def test_tenant_evidence_is_invisible_to_a_system_session(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The sweep and every operator job run with no tenant context. If tenant evidence were
        visible there it would pool into the shared catalog, which is the leak that matters
        most — it would be published rather than merely exposed."""
        tenant, _ = two_tenants
        ref = f"model-{uuid.uuid4().hex[:6]}"
        with tenant_session(engine, tenant) as s:
            intake.submit(
                s,
                _sub(component_ref=ref, runs=100, failures=4),
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=tenant,
            )
        with tenant_session(engine, None, commit=False) as s:
            rows = s.execute(
                select(EvalObservation).where(EvalObservation.component_ref == ref)
            ).scalars().all()
        assert rows == []

"""Running an eval suite against a model, and turning the result into catalog records.

This is the origin that can find a defect nobody has reported. For LLM models it is the only
one: no vendor publishes a defect tracker, and behaviour moves under a stable identifier with no
changelog to crawl (FR-53). A scheduled eval that starts failing is the only artifact that
records the change.

Two things here that are easy to get wrong and expensive to get wrong quietly:

**Applicability.** A model has no versions, so a version range cannot describe it. The record
uses a ModelRevision applicability keyed on the identifier the provider serves — which is
exactly the case the tagged-union applicability model exists for.

**Cost.** Detecting a 1-in-50 defect takes hundreds of calls, so an eval suite is the most
expensive thing in this system by a wide margin. Every run is metered against a ceiling, checked
between calls rather than after the loop, because by the end the money is spent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bugmine.catalog import IncomingBug
from bugmine.evals.probe import Probe, ProbeOutcome
from bugmine.evals.rate import FailureRate, wilson
from bugmine.models import BugType, SubjectDomain

logger = logging.getLogger("bugmine.evals")

DEFAULT_RUNS = 40
"""Enough to corroborate a defect around 5% or worse. Rarer ones need `runs_needed`, and the
suite should say so rather than run 40 and conclude nothing is wrong."""


@dataclass
class SuiteResult:
    target: str
    outcomes: list[ProbeOutcome] = field(default_factory=list)
    rates: dict[str, FailureRate] = field(default_factory=dict)
    stopped_early: str | None = None
    """Why the run ended before completing, if it did. A truncated suite whose result reads as
    complete would report a defect absent on evidence that was never gathered."""


def run_suite(
    probes: tuple[Probe, ...],
    *,
    target: str,
    call,  # type: ignore[no-untyped-def]
    runs: int = DEFAULT_RUNS,
    budget_check=None,  # type: ignore[no-untyped-def]
) -> SuiteResult:
    """Run each probe `runs` times against `target`.

    `call` takes a prompt and returns the response text, or None on a transport failure.
    Injected rather than imported so the suite is testable without a provider — and so the same
    runner can evaluate any target.

    `budget_check` is called between calls and may raise to stop the run. Between rather than
    after: a generation cannot be interrupted once started, so the only place a ceiling is real
    is before the next call.
    """
    result = SuiteResult(target=target)

    for probe in probes:
        outcome = ProbeOutcome(probe=probe.name, probe_version=probe.version, target=target)
        for _ in range(runs):
            if budget_check is not None:
                try:
                    budget_check()
                except Exception as exc:
                    result.stopped_early = f"{type(exc).__name__}: {exc}"[:200]
                    break
            try:
                response = call(probe.prompt)
            except Exception:
                logger.exception("probe %s failed in transport against %s", probe.name, target)
                outcome.record(None, ok=False)
                continue
            outcome.record(response, ok=bool(response is not None and probe.check(response)))

        result.outcomes.append(outcome)
        # Behavioural failures over behavioural runs. Transport errors are excluded from both:
        # a timeout says nothing about the model, and counting it either way is a lie in one
        # direction or the other.
        behavioural = outcome.runs - outcome.errors
        result.rates[probe.name] = wilson(outcome.failures, behavioural)
        if result.stopped_early:
            break

    return result


def to_records(
    result: SuiteResult,
    *,
    probes: tuple[Probe, ...],
    now: datetime | None = None,
) -> list[IncomingBug]:
    """Turn corroborated failures into catalog records.

    Only corroborated ones. An undetermined rate is not a defect we can assert, and a refuted
    one is evidence of absence — writing either would put a claim in the catalog that the
    evidence does not support, wearing a citation (ADR-0002) that says it does.
    """
    at = now or datetime.now(UTC)
    by_name = {p.name: p for p in probes}
    records: list[IncomingBug] = []

    for outcome in result.outcomes:
        rate = result.rates.get(outcome.probe)
        if rate is None or not rate.corroborated:
            continue
        probe = by_name.get(outcome.probe)

        percent = f"{rate.observed * 100:.1f}%"
        interval = f"{rate.lower * 100:.1f} to {rate.upper * 100:.1f}%"
        records.append(
            IncomingBug(
                subject_domain=SubjectDomain.LLM_MODEL,
                component_ref=result.target,
                ecosystem=None,
                # Functional rather than a category of its own: the model does the wrong thing.
                bug_type=BugType.FUNCTIONAL,
                applicability={
                    # A model has no versions, so a version range cannot describe it. This is
                    # the case the tagged union exists for.
                    "kind": "model_revision",
                    "model_id": result.target,
                    # When we first observed it, not when it started. A floating alias repoints
                    # silently, so the honest bound is the run rather than a guess at the cause.
                    "effective_from": at.isoformat(),
                },
                title=f"{outcome.probe} fails {percent} of the time",
                description=(
                    f"{probe.description if probe else ''}\n\n"
                    f"Observed {outcome.failures} failures in {outcome.runs - outcome.errors} "
                    f"behavioural runs ({percent}, 95% CI {interval}). "
                    f"Probe {outcome.probe} version {outcome.probe_version}, "
                    f"target {result.target}"
                    + (f" revision {outcome.target_revision}" if outcome.target_revision else "")
                    + ".\n\nThe interval is what the claim rests on: the lower bound clears the "
                    "rate we treat as noise, so this is a defect rather than a bad night.\n\n"
                    + ("Failing responses:\n" + "\n".join(f"- {s}" for s in outcome.samples)
                       if outcome.samples else "")
                ).strip(),
                # Reproducible by construction (FR-52): the probe, its version and the target are
                # all in the identity, so re-running the same question against the same model is
                # the same record rather than a new one.
                identity_key=f"eval:{outcome.probe}:{outcome.probe_version}:{result.target}"[:280],
            )
        )
    return records

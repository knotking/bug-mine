"""Corroboration by failure rate, not by consecutive reproduction.

FR-55 says eval-derived candidates are corroborated by *n* reproducing runs. The requirements
document flags the weakness itself, and it is worth solving rather than inheriting: **LLM
defects are usually probabilistic.** A model that emits malformed structured output 4% of the
time will essentially never reproduce five times in a row, so a consecutive-runs rule rejects a
real and expensive defect while accepting only the deterministic ones — which are the easy ones
nobody needed an eval to find.

So corroboration here is a *rate with a confidence interval*, and the decision rule is:

    the lower bound of the interval exceeds the rate we are willing to call normal

Using the lower bound rather than the observed rate is the whole point. Three failures in ten
runs is 30% observed, but the interval is wide enough to include 7% — reporting "30% failure"
on that evidence would be a claim the data does not support. As runs accumulate the interval
narrows and the lower bound rises toward the truth, so a real defect becomes reportable and a
fluke never does.

Wilson score interval rather than the normal approximation, because the normal one is badly
wrong exactly where evals live: small samples and proportions near zero. At 0/20 it produces an
interval of zero width, which would let a single clean run "prove" a defect absent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 95%. Two-sided z for 0.975.
Z_95 = 1.959963984540054

TOLERATED_FAILURE_RATE = 0.01
"""Below this a failure reads as noise rather than a defect.

Not zero: every provider has a tail of timeouts and transient errors, and a rule with no
tolerance would file a record against every model on its first bad night.
"""


@dataclass(frozen=True)
class FailureRate:
    runs: int
    failures: int
    lower: float
    """Lower bound of the confidence interval. The number decisions are made on."""
    upper: float
    observed: float

    @property
    def corroborated(self) -> bool:
        """Whether the evidence supports reporting a defect.

        The lower bound clearing the tolerated rate, not the observed rate — so a small sample
        that happens to look bad does not become a record.
        """
        return self.lower > TOLERATED_FAILURE_RATE

    @property
    def refuted(self) -> bool:
        """Whether the evidence supports saying the defect is *not* there.

        Deliberately separate from `not corroborated`. "We have not shown it" and "we have shown
        it is absent" are different claims, and the gap between them is where a system quietly
        starts reporting ignorance as reassurance.
        """
        return self.upper < TOLERATED_FAILURE_RATE

    @property
    def undetermined(self) -> bool:
        return not self.corroborated and not self.refuted


def wilson(failures: int, runs: int, *, z: float = Z_95) -> FailureRate:
    """Wilson score interval for a failure proportion.

    Chosen over the normal approximation because evals live exactly where that one breaks:
    small samples and proportions near zero. At 0 failures in 20 runs the normal approximation
    gives an interval of zero width, which would let one clean run "prove" a defect absent.
    """
    if runs <= 0:
        return FailureRate(runs=0, failures=0, lower=0.0, upper=1.0, observed=0.0)

    observed = failures / runs
    denominator = 1 + z**2 / runs
    centre = (observed + z**2 / (2 * runs)) / denominator
    margin = (
        z / denominator * math.sqrt(observed * (1 - observed) / runs + z**2 / (4 * runs**2))
    )
    return FailureRate(
        runs=runs,
        failures=failures,
        lower=max(0.0, centre - margin),
        upper=min(1.0, centre + margin),
        observed=observed,
    )


def runs_needed(target_rate: float, *, z: float = Z_95) -> int:
    """Roughly how many runs before a defect at `target_rate` becomes reportable.

    Exists so eval scheduling is a decision rather than a guess: detecting a 1-in-100 failure is
    not a handful of runs, and a suite scheduled without knowing that will conclude "no defect"
    from evidence that could never have shown one.
    """
    if target_rate <= TOLERATED_FAILURE_RATE:
        return 0  # Indistinguishable from tolerated noise at any sample size.
    runs = 10
    while runs < 100_000:
        # The optimistic case: failures arrive exactly at the target rate.
        if wilson(round(target_rate * runs), runs, z=z).corroborated:
            return runs
        runs = int(runs * 1.5) + 1
    return runs

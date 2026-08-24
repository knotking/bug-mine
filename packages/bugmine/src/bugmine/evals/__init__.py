"""Evals — the origin that finds defects nobody has reported.

For LLM models this is the *only* viable origin: no vendor publishes a defect tracker, and
behaviour moves under a stable identifier with no changelog to crawl. A scheduled eval that
starts failing is the only artifact that records the change.
"""

from bugmine.evals.probe import DEFAULT_SUITE, Probe, ProbeOutcome, valid_json
from bugmine.evals.rate import FailureRate, runs_needed, wilson

__all__ = [
    "DEFAULT_SUITE",
    "FailureRate",
    "Probe",
    "ProbeOutcome",
    "runs_needed",
    "valid_json",
    "wilson",
]

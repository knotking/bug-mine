"""Eval probes — reproducible checks against a target.

FR-52 requires every eval-derived record to be reproducible: it carries the eval, the eval's
version, the target revision, and what is needed to run it again. That is not bookkeeping. An
eval-derived record is the only kind that asserts a defect nobody has reported, so the claim
rests entirely on somebody else being able to run the same thing and see the same result.

The probe's *version* matters as much as the target's. If a probe changes, its old results
describe a different question — and silently pooling them with new ones would let a change in
what we asked look like a change in how the model behaved. FR-54 exists for the same reason:
behaviour can move with no version change on the target at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Probe:
    """One question, asked the same way every time."""

    name: str
    version: str
    """Bumped whenever the prompt or the check changes. Results from different probe versions
    are never pooled: they answer different questions."""
    prompt: str
    check: Callable[[str], bool]
    """Returns True when the response is acceptable. Deterministic and cheap — a check that
    itself calls a model would make failures unattributable between the two."""
    description: str = ""


@dataclass
class ProbeOutcome:
    probe: str
    probe_version: str
    target: str
    """The model identifier as the provider names it, e.g. gemini-3.7-flash."""
    target_revision: str | None = None
    """What the provider says it served, when it says anything. Usually nothing, which is the
    entire reason this subject needs evals rather than a changelog."""
    runs: int = 0
    failures: int = 0
    errors: int = 0
    """Transport failures. Counted apart from behavioural failures because a timeout is not the
    model being wrong, and pooling them would manufacture defects out of a bad network."""
    samples: list[str] = field(default_factory=list)
    """A few failing responses, kept for the record's evidence. Capped, since a probe run
    thousands of times would otherwise carry thousands of copies of the same wrongness."""

    MAX_SAMPLES = 3

    def record(self, response: str | None, ok: bool) -> None:
        self.runs += 1
        if response is None:
            self.errors += 1
            return
        if not ok:
            self.failures += 1
            if len(self.samples) < self.MAX_SAMPLES:
                self.samples.append(response[:500])


def valid_json(response: str) -> bool:
    """The most load-bearing probe there is.

    Structured output is what applications parse, and a model that returns malformed JSON some
    small percentage of the time breaks callers intermittently in a way no version number
    records. This is the exact shape the requirements cite: measurable, probabilistic, and
    invisible to every versioning mechanism.
    """
    text = response.strip()
    if text.startswith("```"):
        # Fenced output is a wrapper, not malformed JSON, and treating it as a failure would
        # measure our parsing rather than the model's.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.removeprefix("json").strip()
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def json_has_keys(*keys: str) -> Callable[[str], bool]:
    """Structured output that parses but omits requested fields — the quieter failure.

    A caller that reads `result["answer"]` gets a KeyError rather than a parse error, so this
    one surfaces as an application bug rather than a model one.
    """

    def check(response: str) -> bool:
        if not valid_json(response):
            return False
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].removeprefix("json").strip()
        try:
            payload: Any = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(payload, dict) and all(k in payload for k in keys)

    return check


STRUCTURED_OUTPUT = Probe(
    name="structured-output-valid-json",
    version="1",
    prompt=(
        "Return only a JSON object with the keys \"name\" and \"count\". "
        "name is the string \"example\" and count is the number 3. No prose, no code fence."
    ),
    check=json_has_keys("name", "count"),
    description="Whether the model returns parseable JSON with the requested keys.",
)

INSTRUCTION_ADHERENCE = Probe(
    name="instruction-adherence-single-word",
    version="1",
    prompt='Reply with exactly one word: "ok". No punctuation, no explanation.',
    check=lambda r: r.strip().strip(".").lower() == "ok",
    description="Whether the model follows a trivial output constraint.",
)

DEFAULT_SUITE: tuple[Probe, ...] = (STRUCTURED_OUTPUT, INSTRUCTION_ADHERENCE)

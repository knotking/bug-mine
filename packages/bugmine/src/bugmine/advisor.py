"""The advisor — what you will run into, before you build it.

The scanner answers *what is wrong with the code I have*. The advisor answers *what will go
wrong if I build what I am describing*, from a stack the user states rather than a repository
they already wrote.

The requirement that shapes everything here is FR-26: **"no known problems" and "not enough
information to say" are different answers, and the second must never be presented as the
first.** A confident empty report from a thin profile is the single most damaging thing this
could produce, because it reads as reassurance and was actually ignorance.

So the advisor reports three things separately and never merges them:

- **grounded** — findings from catalog records, each cited (FR-32)
- **unknowns** — the specific facts it lacks, named individually so they can be answered (FR-27)
- **not covered** — components it has no records for at all, said out loud (FR-39)

Non-goals from the requirements, held to deliberately: it does not propose fixes, and it does
not judge the design on general merit. Both would dilute the grounding that makes the rest
trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from bugmine.catalog import Query, retrieve
from bugmine.models import BugType, Component, SubjectDomain

# A bug that only bites at scale is advice only if the stated scale reaches it (FR-31).
SCALE_SENSITIVE = frozenset({BugType.PERFORMANCE})


@dataclass(frozen=True)
class StackComponent:
    """One named thing in the user's stack. Version is optional and its absence is a fact."""

    ref: str
    version: str | None = None
    ecosystem: str | None = None
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY


@dataclass(frozen=True)
class StackProfile:
    """FR-25 — one representation everything downstream operates on."""

    components: tuple[StackComponent, ...] = ()
    intention: str | None = None
    expected_scale: str | None = None
    """Free text, e.g. "about 50 requests a second". Absent means scale-sensitive findings are
    reported as unconfirmed rather than dropped or asserted."""


PER_COMPONENT_LIMIT = 8
"""Most relevant records per component.

Asked about sqlalchemy 2.0.40 the catalog returns 109 matches, and every one is technically
correct: a breaking change introduced in 0.9 with no fixed version does affect 2.0.40. But
somebody *choosing* 2.0.40 starts after those changes rather than living through them, so
history reads as noise and buries the handful that matter. The cap is reported, never silent —
a truncated list that looks complete is worse than a long one.
"""


@dataclass
class Advice:
    grounded: list[dict] = field(default_factory=list)
    """Findings backed by catalog records. Each carries its citations."""
    unknowns: list[str] = field(default_factory=list)
    """Specific missing facts (FR-27) — not a generic request for more detail."""
    not_covered: list[str] = field(default_factory=list)
    """Components with no catalog records at all. Absence of coverage, not absence of defects."""
    omitted: dict[str, int] = field(default_factory=dict)
    """Records held back per component by the cap. Surfaced so a short list cannot be mistaken
    for a complete one."""

    @property
    def has_enough_to_say(self) -> bool:
        """Whether a report here is worth anything.

        False means the honest answer is "not enough information", which FR-26 requires be
        distinguishable from "nothing found".
        """
        return bool(self.grounded) or not self.unknowns


def _recency(finding: dict) -> float:
    """Newer records first within a type.

    A defect recorded last month is more likely to still be live than one from a release series
    nobody runs. This is a proxy for relevance, not a measure of it — the record's own dates say
    when we saw it, not when it stopped mattering.
    """
    observed = finding.get("observed_at")
    return -observed.timestamp() if observed else 0.0


def _rank(finding: dict) -> tuple[int, int, float, str]:
    """Rank for *this* stack, not by the bug's general severity (FR-33).

    A confirmed version match outranks an unconfirmed one, because the unconfirmed one may not
    apply at all. Breaking changes and deprecations outrank security here — not because they
    matter more in the abstract, but because the user is choosing what to build, and a component
    that will break under them is a decision they can still avoid.
    """
    order = {
        BugType.BREAKING_CHANGE: 0,
        BugType.DEPRECATION: 1,
        BugType.SECURITY: 2,
        BugType.FUNCTIONAL: 3,
        BugType.PERFORMANCE: 4,
        BugType.COMPATIBILITY: 5,
        BugType.BUILD: 6,
    }
    confirmed = 0 if finding.get("version_confirmed") else 1
    return (
        confirmed,
        order.get(finding.get("bug_type"), 9),
        _recency(finding),
        finding.get("component", ""),
    )


def advise(
    session: Session,
    profile: StackProfile,
    *,
    now: datetime | None = None,
) -> Advice:
    """Produce advice for a stated stack.

    Partial advice where the profile supports it (FR-28): a component with no version still gets
    its records, marked unconfirmed, rather than the whole request being refused because one
    field is missing.
    """
    at = now or datetime.now(UTC)
    advice = Advice()

    if not profile.components:
        advice.unknowns.append(
            "No components named. Advice needs at least one library, database, runtime or "
            "service to speak about — the advisor reports from catalog records, not from "
            "general judgement."
        )
        return advice

    for component in profile.components:
        if component.version is None:
            # Named individually so it can be answered, rather than "please provide more detail".
            advice.unknowns.append(
                f"No version for {component.ref}. Records that apply to some versions and not "
                f"others cannot be confirmed without it."
            )

        matches, missing = retrieve(
            session,
            Query(
                component.subject_domain,
                component.ref,
                version=component.version,
                ecosystem=component.ecosystem,
            ),
            at=at,
        )
        if missing is not None:
            advice.not_covered.append(component.ref)
            continue

        for match in matches:
            if match.record.bug_type in SCALE_SENSITIVE and not profile.expected_scale:
                advice.unknowns.append(
                    f"No expected scale given, so a performance record for {component.ref} "
                    f"cannot be judged relevant."
                )

            advice.grounded.append(
                {
                    "component": component.ref,
                    "bug_type": match.record.bug_type,
                    "title": match.version.title,
                    "version_confirmed": match.version_confirmed,
                    "match_note": match.result.reason,
                    "citations": [str(match.version.id)],
                    "evidence_url": match.version.evidence_url,
                    "observed_at": match.version.observed_at,
                }
            )

    advice.grounded.sort(key=_rank)

    kept: list[dict] = []
    seen_per_component: dict[str, int] = {}
    for finding in advice.grounded:
        component = finding["component"]
        count = seen_per_component.get(component, 0)
        if count < PER_COMPONENT_LIMIT:
            kept.append(finding)
            seen_per_component[component] = count + 1
        else:
            advice.omitted[component] = advice.omitted.get(component, 0) + 1
    advice.grounded = kept

    # observed_at was only needed for ranking; it is not part of the answer.
    for finding in advice.grounded:
        finding.pop("observed_at", None)
    return advice


def interactions(session: Session, profile: StackProfile) -> list[dict]:
    """FR-30 — problems between components rather than within one.

    Deliberately narrow: it reports only pairs the catalog already links, by a record naming one
    component whose text names another. Inferring interactions the catalog does not record would
    be exactly the ungrounded assertion FR-32 forbids, and the plausible-sounding output would be
    the hardest kind to disbelieve.
    """
    refs = {c.ref.lower() for c in profile.components}
    if len(refs) < 2:
        return []

    found: list[dict] = []
    for component in profile.components:
        matches, missing = retrieve(
            session,
            Query(component.subject_domain, component.ref, version=component.version,
                  ecosystem=component.ecosystem),
        )
        if missing is not None:
            continue
        for match in matches:
            text = f"{match.version.title} {match.version.description or ''}".lower()
            for other in refs - {component.ref.lower()}:
                if other in text:
                    found.append({
                        "components": sorted([component.ref.lower(), other]),
                        "title": match.version.title,
                        "citations": [str(match.version.id)],
                    })
    return found


def component_exists(session: Session, ref: str) -> bool:
    """Whether the catalog knows this component at all."""
    return (
        session.query(Component).filter(Component.canonical_ref == ref.lower()).first() is not None
    )

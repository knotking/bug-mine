"""Scan analysis — dependencies plus source, narrowed to what actually reaches the code.

This is where the catalog, applicability matching and reachability meet. The retrieval step
answers "is there a known defect in this dependency at this version"; reachability answers "does
this project touch it". Only the second is what BugMine claims to add, and it is the only reason
a scan here should be shorter than a Dependabot report.

Three rules hold throughout, and each is a requirement rather than a preference:

- **Every finding cites a record.** A finding without a citation is ungrounded, and ADR-0002
  makes that a structural property rather than a prompt instruction. Findings are built *from*
  matches, so a citation cannot be absent.
- **Only a confirmed negative suppresses.** Undetermined reachability produces a finding with
  `reachable = None` and reduced confidence — never silence (ADR-0006).
- **Uncovered components are counted and reported.** An empty result and "we know nothing about
  this dependency" are different answers, and FR-39 forbids presenting the second as the first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from bugmine.catalog import Query, retrieve
from bugmine.inventory.models import Dependency
from bugmine.models import BugType, Component, Finding, FindingCitation, SubjectDomain
from bugmine.reach import (
    Reach,
    affected_symbols,
    analyse_go_source,
    analyse_javascript_source,
    analyse_jvm_source,
    analyse_python_source,
)

# Every ecosystem the catalog holds records for. An ecosystem missing here is reported
# "not covered" for every dependency in it — even when the catalog has records — because the
# lookup never happens. That silently hid the whole Java, Go, Android and iOS catalog behind an
# answer that reads as "we checked and found nothing".
ECOSYSTEM_DOMAIN = {
    "pypi": SubjectDomain.REPO_LIBRARY,
    "npm": SubjectDomain.REPO_LIBRARY,
    "maven": SubjectDomain.REPO_LIBRARY,
    "go": SubjectDomain.REPO_LIBRARY,
    "swift": SubjectDomain.REPO_LIBRARY,
    "cargo": SubjectDomain.REPO_LIBRARY,
    "rubygems": SubjectDomain.REPO_LIBRARY,
    "nuget": SubjectDomain.REPO_LIBRARY,
    "packagist": SubjectDomain.REPO_LIBRARY,
    # Container images. An OS package is the operating system rather than a library — it is
    # what `apt` and `apk` ship, and the catalog holds its breaking changes under that domain.
    "apk": SubjectDomain.OPERATING_SYSTEM,
    "deb": SubjectDomain.OPERATING_SYSTEM,
    "rpm": SubjectDomain.OPERATING_SYSTEM,
}

# Reachability is implemented per language. An ecosystem absent here is not "clean" — it is
# unanalysed, and its findings are reported undetermined rather than narrowed away.
REACHABILITY_SUPPORTED = frozenset({"pypi", "npm", "maven", "go"})

UNCONFIRMED_REACH_CONFIDENCE = 0.5
"""A finding we could not narrow is worth reporting and worth distinguishing. Halving rather
than zeroing, because the match itself is still grounded in a real record."""

UNCONFIRMED_VERSION_PENALTY = 0.7
"""Applied when applicability matched only because the version was unknown."""


@dataclass
class ScanOutcome:
    findings: list[Finding] = field(default_factory=list)
    uncovered: list[Dependency] = field(default_factory=list)
    """Dependencies with no catalog entry at all. Reported, never treated as clean."""
    suppressed: int = 0
    """Matches narrowed away because the project provably does not touch them. The number that
    justifies the feature — worth surfacing rather than hiding, since a scan that suppresses
    everything is indistinguishable from one that found nothing."""
    unanalysed_ecosystems: set[str] = field(default_factory=set)


def _confidence(reach: Reach, *, version_confirmed: bool) -> float:
    score = 1.0 if reach.confirmed else UNCONFIRMED_REACH_CONFIDENCE
    if not version_confirmed:
        score *= UNCONFIRMED_VERSION_PENALTY
    return round(score, 3)


def analyse(
    session: Session,
    *,
    scan_id: uuid.UUID,
    dependencies: list[Dependency],
    sources: dict[str, str],
    bug_types: list[BugType] | None = None,
    now: datetime | None = None,
) -> ScanOutcome:
    """Turn a dependency list plus the project's own source into grounded findings.

    `sources` maps path to file contents for the project's *own* code — not its dependencies'.
    Reachability asks whether this project references the affected symbol; walking into
    installed packages would answer a different and much more expensive question, and is stage
    two of ADR-0006 rather than this.
    """
    at = now or datetime.now(UTC)
    outcome = ScanOutcome()

    python_sources = {p: s for p, s in sources.items() if p.endswith(".py")}
    js_sources = {
        p: s
        for p, s in sources.items()
        if p.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))
    }
    jvm_sources = {p: s for p, s in sources.items() if p.endswith((".java", ".kt", ".kts"))}
    go_sources = {p: s for p, s in sources.items() if p.endswith(".go")}
    # One place the language of a dependency maps to the source it could possibly be in. Keeping
    # it here rather than in branches means adding a language cannot half-happen.
    by_ecosystem = {
        "pypi": (python_sources, analyse_python_source),
        "npm": (js_sources, analyse_javascript_source),
        "maven": (jvm_sources, analyse_jvm_source),
        "go": (go_sources, analyse_go_source),
    }

    for dependency in dependencies:
        domain = ECOSYSTEM_DOMAIN.get(dependency.ecosystem)
        if domain is None:
            outcome.uncovered.append(dependency)
            continue

        matches, missing = retrieve(
            session,
            Query(domain, dependency.name, version=dependency.version,
                  ecosystem=dependency.ecosystem),
            bug_types=bug_types,
            at=at,
        )
        if missing is not None:
            outcome.uncovered.append(dependency)
            continue

        for match in matches:
            reach = _reachability(dependency, match, by_ecosystem, outcome)
            if reach.suppressible:
                # The whole point: a known defect in a dependency this project never calls.
                outcome.suppressed += 1
                continue

            component = session.get(Component, match.record.component_id)
            assert component is not None

            finding = Finding(
                scan_id=scan_id,
                component_id=component.id,
                bug_type=match.record.bug_type,
                title=match.version.title,
                detail=_detail(reach),
                # True only when confirmed reached. Never False: a confirmed negative was
                # suppressed above, so a stored False could only ever be a lie.
                reachable=True if (reach.reached and reach.confirmed) else None,
                confidence=_confidence(reach, version_confirmed=match.version_confirmed),
                version_confirmed=match.version_confirmed,
            )
            # The version identifies its record, so citing the version cites both — and
            # pins which *revision* of the record grounded this finding, which a record-level
            # citation would lose the moment the record is revised.
            finding.citations.append(FindingCitation(bug_version_id=match.version.id))
            outcome.findings.append(finding)

    return outcome


def _reachability(
    dependency: Dependency,
    match: object,
    by_ecosystem: dict,
    outcome: ScanOutcome,
) -> Reach:
    if dependency.ecosystem not in REACHABILITY_SUPPORTED:
        outcome.unanalysed_ecosystems.add(dependency.ecosystem)
        return Reach.unknown(f"reachability is not implemented for {dependency.ecosystem}")

    files, analyse = by_ecosystem[dependency.ecosystem]
    if not files:
        # Source of the right language is absent — a gap in what we were given, not evidence
        # the dependency is untouched.
        return Reach.unknown(f"no {dependency.ecosystem} source was provided to analyse")

    version = match.version  # type: ignore[attr-defined]
    symbols = affected_symbols(version.title, version.description)
    return analyse(files, component=dependency.name, symbols=symbols)


def _detail(reach: Reach) -> str:
    """What the reader needs to judge the finding: where it lands, or why we could not tell."""
    if reach.evidence:
        where = ", ".join(f"{e.path}:{e.line}" for e in reach.evidence[:5])
        more = f" (+{len(reach.evidence) - 5} more)" if len(reach.evidence) > 5 else ""
        return f"Referenced at {where}{more}."
    return f"Reachability undetermined: {reach.reason}."

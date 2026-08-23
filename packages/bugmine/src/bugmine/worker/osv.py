"""OSV ingestion — the security baseline, deterministically.

OSV publishes structured JSON with the affected ranges already declared, so nothing here goes
near a model. Sending it to one would cost tokens to be *less* certain about fields the feed
states outright, and it would reintroduce the failure this pipeline has had four times: a
release note read backwards, recording a fix as the defect's introduction.

OSV states direction explicitly:

    "events": [{"introduced": "1.3"}, {"fixed": "1.3.4"}]

So the direction is read, never inferred, and the whole inversion class is structurally absent
from this path.

Two properties of the feed that most aggregators get wrong, handled here:

- **Withdrawal.** A record can be retracted upstream. Ingesting it anyway leaves a disowned
  advisory live in our catalog forever, and we would have no way to learn it was pulled.
- **Aliases.** One vulnerability carries a CVE, a GHSA and often a PYSEC or GO id at once.
  Without a stable identity across feeds the same defect is stored several times, and a user
  searching by CVE finds nothing because we filed it under something else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bugmine.catalog import IncomingBug
from bugmine.models import BugType, SubjectDomain

# OSV's ecosystem names to ours. Absent means we do not catalog it rather than that it is safe.
ECOSYSTEMS = {
    "PyPI": "pypi",
    "npm": "npm",
    "Maven": "maven",
    "Go": "go",
    "crates.io": "cargo",
    "RubyGems": "rubygems",
    "NuGet": "nuget",
    "Packagist": "packagist",
    "Pub": "pub",
    "Hex": "hex",
    "SwiftURL": "swift",
}


@dataclass(frozen=True)
class OsvResult:
    bugs: list[IncomingBug]
    withdrawn: int
    """Advisories OSV has retracted. Skipped rather than stored — an entry the source has
    disowned should not become a finding, and we would never learn it had been pulled."""
    skipped: int
    """Entries with no usable affected range, or in an ecosystem we do not catalog."""


def _identity(vuln: dict[str, Any]) -> str:
    """A stable identity across feeds.

    Prefers the CVE when there is one, because that is what a user searches for and what other
    tools report. Falls back to the OSV id. Without this the same defect arrives once as a GHSA
    and again as a PYSEC and is stored twice.
    """
    aliases = [a for a in (vuln.get("aliases") or []) if isinstance(a, str)]
    cve = next((a for a in aliases if a.upper().startswith("CVE-")), None)
    return (cve or vuln.get("id") or "").strip().lower()


def _title(vuln: dict[str, Any]) -> str:
    summary = (vuln.get("summary") or "").strip()
    if summary:
        return summary
    # Details can be pages of markdown; its first line is the closest thing to a summary.
    details = (vuln.get("details") or "").strip().splitlines()
    return (details[0] if details else vuln.get("id") or "Unnamed advisory")[:500]


def _ranges(affected: dict[str, Any]) -> list[tuple[str | None, str | None]]:
    """Every (introduced, fixed) pair in an affected entry.

    A single advisory often covers several branches — fixed in 1.3.4 *and* 2.0.1 — and each is
    its own applicability. Collapsing them to one range would either over-claim or under-claim
    depending on which was kept.
    """
    out: list[tuple[str | None, str | None]] = []
    for entry in affected.get("ranges") or []:
        introduced: str | None = None
        for event in entry.get("events") or []:
            if "introduced" in event:
                # OSV writes "0" for "from the beginning"; None says that more plainly.
                introduced = None if event["introduced"] == "0" else event["introduced"]
            elif "fixed" in event:
                out.append((introduced, event["fixed"]))
                introduced = None
            elif "last_affected" in event:
                out.append((introduced, None))
                introduced = None
        if introduced is not None:
            out.append((introduced, None))
    return out


def to_bugs(vuln: dict[str, Any]) -> list[IncomingBug]:
    """Convert one OSV advisory into catalog records, one per affected package and range."""
    if vuln.get("withdrawn"):
        return []

    identity = _identity(vuln)
    title = _title(vuln)
    bugs: list[IncomingBug] = []

    for affected in vuln.get("affected") or []:
        package = affected.get("package") or {}
        ecosystem = ECOSYSTEMS.get((package.get("ecosystem") or "").split(":")[0])
        name = (package.get("name") or "").strip().lower()
        if not ecosystem or not name:
            continue

        for introduced, fixed in _ranges(affected) or [(None, None)]:
            if introduced is None and fixed is None:
                # No bound at all would match every version of the package forever.
                continue
            bugs.append(
                IncomingBug(
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    component_ref=name,
                    ecosystem=ecosystem,
                    bug_type=BugType.SECURITY,
                    applicability={
                        "kind": "version_range",
                        "scheme": "generic",
                        "introduced_in": introduced,
                        "fixed_in": fixed,
                    },
                    title=title,
                    description=(vuln.get("details") or None),
                    evidence_url=f"https://osv.dev/vulnerability/{vuln.get('id')}",
                    # Scoped by range so two branches of one advisory stay distinct records
                    # rather than colliding on identity and losing one.
                    identity_key=f"{identity}:{introduced or '0'}:{fixed or 'unfixed'}"[:280],
                )
            )
    return bugs


def parse(payload: dict[str, Any]) -> OsvResult:
    """Convert an OSV query response into records."""
    bugs: list[IncomingBug] = []
    withdrawn = skipped = 0
    for vuln in payload.get("vulns") or []:
        if vuln.get("withdrawn"):
            withdrawn += 1
            continue
        produced = to_bugs(vuln)
        if produced:
            bugs.extend(produced)
        else:
            skipped += 1
    return OsvResult(bugs=bugs, withdrawn=withdrawn, skipped=skipped)


OSV_QUERY_URL = "https://api.osv.dev/v1/query"


def fetch(ecosystem: str, name: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Query OSV for one package.

    The ecosystem name is OSV's, not ours — "PyPI" rather than "pypi" — and an unknown one is
    rejected here rather than sent, so a typo returns nothing rather than silently querying the
    wrong ecosystem and reporting an empty result as coverage.
    """
    import httpx

    osv_name = next((k for k, v in ECOSYSTEMS.items() if v == ecosystem), None)
    if osv_name is None:
        raise ValueError(f"no OSV ecosystem for {ecosystem!r}")

    with httpx.Client(timeout=timeout) as http:
        response = http.post(
            OSV_QUERY_URL,
            json={"package": {"name": name, "ecosystem": osv_name}},
            headers={"User-Agent": "BugMine/0.1 (+https://bugmine.dev)"},
        )
        response.raise_for_status()
        return response.json()

"""Deterministic extraction for structured feeds.

Not every source needs a model. A GitHub releases feed is JSON with a tag, a title, and a body
— the version is stated rather than inferred, and a parser gets it exactly right for free.
Sending it to an LLM would cost tokens to be *less* certain about a field the feed already
declares.

So this is not only a fallback for when the model is unavailable. It is the right tool for any
source whose structure is known, and it leaves the model for what it is actually needed for:
prose changelogs where the defect has to be read out of a sentence.

Its limitation is real and worth stating: it can identify *that* a release describes a breaking
change or deprecation, from conventional markers, but it cannot summarise what the change is.
Records it produces carry the release's own title.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bugmine.catalog import IncomingBug
from bugmine.models import BugType, SubjectDomain

# Conventional markers used across release notes. Deliberately conservative: a marker that is
# merely plausible produces a record nobody can trust, and precision is the product's claim.
MARKERS: list[tuple[re.Pattern[str], BugType]] = [
    (re.compile(r"\bbreaking[ -]changes?\b", re.I), BugType.BREAKING_CHANGE),
    (re.compile(r"\bbackwards?[ -]incompatible\b", re.I), BugType.BREAKING_CHANGE),
    (re.compile(r"\bdeprecat(e|ed|ion|ions)\b", re.I), BugType.DEPRECATION),
    (re.compile(r"\bremoved?\b.{0,40}\bsupport\b", re.I), BugType.BREAKING_CHANGE),
    (re.compile(r"\bsecurity (fix|advisory|release)\b", re.I), BugType.SECURITY),
    (re.compile(r"\bCVE-\d{4}-\d+", re.I), BugType.SECURITY),
    (re.compile(r"\bperformance regression\b", re.I), BugType.PERFORMANCE),
    (re.compile(r"\bregression\b", re.I), BugType.FUNCTIONAL),
]

_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][0-9A-Za-z.]+)?)")


@dataclass(frozen=True)
class StructuredResult:
    bugs: list[IncomingBug]
    releases_seen: int
    skipped: int
    """Releases with no recognised marker. Most releases describe features, so a high skip
    count is the expected shape rather than a sign of failure."""


def looks_like_github_releases(document: str) -> bool:
    """Recognise a GitHub releases feed without trusting the source URL.

    Keyed on shape rather than on the URL that produced it, so a source misconfigured to point
    somewhere else falls through to the model rather than being parsed as something it is not.
    """
    try:
        payload = json.loads(document)
    except (json.JSONDecodeError, ValueError):
        return False
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        return False
    first = payload[0]
    return isinstance(first, dict) and "tag_name" in first and "body" in first


def _version_from(tag: str, name: str) -> str | None:
    for candidate in (tag or "", name or ""):
        if m := _VERSION.search(candidate):
            return m.group(1)
    return None


def extract_github_releases(
    document: str,
    *,
    component_ref: str,
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY,
    ecosystem: str | None = None,
    artifact_uri: str | None = None,
) -> StructuredResult:
    payload = json.loads(document)
    if isinstance(payload, dict):
        payload = [payload]

    bugs: list[IncomingBug] = []
    skipped = 0

    for release in payload:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        body = release.get("body") or ""
        name = release.get("name") or ""
        tag = release.get("tag_name") or ""
        haystack = f"{name}\n{body}"

        # First match wins, in marker order — the list is ordered most-specific first so a
        # release mentioning both a breaking change and a regression is classified as the
        # former.
        matched = next((bt for pattern, bt in MARKERS if pattern.search(haystack)), None)
        if matched is None:
            skipped += 1
            continue

        version = _version_from(tag, name)
        bugs.append(
            IncomingBug(
                subject_domain=subject_domain,
                component_ref=component_ref,
                ecosystem=ecosystem,
                bug_type=matched,
                applicability={
                    "kind": "version_range",
                    "scheme": "generic",
                    # The release *introduces* the change, so it is affected from here on.
                    "introduced_in": version,
                    "fixed_in": None,
                },
                title=(name or tag or "release")[:500],
                description=body[:2000] or None,
                evidence_url=release.get("html_url"),
                raw_artifact_uri=artifact_uri,
            )
        )

    return StructuredResult(bugs=bugs, releases_seen=len(payload), skipped=skipped)

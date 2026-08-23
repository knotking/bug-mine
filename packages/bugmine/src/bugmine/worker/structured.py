"""Deterministic extraction for structured feeds.

Not every source needs a model. A GitHub releases feed is JSON with a tag, a title, and a body
— the version is stated rather than inferred, and a parser gets it exactly right for free.
Sending it to an LLM would cost tokens to be *less* certain about a field the feed already
declares.

So this is not only a fallback for when the model is unavailable. It is the right tool for any
source whose structure is known, and it leaves the model for what it is actually needed for:
prose changelogs where the defect has to be read out of a sentence.

**Direction is the whole difficulty.** A release note mentions a defect for one of two opposite
reasons: the release *introduces* it (a removal, a breaking change) or the release *fixes* it (a
security patch, a regression fix). Recording a fix as an introduction inverts the record — every
version that is actually safe gets flagged, and every version that is actually broken gets
cleared. That is strictly worse than having no record, so direction is never assumed here: it
comes from the marker, or from the section heading the marker sits under, and a match whose
direction cannot be resolved is dropped rather than guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from bugmine.catalog import IncomingBug
from bugmine.models import BugType, SubjectDomain


class Direction(Enum):
    """Which side of the defect's life this version marks."""

    INTRODUCED = "introduced"
    """The release causes the problem — affected from this version onward."""
    FIXED = "fixed"
    """The release resolves the problem — affected up to, but not including, this version."""


@dataclass(frozen=True)
class Marker:
    pattern: re.Pattern[str]
    bug_type: BugType
    direction: Direction | None
    """None means the marker alone is ambiguous and only a section heading can resolve it.
    "regression" is the canonical case: it reads identically in "fixes a regression" and
    "this release introduces a regression"."""


# Deliberately conservative: a marker that is merely plausible produces a record nobody can
# trust, and precision is the product's claim.
#
# The negative lookbehind on "breaking change" is not a nicety. Release notes routinely say
# "considered non-breaking changes", and matching that phrase files a breaking-change record
# against a release whose text says the opposite.
MARKERS: list[Marker] = [
    Marker(re.compile(r"(?<!non-)(?<!non )\bbreaking[ -]changes?\b", re.I),
           BugType.BREAKING_CHANGE, Direction.INTRODUCED),
    Marker(re.compile(r"(?<!non-)(?<!non )\bbackwards?[ -]incompatible\b", re.I),
           BugType.BREAKING_CHANGE, Direction.INTRODUCED),
    Marker(re.compile(r"\bdeprecat(e|es|ed|ing|ion|ions)\b", re.I),
           BugType.DEPRECATION, Direction.INTRODUCED),
    Marker(re.compile(r"\b(drops?|dropped|removes?|removed)\b.{0,40}\bsupport\b", re.I),
           BugType.BREAKING_CHANGE, Direction.INTRODUCED),
    Marker(re.compile(r"\bsecurity (fix|advisory|release|patch)\b", re.I),
           BugType.SECURITY, Direction.FIXED),
    Marker(re.compile(r"\bCVE-\d{4}-\d+", re.I),
           BugType.SECURITY, Direction.FIXED),
    Marker(re.compile(r"\bperformance regressions?\b", re.I),
           BugType.PERFORMANCE, None),
    Marker(re.compile(r"\bregressions?\b", re.I),
           BugType.FUNCTIONAL, None),
]

# Section headings that state the direction for everything beneath them.
_FIX_HEADING = re.compile(r"\b(fix|fixes|fixed|bugfix(es)?|bug fixes|patches|security)\b", re.I)
_BREAK_HEADING = re.compile(
    r"\b(breaking|incompatible|removal|removals|removed|deprecat\w*)\b", re.I
)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", re.M)
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][0-9A-Za-z.]+)?)")

MAX_RECORDS_PER_RELEASE = 20
"""A release note is a document, not a defect list; an unbounded fan-out here would let one
verbose release dominate the catalog."""


@dataclass(frozen=True)
class StructuredResult:
    bugs: list[IncomingBug]
    releases_seen: int
    skipped: int
    """Releases that produced no record. Most releases describe features, so a high skip count
    is the expected shape rather than a sign of failure."""
    unresolved: int = 0
    """Matches dropped because direction could not be established. Tracked separately from
    `skipped` because this is the count that says how much the heading vocabulary is missing."""


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


# A section heading classifies everything beneath it. "## Breaking Changes" followed by bullets
# is the single most common shape in release notes, and each bullet under it is a breaking
# change even when the bullet's own wording carries no marker.
_HEADING_CLASS: list[tuple[re.Pattern[str], BugType, Direction]] = [
    (re.compile(r"\b(breaking|incompatible)\b", re.I),
     BugType.BREAKING_CHANGE, Direction.INTRODUCED),
    (re.compile(r"\bdeprecat\w*\b", re.I), BugType.DEPRECATION, Direction.INTRODUCED),
    (re.compile(r"\bremovals?\b|\bremoved\b", re.I),
     BugType.BREAKING_CHANGE, Direction.INTRODUCED),
    (re.compile(r"\bsecurity\b", re.I), BugType.SECURITY, Direction.FIXED),
]

_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")


def _heading_class(heading: str) -> tuple[BugType | None, Direction | None]:
    """Breaking wins over fix: a heading like "Breaking fixes" describes changes that break."""
    for pattern, bug_type, direction in _HEADING_CLASS:
        if pattern.search(heading):
            return bug_type, direction
    if _BREAK_HEADING.search(heading):
        return None, Direction.INTRODUCED
    if _FIX_HEADING.search(heading):
        return None, Direction.FIXED
    return None, None


def _sections(body: str) -> list[tuple[str, str]]:
    """Split a release body into (heading, text). Text before the first heading gets ""."""
    matches = list(_HEADING.finditer(body))
    if not matches:
        return [("", body)]
    out: list[tuple[str, str]] = []
    if preamble := body[: matches[0].start()].strip():
        out.append(("", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((m.group(1), body[m.end() : end]))
    return out


def _clean_title(line: str) -> str:
    """Strip list markers and decoration so the title reads as a statement, not a bullet."""
    line = re.sub(r"^\s*[-*+]\s*", "", line.strip())
    line = re.sub(r"^\s*\d+[.)]\s*", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # inline links -> their text
    # Underscores are left alone: in this domain `eval_type_backport` is an identifier far
    # more often than it is markdown emphasis, and mangling it breaks the symbol a reader
    # would search for.
    line = re.sub(r"[*`#]+", "", line)
    line = re.sub(r"\s+by @\S+.*$", "", line)  # trailing "by @author in #123"
    return line.strip(" .:—-")


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
    unresolved = 0

    for release in payload:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        body = release.get("body") or ""
        name = release.get("name") or ""
        tag = release.get("tag_name") or ""
        version = _version_from(tag, name)

        before = len(bugs)
        seen: set[tuple[BugType, Direction, str]] = set()

        for heading, text in _sections(body):
            section_type, section_direction = _heading_class(heading)
            section_start = len(bugs)
            # The release title itself can announce a breaking change; treat it as unheaded text.
            haystack_lines = (f"{name}\n{text}" if heading == "" else text).splitlines()

            for line in haystack_lines:
                matched = next((m for m in MARKERS if m.pattern.search(line)), None)
                # A bullet under a classified heading inherits that heading's type, so
                # "## Breaking Changes" / "- removed the old API" records the removal even
                # though the bullet alone says nothing recognisable.
                if matched is None and section_type is not None and _BULLET.search(line):
                    candidates = [(section_type, section_direction)]
                elif matched is not None:
                    candidates = [(matched.bug_type, matched.direction or section_direction)]
                else:
                    candidates = []

                for bug_type, direction in candidates:
                    if direction is None:
                        # Ambiguous marker with no heading to disambiguate it. Dropping this is
                        # the point: guessing here is what inverts records.
                        unresolved += 1
                        continue

                    title = _clean_title(line) or name or tag or "release"
                    key = (bug_type, direction, title.lower())
                    if key in seen:
                        continue
                    seen.add(key)

                    bugs.append(
                        IncomingBug(
                            subject_domain=subject_domain,
                            component_ref=component_ref,
                            ecosystem=ecosystem,
                            bug_type=bug_type,
                            applicability={
                                "kind": "version_range",
                                "scheme": "generic",
                                "introduced_in": (
                                    version if direction is Direction.INTRODUCED else None
                                ),
                                "fixed_in": version if direction is Direction.FIXED else None,
                            },
                            title=title[:500],
                            description=(body[:2000] or None),
                            evidence_url=release.get("html_url"),
                            raw_artifact_uri=artifact_uri,
                        )
                    )
                    break  # first classification wins for this line

                if len(bugs) - before >= MAX_RECORDS_PER_RELEASE:
                    break

            # A classified heading over prose rather than a bullet list still announces the
            # change — "## Breaking Changes" followed by a paragraph is a breaking change even
            # though there is no bullet to name. Per-defect records are preferable and win when
            # bullets exist; this keeps the section from being lost when they do not.
            if (
                section_type is not None
                and section_direction is not None
                and len(bugs) == section_start
                and text.strip()
            ):
                title = _clean_title(heading) or name or tag or "release"
                key = (section_type, section_direction, title.lower())
                if key not in seen:
                    seen.add(key)
                    bugs.append(
                        IncomingBug(
                            subject_domain=subject_domain,
                            component_ref=component_ref,
                            ecosystem=ecosystem,
                            bug_type=section_type,
                            applicability={
                                "kind": "version_range",
                                "scheme": "generic",
                                "introduced_in": (
                                    version if section_direction is Direction.INTRODUCED else None
                                ),
                                "fixed_in": (
                                    version if section_direction is Direction.FIXED else None
                                ),
                            },
                            title=title[:500],
                            description=(body[:2000] or None),
                            evidence_url=release.get("html_url"),
                            raw_artifact_uri=artifact_uri,
                        )
                    )

            if len(bugs) - before >= MAX_RECORDS_PER_RELEASE:
                break

        if len(bugs) == before:
            skipped += 1

    return StructuredResult(
        bugs=bugs, releases_seen=len(payload), skipped=skipped, unresolved=unresolved
    )

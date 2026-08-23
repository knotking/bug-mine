"""Recover the affected symbol names from a record's own text.

Reachability needs to know *what* is affected. Records rarely carry a structured symbol field —
release notes state it in prose — so the names have to be read back out of the title and
description.

This is deliberately conservative. A symbol invented here narrows against something the record
never mentioned, and a wrong narrow *suppresses a real defect* — the one failure this system
must not have. Extracting nothing is safe: the caller treats an empty set as "cannot tell" and
reports the finding unconfirmed.
"""

from __future__ import annotations

import re

# Names as changelogs actually write them.
_BACKTICKED = re.compile(r"`([^`]{2,80})`")
_DOTTED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*\(\s*\)")

# Prose that parses as an identifier but names nothing. Without this, "Fixed issue where the
# behaviour changed" contributes "issue" and every record matches every project.
_STOPWORDS = frozenset(
    [
        "a", "all", "an", "and", "any", "are", "as", "at", "be", "been", "bug", "bugs", "by",
        "case", "cases", "change", "changed", "changes", "class", "classes", "code", "else",
        "error", "errors", "fix", "fixed", "fixes", "for", "from", "function", "functions", "how",
        "if", "in", "is", "issue", "issues", "it", "its", "method", "methods", "module",
        "modules", "new", "no", "not", "now", "object", "objects", "of", "old", "on", "option",
        "options", "or", "parameter", "parameters", "release", "releases", "some", "support",
        "supported", "supports", "test", "tests", "than", "that", "the", "then", "these", "this",
        "those", "to", "type", "types", "use", "used", "user", "users", "using", "value",
        "values", "version", "versions", "warning", "warnings", "was", "were", "what", "when",
        "where", "which", "who", "why", "with", "yes",
    ]
)

# Documentation cross-reference prefixes: SQLAlchemy writes `_orm.Session.flush`, where `_orm`
# is a docs namespace rather than anything importable.
_DOC_PREFIX = re.compile(r"^_[a-z][a-z0-9_]*\.")


def _clean(candidate: str) -> str | None:
    candidate = candidate.strip().strip("().,;:")
    candidate = _DOC_PREFIX.sub("", candidate)
    if not candidate or " " in candidate:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", candidate):
        return None
    parts = candidate.split(".")
    # Every segment being a common word means this is a sentence, not a symbol.
    if all(p.lower() in _STOPWORDS for p in parts):
        return None
    # A bare lowercase common word is prose even when the record backticked it.
    if len(parts) == 1 and candidate.lower() in _STOPWORDS:
        return None
    return candidate


def affected_symbols(title: str, description: str | None = None) -> frozenset[str]:
    """Candidate affected symbols, most reliable source first.

    Backticked names are taken from the title and the description; unbackticked dotted names
    and call syntax only from the title. A description is a paragraph of prose, and mining bare
    identifiers out of it produces matches on words that merely appear near the defect.
    """
    found: set[str] = set()

    for text in (title, description or ""):
        for raw in _BACKTICKED.findall(text):
            if cleaned := _clean(raw):
                found.add(cleaned)

    for pattern in (_DOTTED, _CALL):
        for raw in pattern.findall(title):
            if cleaned := _clean(raw):
                found.add(cleaned)

    # A dotted name implies its own leaf: a record about `Session.flush` should match code that
    # imported `flush` directly.
    for symbol in list(found):
        if "." in symbol:
            found.add(symbol.rsplit(".", 1)[1])

    # It also implies its parent, because records name things finer-grained than code
    # references reach. `Session.flush.objects` is a *parameter*; the call site resolves only
    # to `Session.flush`, so without this the two never intersect and a real defect is
    # suppressed. Parents are kept only while they stay dotted — reducing to a bare `Session`
    # would match every use of the class and give up the precision this exists to provide.
    for symbol in list(found):
        parts = symbol.split(".")
        for cut in range(len(parts) - 1, 1, -1):
            found.add(".".join(parts[:cut]))

    return frozenset(found)

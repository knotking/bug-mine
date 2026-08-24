"""Static reference analysis for Go.

Go's import block is the most tractable of the four languages here: paths are quoted string
literals, they cannot be computed, and the package name is the last path segment unless an
alias says otherwise. That makes the parsing nearly exact — the uncertainty is in which *symbol*
a record names, not in what was imported.

Shapes handled:

    import "github.com/gin-gonic/gin"             single
    import ( "a/b"  alias "c/d"  _ "e/f" )        grouped, aliased, blank
    gin.New()                                     qualified use

The blank import `_ "pkg"` is a reference even though nothing is named: it runs the package's
init, which is exactly how some defects manifest. Treating it as unused would suppress those.
"""

from __future__ import annotations

import re

from bugmine.reach.verdict import Evidence, Reach

_SINGLE = re.compile(r'^\s*import\s+(?:(\w+|_|\.)\s+)?"([^"]+)"', re.M)
_GROUP = re.compile(r"^\s*import\s*\(\s*(.*?)\s*\)", re.M | re.S)
_GROUP_LINE = re.compile(r'^\s*(?:(\w+|_|\.)\s+)?"([^"]+)"', re.M)


def _path_matches(import_path: str, component: str) -> bool:
    """Whether an import path names this module.

    A submodule counts: `github.com/x/y/pkg/sub` belongs to `github.com/x/y`. Bare names also
    match the last segment, since a catalog component is often recorded as `gin` rather than the
    full module path.
    """
    path = import_path.lower()
    comp = component.lower()
    if path == comp or path.startswith(comp + "/"):
        return True
    return "/" not in comp and path.rsplit("/", 1)[-1] == comp


def references_in_go(source: str, *, component: str) -> list[tuple[str, int]]:
    """Every (symbol, line) reference to `component`, aliases and blank imports included."""
    found: list[tuple[str, int]] = []
    locals_: dict[str, str] = {}

    def record(alias: str | None, path: str, line: int) -> None:
        if not _path_matches(path, component):
            return
        found.append((path, line))
        name = path.rsplit("/", 1)[-1]
        found.append((name, line))
        # `_` runs init and binds nothing; `.` dot-imports every exported name.
        if alias and alias not in {"_", "."}:
            locals_[alias] = path
            found.append((alias, line))
        elif not alias:
            locals_[name] = path

    for match in _SINGLE.finditer(source):
        record(match.group(1), match.group(2), source[: match.start()].count("\n") + 1)

    for block in _GROUP.finditer(source):
        base = source[: block.start()].count("\n") + 1
        for line_match in _GROUP_LINE.finditer(block.group(1)):
            offset = block.group(1)[: line_match.start()].count("\n")
            record(line_match.group(1), line_match.group(2), base + offset)

    for name in locals_:
        for match in re.finditer(rf"\b{re.escape(name)}\.([A-Z]\w*)", source):
            found.append((match.group(1), source[: match.start()].count("\n") + 1))

    return found


def analyse_go_source(
    files: dict[str, str],
    *,
    component: str,
    symbols: frozenset[str],
) -> Reach:
    """Decide whether a defect in `component`'s `symbols` is reachable from these files."""
    if not symbols:
        return Reach.unknown("record names no affected symbol")

    references: list[tuple[str, str, int]] = []
    for path, source in files.items():
        for symbol, line in references_in_go(source, component=component):
            references.append((path, symbol, line))

    if not references:
        return Reach.does_not_reach(f"{component} is never imported")

    hits = tuple(
        Evidence(path=path, line=line, symbol=symbol)
        for path, symbol, line in references
        if symbol in symbols or symbol.rsplit("/", 1)[-1] in symbols
    )
    if hits:
        return Reach.reaches(hits)
    return Reach.does_not_reach(f"{component} is imported but the affected symbol is not used")

"""Static reference analysis for JavaScript and TypeScript.

Per ADR-0006 each language needs a parser, not a compiler. Python had one in the standard
library; JavaScript does not, and pulling a full parser in would mean shipping a Node runtime or
a wasm blob into a worker whose whole security value is that it has no egress and few moving
parts.

So this reads imports with regular expressions, which is a real limitation and is stated here
rather than discovered later: a regex cannot know that a name was reassigned, shadowed in a
nested scope, or reached through a computed property. **It is therefore biased to over-report.**
Every ambiguity resolves toward "referenced", because a missed reference suppresses a real
finding and that is the one failure this system must not have. A false positive is dismissible.

What it does handle, because these are the shapes that actually appear:

    import x from 'pkg'                     default import
    import {a, b as c} from 'pkg'           named, including aliases
    import * as ns from 'pkg'               namespace
    const {a} = require('pkg')              CommonJS destructuring
    const x = require('pkg')                CommonJS default
    import('pkg')                           dynamic
    export {a} from 'pkg'                   re-export
"""

from __future__ import annotations

import re

from bugmine.reach.verdict import Evidence, Reach

# `from 'pkg'` / `from "pkg"` — the specifier is what names the dependency.
_FROM = re.compile(r"""\bfrom\s+['"]([^'"]+)['"]""")
_REQUIRE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_DYNAMIC = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_SIDE_EFFECT = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.M)

# The binding half of an import statement: everything between `import` and `from`.
_IMPORT_CLAUSE = re.compile(
    r"""\bimport\s+(?!type\b)(.+?)\s+from\s+['"]([^'"]+)['"]""", re.S
)
_REQUIRE_CLAUSE = re.compile(
    r"""(?:const|let|var)\s+(.+?)\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.S
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")


def _specifier_matches(specifier: str, component: str) -> bool:
    """Whether an import specifier names this package.

    Subpath imports count: `lodash/debounce` is lodash, and `@scope/pkg/sub` is `@scope/pkg`.
    Relative paths never match — they are the project's own modules.
    """
    if specifier.startswith("."):
        return False
    parts = specifier.split("/")
    root = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    return root == component or specifier == component


def _bound_names(clause: str) -> set[str]:
    """Local names a clause introduces, and the original names they came from.

    Both are kept: `{a as b}` should match a record naming `a` *and* code using `b`. Keeping
    only one of them would miss a reference, and a missed reference suppresses a finding.
    """
    names: set[str] = set()
    for match in re.finditer(r"([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)", clause):
        names.update(match.groups())
    names.update(_IDENTIFIER.findall(re.sub(r"\bas\b", " ", clause)))
    return {n for n in names if n not in {"type", "default", "as", "from", "import"}}


def references_in_javascript(source: str, *, component: str) -> list[tuple[str, int]]:
    """Every (symbol, line) reference to `component`, local aliases included."""
    found: list[tuple[str, int]] = []
    bindings: dict[str, set[str]] = {}

    for pattern in (_IMPORT_CLAUSE, _REQUIRE_CLAUSE):
        for match in pattern.finditer(source):
            clause, specifier = match.group(1), match.group(2)
            if not _specifier_matches(specifier, component):
                continue
            line = source[: match.start()].count("\n") + 1
            names = _bound_names(clause)
            found.append((specifier, line))
            for name in names:
                bindings.setdefault(name, set()).add(specifier)
                found.append((name, line))

    # An import with no binding still reaches the package: a removed module breaks at load.
    for pattern in (_FROM, _REQUIRE, _DYNAMIC, _SIDE_EFFECT):
        for match in pattern.finditer(source):
            specifier = match.group(1)
            if not _specifier_matches(specifier, component):
                continue
            line = source[: match.start()].count("\n") + 1
            found.append((specifier, line))
            # A subpath names the symbol directly. `import 'lodash/debounce'` reaches debounce
            # without ever binding a name, and a record about debounce must match it.
            tail = specifier.rsplit("/", 1)[-1]
            if tail != specifier:
                found.append((tail, line))

    # Uses of the bound names, including member access: `_.debounce`, `ns.thing`.
    for name in bindings:
        for match in re.finditer(rf"\b{re.escape(name)}\s*\.\s*([A-Za-z_$][\w$]*)", source):
            found.append((match.group(1), source[: match.start()].count("\n") + 1))
    return found


def analyse_javascript_source(
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
        for symbol, line in references_in_javascript(source, component=component):
            references.append((path, symbol, line))

    if not references:
        return Reach.does_not_reach(f"{component} is never imported")

    hits = tuple(
        Evidence(path=path, line=line, symbol=f"{component}.{symbol}")
        for path, symbol, line in references
        if symbol in symbols or symbol.rsplit(".", 1)[-1] in symbols
    )
    if hits:
        return Reach.reaches(hits)
    return Reach.does_not_reach(f"{component} is imported but the affected symbol is not used")

"""Static reference analysis for Java and Kotlin.

Same trade as JavaScript, and worth restating rather than assuming: a real parser means shipping
a JVM or a grammar into a worker whose security value is having no egress and few moving parts.
So this reads imports and type references with regular expressions, **biased to over-report** —
a missed reference suppresses a real finding, which is the one failure this system must not
have, while a false positive is dismissible.

Java's shape helps more than JavaScript's did. Imports are explicit, fully qualified, and cannot
be computed:

    import com.fasterxml.jackson.databind.ObjectMapper;   single type
    import com.fasterxml.jackson.databind.*;              on-demand, whole package
    import static org.junit.Assert.assertEquals;          static member

The wildcard is the interesting case. `import java.util.*` binds every type in the package
without naming any, so a record about `ArrayList` matches source that never writes the word.
Treating a wildcard as "no specific symbol" would suppress exactly those findings, so it
resolves to *the package is referenced* and any symbol in it is reachable.
"""

from __future__ import annotations

import re

from bugmine.reach.verdict import Evidence, Reach

_IMPORT = re.compile(r"^\s*import\s+(static\s+)?([\w.]+?)(\.\*)?\s*;", re.M)
# Kotlin drops the semicolon and allows an alias.
_KT_IMPORT = re.compile(r"^\s*import\s+([\w.]+?)(\.\*)?(?:\s+as\s+(\w+))?\s*$", re.M)
_TYPE_USE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")


def _group_matches(coordinate: str, package: str) -> bool:
    """Whether an import belongs to this dependency.

    Maven coordinates are `group:artifact`, and the package usually mirrors the group — jackson's
    `com.fasterxml.jackson.core:jackson-databind` publishes `com.fasterxml.jackson.databind`.
    They agree often enough to be useful and not always, so matching is on the longest shared
    dotted prefix rather than equality. Requiring equality would miss most real imports; matching
    a bare substring would make `org.apache` match everything Apache ever published.
    """
    coord = coordinate.replace(":", ".").replace("-", ".").lower()
    pkg = package.lower()
    coord_parts = [p for p in coord.split(".") if p]
    pkg_parts = [p for p in pkg.split(".") if p]

    shared = 0
    for a, b in zip(coord_parts, pkg_parts, strict=False):
        if a != b:
            break
        shared += 1
    # Three segments, not two. "org.apache" is shared by everything Apache ever published, so a
    # two-segment match would have org.apache.camel claiming every org.apache.kafka import.
    if shared >= 3:
        return True

    # Otherwise the artifact name has to appear in the package. Group and package often diverge
    # entirely — kotlinx-coroutines-core publishes kotlinx.coroutines.* under an org.jetbrains
    # group — and the artifact is what survives that.
    artifact = coordinate.split(":")[-1].replace("-", ".").lower()
    artifact_parts = [p for p in artifact.split(".") if p]
    for size in (3, 2):
        for i in range(len(artifact_parts) - size + 1):
            window = artifact_parts[i : i + size]
            for j in range(len(pkg_parts) - size + 1):
                if pkg_parts[j : j + size] == window:
                    return True
    # A single distinctive segment counts: junit:junit against org.junit.Assert.
    return any(len(p) >= 4 and p in pkg_parts for p in artifact_parts)


def references_in_jvm(source: str, *, component: str) -> list[tuple[str, int]]:
    """Every (symbol, line) reference to `component`, wildcards included."""
    found: list[tuple[str, int]] = []
    wildcard_packages: list[tuple[str, int]] = []

    for pattern in (_IMPORT, _KT_IMPORT):
        for match in pattern.finditer(source):
            groups = match.groups()
            path = groups[1] if pattern is _IMPORT else groups[0]
            wildcard = bool(groups[2] if pattern is _IMPORT else groups[1])
            if not path or not _group_matches(component, path):
                continue
            line = source[: match.start()].count("\n") + 1
            if wildcard:
                # No symbol named, but every type in the package is now in scope.
                wildcard_packages.append((path, line))
                found.append((path, line))
                continue
            found.append((path, line))
            found.append((path.rsplit(".", 1)[-1], line))
            if pattern is _KT_IMPORT and len(groups) > 2 and groups[2]:
                found.append((groups[2], line))  # the alias

    # Under a wildcard import, a capitalised identifier may be one of that package's types. This
    # over-reports on purpose: the alternative is suppressing every finding about a package
    # imported with `.*`, which is most of them in older Java.
    if wildcard_packages:
        _, line = wildcard_packages[0]
        for match in _TYPE_USE.finditer(source):
            found.append((match.group(1), source[: match.start()].count("\n") + 1))

    return found


def analyse_jvm_source(
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
        for symbol, line in references_in_jvm(source, component=component):
            references.append((path, symbol, line))

    if not references:
        return Reach.does_not_reach(f"{component} is never imported")

    hits = tuple(
        Evidence(path=path, line=line, symbol=symbol)
        for path, symbol, line in references
        if symbol in symbols or symbol.rsplit(".", 1)[-1] in symbols
    )
    if hits:
        return Reach.reaches(hits)
    return Reach.does_not_reach(f"{component} is imported but the affected symbol is not used")

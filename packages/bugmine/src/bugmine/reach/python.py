"""Static reference analysis for Python, using the standard library's own parser.

Per ADR-0006 this needs a *parser*, not a compiler — which is what makes per-language support
an order of magnitude cheaper than building call graphs, and why a new ecosystem is weeks
rather than quarters.

The whole difficulty is aliasing. `from pydantic import BaseModel as BM` then `BM(...)` is a
reference to `pydantic.BaseModel`, and a grep for "pydantic.BaseModel" finds nothing. Resolving
local names back to what they were imported as is the entire job here; getting it wrong in the
direction of *missing* a reference would suppress a real finding.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from bugmine.reach.verdict import Evidence, Reach


@dataclass(frozen=True)
class Reference:
    qualified: str
    """Fully-qualified, e.g. "pydantic.BaseModel" or "pydantic" for a bare module use."""
    line: int


class _Collector(ast.NodeVisitor):
    """Resolve every local name back to the module it came from."""

    def __init__(self, component: str) -> None:
        self.component = component
        self.root = component.split(".")[0]
        # local name -> fully-qualified prefix it stands for
        self.bindings: dict[str, str] = {}
        self.references: list[Reference] = []

    # -- imports establish the bindings ------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == self.root or alias.name.startswith(self.root + "."):
                local = alias.asname or alias.name.split(".")[0]
                self.bindings[local] = alias.name if alias.asname else alias.name.split(".")[0]
                self.references.append(Reference(alias.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == self.root or module.startswith(self.root + "."):
            for alias in node.names:
                local = alias.asname or alias.name
                qualified = f"{module}.{alias.name}"
                self.bindings[local] = qualified
                # The import itself is a reference: `from x import y` touches y even if the
                # name is then never called, and a removed y breaks at import time.
                self.references.append(Reference(qualified, node.lineno))
        self.generic_visit(node)

    # -- uses resolve through them ----------------------------------------------------
    def _resolve(self, node: ast.AST) -> str | None:
        """Turn an attribute chain back into a qualified name, or None if not ours."""
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        base = self.bindings.get(current.id)
        if base is None:
            return None
        parts.reverse()
        return ".".join([base, *parts]) if parts else base

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (qualified := self._resolve(node)) is not None:
            self.references.append(Reference(qualified, node.lineno))
        # Do not descend: the outermost attribute already carries the whole chain, and
        # recording `pydantic.a` under `pydantic.a.b` would double-count one reference.
        return

    def visit_Name(self, node: ast.Name) -> None:
        if (base := self.bindings.get(node.id)) is not None:
            self.references.append(Reference(base, node.lineno))
        self.generic_visit(node)


def references_in_python(source: str, *, component: str, path: str = "<source>") -> list[Reference]:
    """Every reference to `component` in one file, with local aliases resolved."""
    tree = ast.parse(source)
    collector = _Collector(component)
    collector.visit(tree)
    return collector.references


def analyse_python_source(
    files: dict[str, str],
    *,
    component: str,
    symbols: frozenset[str],
) -> Reach:
    """Decide whether a defect in `component`'s `symbols` is reachable from these files.

    `files` maps path to source. `symbols` are the affected names taken from the record; an
    empty set means the record did not say what it affects, which is a limit of the record
    rather than a fact about the code.
    """
    if not symbols:
        return Reach.unknown("record names no affected symbol")

    references: list[tuple[str, Reference]] = []
    unparseable = 0
    for path, source in files.items():
        try:
            for reference in references_in_python(source, component=component, path=path):
                references.append((path, reference))
        except SyntaxError:
            # A file we cannot read is a hole in the analysis, not proof of absence.
            unparseable += 1

    root = component.split(".")[0]
    imported = any(r.qualified == root or r.qualified.startswith(root + ".") for _, r in references)
    if not imported:
        if unparseable:
            return Reach.unknown(f"{unparseable} file(s) could not be parsed and none imported it")
        return Reach.does_not_reach(f"{component} is never imported")

    hits = tuple(
        Evidence(path=path, line=reference.line, symbol=reference.qualified)
        for path, reference in references
        if _matches(reference.qualified, root, symbols)
    )
    if hits:
        return Reach.reaches(hits)
    if unparseable:
        return Reach.unknown(
            f"{component} is imported and {unparseable} file(s) could not be parsed"
        )
    return Reach.does_not_reach(f"{component} is imported but the affected symbol is not used")


def _matches(qualified: str, root: str, symbols: frozenset[str]) -> bool:
    """Whether a resolved reference is one of the affected symbols.

    Compared on the trailing segments rather than the full path, because a record says
    `Session.flush` while the code resolves to `sqlalchemy.orm.Session.flush`. Requiring the
    record to spell the full import path would miss almost every real reference.
    """
    tail = qualified[len(root) + 1 :] if qualified.startswith(root + ".") else qualified
    parts = tail.split(".")
    candidates = {".".join(parts[i:]) for i in range(len(parts))}
    candidates.add(tail)
    return bool(candidates & symbols)

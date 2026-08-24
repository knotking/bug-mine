"""Own-code analysis — defects in the user's code, not their dependencies.

FR-36. And FR-37 is the requirement that shapes every decision here: **own-code findings must be
distinguishable from catalog-grounded ones in every output.**

The asymmetry is real and worth restating, because collapsing it is the easy mistake. A
catalog-grounded finding cites a record: *this component, this version, this documented defect,
observed here*. An own-code finding has nothing to cite — it is the system's own judgment about
code it read once. Presented in one undifferentiated list, the weaker evidence borrows the
authority of the stronger, and the citation mechanism that makes the catalog trustworthy becomes
the thing that launders a guess.

So own-code findings:

- carry `origin = own_code` and no citations, structurally — there is no field to put one in
- are never eligible for the shared catalog (FR-42), enforced on origin rather than on content
- state the rule they matched, so a reader can judge the rule rather than trust the verdict

The rules here are deliberately mechanical: patterns with a clear, checkable definition, not
model judgment. A model reading a repository and offering opinions is a different product, it
cannot cite anything, and its plausible-sounding output is the hardest kind to disbelieve.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OwnCodeFinding:
    path: str
    line: int
    rule: str
    """The rule's name, so a reader can judge the rule rather than trust the verdict."""
    message: str
    severity: str = "warning"

    @property
    def citations(self) -> tuple[()]:
        """Always empty, and empty by construction rather than by convention.

        An own-code finding has nothing to cite. Making that a property rather than a field
        means no code path can accidentally attach one and borrow the catalog's authority.
        """
        return ()


@dataclass
class OwnCodeResult:
    findings: list[OwnCodeFinding] = field(default_factory=list)
    files_analysed: int = 0
    files_unparseable: int = 0
    """Counted rather than ignored: a file we could not read is a gap in the analysis, and zero
    findings over unread files is not the same answer as zero findings over read ones."""


def _bare_except(tree: ast.AST, path: str) -> list[OwnCodeFinding]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(OwnCodeFinding(
                path=path, line=node.lineno, rule="bare-except",
                message="Bare `except:` swallows KeyboardInterrupt and SystemExit as well as "
                        "the error you meant to catch, so the process cannot be stopped cleanly.",
            ))
    return out


def _swallowed_exception(tree: ast.AST, path: str) -> list[OwnCodeFinding]:
    """`except: pass` — the pattern that makes a live failure undiagnosable.

    Included because it is the one this project itself shipped: workers converted exceptions
    into a generic error, and the only useful detail was gone by the time anyone looked.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = [n for n in node.body if not isinstance(n, ast.Pass)]
            if not body:
                out.append(OwnCodeFinding(
                    path=path, line=node.lineno, rule="swallowed-exception",
                    message="This exception is caught and discarded. A failure here leaves no "
                            "trace, so it is invisible until something downstream breaks.",
                ))
    return out


def _mutable_default(tree: ast.AST, path: str) -> list[OwnCodeFinding]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                if isinstance(default, ast.List | ast.Dict | ast.Set):
                    out.append(OwnCodeFinding(
                        path=path, line=node.lineno, rule="mutable-default",
                        message=f"`{node.name}` has a mutable default argument. It is created "
                                "once at definition and shared by every call, so state leaks "
                                "between callers.",
                    ))
    return out


def _except_without_from(tree: ast.AST, path: str) -> list[OwnCodeFinding]:
    """Re-raising inside an except block without `from` hides the original traceback."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            raising_new = (
                isinstance(inner, ast.Raise)
                and inner.exc is not None
                and inner.cause is None
                and isinstance(inner.exc, ast.Call)
            )
            if raising_new:
                out.append(OwnCodeFinding(
                        path=path, line=inner.lineno, rule="raise-without-from",
                    message="Raising a new exception inside an except block without `from` "
                            "discards the original cause, which is usually the only thing that "
                            "says what actually went wrong.",
                    severity="info",
                ))
    return out


RULES = (_bare_except, _swallowed_exception, _mutable_default, _except_without_from)


def analyse_python(files: dict[str, str]) -> OwnCodeResult:
    """Run the rules over the project's own Python source.

    Mechanical checks only. Each rule has a definition a reader can verify, which is what an
    own-code finding has instead of a citation.
    """
    result = OwnCodeResult()
    for path, source in sorted(files.items()):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            result.files_unparseable += 1
            continue
        result.files_analysed += 1
        for rule in RULES:
            result.findings.extend(rule(tree, path))
    result.findings.sort(key=lambda f: (f.path, f.line))
    return result

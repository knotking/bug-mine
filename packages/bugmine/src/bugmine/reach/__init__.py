"""Reachability — does a known defect actually touch this project's code?

BugMine's positioning rests on one number: the large majority of flagged dependency
vulnerabilities are never exploitable in the project that imported them, because the project
never calls the affected code. Reporting every catalog match for a resolved dependency is what
Dependabot-class tools already do, and it is the behaviour BugMine exists to improve on. So
reachability is not a refinement here — it is the product claim.

This is **stage one of ADR-0006 only**: static narrowing. It answers whether the affected
symbol or module is referenced anywhere in the project's own source. It does not decide whether
a referenced call path is live under real conditions; that is stage two, and it needs a model.

**The failure direction is asymmetric and deliberate.** Suppressing a real defect is far worse
than reporting one the user does not hit, so "we could not tell" resolves to *reported and
unconfirmed*, never to silence. Only a positive determination that the code is untouched
suppresses anything.
"""

from bugmine.reach.golang import analyse_go_source, references_in_go
from bugmine.reach.javascript import (
    analyse_javascript_source,
    references_in_javascript,
)
from bugmine.reach.jvm import analyse_jvm_source, references_in_jvm
from bugmine.reach.python import analyse_python_source, references_in_python
from bugmine.reach.symbols import affected_symbols
from bugmine.reach.verdict import Evidence, Reach

__all__ = [
    "Evidence",
    "Reach",
    "affected_symbols",
    "analyse_go_source",
    "analyse_javascript_source",
    "analyse_jvm_source",
    "analyse_python_source",
    "references_in_go",
    "references_in_javascript",
    "references_in_jvm",
    "references_in_python",
]

"""MCP server — BugMine inside Cursor and Claude Code.

Runs locally over stdio. The dependency graph is resolved on this machine and only component
names and versions are sent to the API, so **source code never leaves the developer's machine**.
That is the whole reason this fits an editor better than a dashboard: "what will this dependency
cost me" gets answered while the decision is being made, without shipping the codebase anywhere.

Two things every tool here does deliberately:

- Reports what is **not covered** rather than omitting it. An empty answer over a thin catalog
  reads as a clean bill of health, and early on it usually means "we don't know".
- Returns the **evidence URL** with every finding. An assistant relaying a finding without its
  source is asking the developer to trust an assertion.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server import MCPServer

from bugmine.client import BugMineClient
from bugmine.inventory import Dependency, collect

server = MCPServer(
    "bugmine",
    title="BugMine",
    instructions=(
        "Check dependencies against a catalog of known defects — functional regressions, "
        "breaking changes and deprecations, not only CVEs. Call check_dependency before "
        "adding or upgrading a package. Findings cite their evidence; components reported as "
        "not covered are unknown, which is not the same as safe."
    ),
)


def _render(matches: list, not_covered: list, *, scanned: int | None = None) -> str:
    lines: list[str] = []
    if scanned is not None:
        lines.append(f"Scanned {scanned} resolved dependencies.\n")

    if matches:
        lines.append(f"{len(matches)} known issue(s):\n")
        for m in matches:
            unconfirmed = "" if m.version_confirmed else " (version unconfirmed)"
            lines.append(f"- [{m.bug_type}] {m.name}@{m.version or '?'}{unconfirmed}")
            lines.append(f"  {m.title}")
            if m.evidence_url:
                lines.append(f"  evidence: {m.evidence_url}")
            lines.append("")
    else:
        lines.append("No known issues for the covered dependencies.\n")

    if not_covered:
        names = ", ".join(f"{n.ecosystem}/{n.name}" for n in not_covered[:10])
        more = f" and {len(not_covered) - 10} more" if len(not_covered) > 10 else ""
        lines.append(
            f"Not covered by the catalog ({len(not_covered)}): {names}{more}. "
            "No coverage is not the same as no problem — treat these as unknown."
        )
    return "\n".join(lines)


@server.tool(
    description=(
        "Check one dependency against the BugMine catalog before adding or upgrading it. "
        "Use when the user is considering a package, changing a version, or asking whether "
        "an upgrade is safe. Returns known defects with the evidence behind each."
    )
)
async def check_dependency(ecosystem: str, name: str, version: str | None = None) -> str:
    """Check a single package. Omit `version` if unknown — results are then unconfirmed."""
    result = BugMineClient().check_dependencies([Dependency(ecosystem, name, version)])
    return _render(result.matches, result.not_covered)


@server.tool(
    description=(
        "Scan a project's lockfiles against the BugMine catalog. Dependencies are resolved "
        "locally and only names and versions are sent — source code never leaves the machine."
    )
)
async def scan_workspace(path: str | None = None) -> str:
    """Scan a project root. Defaults to the current working directory."""
    root = Path(path or os.getcwd()).resolve()
    inventory = collect(root)
    if not inventory.dependencies:
        hint = f" ({', '.join(inventory.unresolved)})" if inventory.unresolved else ""
        return f"No resolved dependencies found in {root}{hint}. A lockfile is needed."

    result = BugMineClient().check_dependencies(list(inventory.dependencies))
    return _render(result.matches, result.not_covered, scanned=len(inventory.dependencies))


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

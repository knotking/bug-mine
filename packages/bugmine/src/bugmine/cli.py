"""`bugmine` — the command line interface.

The scan runs here, not on a server. Lockfiles are read locally and only component names and
versions are sent, which is a set of facts the lockfile already publishes. Source code never
leaves the machine.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bugmine.client import BugMineClient
from bugmine.inventory import collect

BUG_TYPE_LABEL = {
    "security": "SECURITY",
    "functional": "FUNCTIONAL",
    "performance": "PERF",
    "compatibility": "COMPAT",
    "breaking_change": "BREAKING",
    "deprecation": "DEPRECATED",
    "build": "BUILD",
}


def _check(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    inventory = collect(root)

    if not inventory.dependencies:
        print(f"No resolved dependencies found in {root}.", file=sys.stderr)
        if inventory.unresolved:
            for item in inventory.unresolved:
                print(f"  found {item} — a lockfile is needed to resolve versions", file=sys.stderr)
        return 1

    print(f"Scanning {root}")
    print(f"  {len(inventory.dependencies)} dependencies from {', '.join(inventory.sources)}")
    if not inventory.complete:
        # Stated rather than implied: a partial inventory scanned silently looks identical to
        # a clean one.
        print(f"  incomplete: {', '.join(inventory.unresolved)}")
    print()

    client = BugMineClient(base_url=args.url, api_key=args.api_key)
    result = client.check_dependencies(list(inventory.dependencies))

    if result.matches:
        for m in result.matches:
            label = BUG_TYPE_LABEL.get(m.bug_type, m.bug_type.upper())
            suffix = "" if m.version_confirmed else "  (version unconfirmed)"
            print(f"  [{label}] {m.name}@{m.version or '?'}{suffix}")
            print(f"      {m.title}")
            if m.evidence_url:
                print(f"      {m.evidence_url}")
            print()
    else:
        print("  No known bugs reach these dependencies.\n")

    # Coverage is reported, not implied. An empty result over an uncovered catalog is not the
    # same claim as a clean bill of health.
    if result.not_covered and args.show_coverage:
        print(f"  Not covered ({len(result.not_covered)}):")
        for n in result.not_covered[:20]:
            print(f"      {n.ecosystem}/{n.name} — {n.reason}")
        if len(result.not_covered) > 20:
            print(f"      ... and {len(result.not_covered) - 20} more")
        print()
    elif result.not_covered:
        print(
            f"  {len(result.not_covered)} dependencies have no catalog coverage "
            f"(--show-coverage to list)\n"
        )

    return 1 if (result.matches and args.fail_on_findings) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bugmine", description="Check what actually breaks.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Check a project's dependencies against the catalog")
    check.add_argument("path", nargs="?", default=".", help="Project root (default: .)")
    check.add_argument("--url", default=None, help="API base URL (or BUGMINE_URL)")
    check.add_argument("--api-key", default=None, help="API key (or BUGMINE_API_KEY)")
    check.add_argument("--show-coverage", action="store_true", help="List uncovered dependencies")
    check.add_argument(
        "--fail-on-findings", action="store_true", help="Exit non-zero when bugs are found"
    )
    check.set_defaults(func=_check)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

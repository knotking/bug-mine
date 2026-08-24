"""The README and docs index.

Documentation drifts silently: a link breaks when a file moves, and a status table ages every
time something ships. Neither fails a build, so both are checked here — a README claiming
"requirements only" for a subsystem running in production is worse than no README, because it is
believed.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _links(markdown: Path) -> list[str]:
    text = markdown.read_text()
    return [
        link.strip()
        for link in re.findall(r"\]\((?!https?:)([^)]+)\)", text)
        if not link.startswith("#")
    ]


def test_readme_links_resolve() -> None:
    for link in _links(ROOT / "README.md"):
        target = ROOT / link.split("#")[0]
        assert target.exists(), f"README links to a missing path: {link}"


def test_docs_index_links_resolve() -> None:
    for link in _links(ROOT / "docs" / "README.md"):
        target = ROOT / "docs" / link.split("#")[0]
        assert target.exists(), f"docs/README links to a missing path: {link}"


def test_every_adr_is_listed_in_the_docs_index() -> None:
    """An ADR nobody links to is one nobody reads, and the reasoning is the point of writing it."""
    index = (ROOT / "docs" / "README.md").read_text()
    for adr in sorted((ROOT / "docs" / "adr").glob("[0-9]*.md")):
        assert adr.name in index, f"{adr.name} is not linked from docs/README.md"


def test_the_readme_states_what_is_not_built() -> None:
    """A README that only lists what works is a sales page. This one has been wrong in the other
    direction too — claiming "requirements only" for subsystems already in production — so the
    section exists to be maintained rather than admired."""
    readme = (ROOT / "README.md").read_text()
    assert "What is not built" in readme


def test_the_readme_does_not_claim_unbuilt_things_are_live() -> None:
    """The GitHub App is written and unregistered, and evals have no scheduler. Both are easy to
    describe as working because the code exists."""
    readme = (ROOT / "README.md").read_text()
    not_built = readme.split("What is not built")[1]
    assert "GitHub App" in not_built
    assert "scheduler" in not_built

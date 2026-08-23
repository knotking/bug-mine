"""Structural checks on the console page.

The console is one file of hand-written HTML and JavaScript with no build step, so the checks a
framework would give for free have to be asserted somewhere. These are the ones whose absence
produced real, user-visible breakage.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

CONSOLE = Path(__file__).parents[1] / "packages/bugmine/src/bugmine/api/static/console.html"


def _source() -> str:
    return CONSOLE.read_text()


def test_no_id_is_used_twice() -> None:
    """`id="out"` named both the Sign out button and the dependency-check results container.

    `getElementById` returns the first match, so running a check wrote its results into the
    Sign out button and destroyed it — the user could not sign out, and nothing errored.
    """
    ids = Counter(re.findall(r'\bid="([A-Za-z][\w-]*)"', _source()))
    duplicated = {name: count for name, count in ids.items() if count > 1}
    assert duplicated == {}, f"duplicate element ids: {duplicated}"


def test_every_referenced_id_exists() -> None:
    """A typo'd getElementById returns null and fails at the point of use, often silently."""
    source = _source()
    declared = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', source))
    # Ids built by interpolation cannot be checked statically; none are used today.
    referenced = set(re.findall(r"getElementById\('([A-Za-z][\w-]*)'\)", source))
    assert referenced <= declared, f"referenced but never declared: {sorted(referenced - declared)}"


def test_the_theme_toggle_covers_all_three_states() -> None:
    source = _source()
    assert set(re.findall(r'data-theme="(light|dark|system)"', source)) == {
        "light",
        "dark",
        "system",
    }


def test_dark_palette_is_defined_for_both_the_media_query_and_the_explicit_choice() -> None:
    """A colour defined only inside the media query loses its value when a viewer forces dark,
    and one defined only under [data-theme] is missing for viewers on the system default."""
    source = _source()
    media = re.search(
        r'@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-theme="light"\]\)\{(.*?)\}\}',
        source,
        re.S,
    )
    explicit = re.search(r':root\[data-theme="dark"\]\{(.*?)\n\*', source, re.S)
    assert media and explicit
    assert set(re.findall(r"(--[\w-]+):", media.group(1))) == set(
        re.findall(r"(--[\w-]+):", explicit.group(1))
    )


def test_the_header_reserves_room_for_the_fixed_theme_toggle() -> None:
    """The toggle is `position:fixed` at the top-right with a high z-index, and the header puts
    the Sign out button in that same corner. Without a reservation the toggle paints over it:
    the button is present, hit-testable only underneath, and invisible — which is exactly how it
    was reported, as "no logout button", with nothing in the console to explain it.
    """
    source = _source()
    toggle = re.search(r"\.theme\{[^}]*right:(\d+)px", source)
    header = re.search(r"header\{[^}]*padding:\s*\d+px\s+(\d+)px", source)
    assert toggle and header, "theme toggle or header padding not found"
    # Toggle is three 28px buttons plus gaps and padding — roughly 100px wide.
    assert int(header.group(1)) >= int(toggle.group(1)) + 100

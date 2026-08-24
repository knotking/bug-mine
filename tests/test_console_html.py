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


def test_the_theme_picker_offers_all_three_states() -> None:
    """The buttons are interpolated from a list, so the literal attribute never appears in the
    source — assert on the list that generates them and on the labels each one needs."""
    source = _source()
    generated = re.search(r"\['light','dark','system'\]\.map", source)
    assert generated, "theme picker is not generated from all three states"
    for state in ("light", "dark", "system"):
        assert f"{state}:" in source, f"no icon or label defined for {state}"


def test_the_theme_picker_is_not_fixed_to_the_viewport() -> None:
    """It was position:fixed in the top-right corner and covered whatever the page put there —
    Sign out in the console header, then Sign in on the landing page. Reserving space for it was
    a patch that had to be repeated for every new bar, and was forgotten the second time. In
    flow, the corner cannot be occupied twice.
    """
    source = _source()
    rule = re.search(r"\.themepick\{([^}]*)\}", source)
    assert rule, "theme picker rule not found"
    assert "position:fixed" not in rule.group(1)


def test_the_theme_picker_sits_in_both_bars() -> None:
    """Rendered per bar rather than once globally, so it has to appear in each."""
    source = _source()
    assert source.count("${themeControl()}") >= 2


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


def test_every_settings_view_exists() -> None:
    """The sub-nav is built from SETTINGS and dispatches through VIEWS. A name in one and not
    the other renders `undefined is not a function` at click time and nowhere earlier."""
    source = _source()
    block = re.search(r"const SETTINGS=\{([^}]*)\}", source).group(1)
    settings = set(re.findall(r"(\w+):'[^']*'", block))
    views = set(re.findall(r"^  async (\w+)\(\)\{", source, re.M))
    assert settings <= views, f"settings screens with no view: {sorted(settings - views)}"


def test_every_top_level_tab_exists() -> None:
    source = _source()
    block = re.search(r"const TABS=\{([^}]*)\}", source).group(1)
    tabs = set(re.findall(r"(\w+):'[^']*'", block))
    views = set(re.findall(r"^  async (\w+)\(\)\{", source, re.M))
    assert tabs <= views, f"tabs with no view: {sorted(tabs - views)}"


def test_every_landing_nav_link_has_a_section() -> None:
    """The nav is anchors into a scrolling page. A link whose id is not on the page scrolls
    nowhere and reports nothing — the failure is silent, which is why it is asserted here."""
    source = _source()
    block = re.search(r"const LAND = \{([^}]*)\}", source).group(1)
    links = set(re.findall(r"(\w+):'[^']*'", block))
    sections = set(re.findall(r'<section[^>]*id="(\w+)"', source))
    assert links <= sections, f"nav links with no section: {sorted(links - sections)}"


def test_the_viewport_is_declared() -> None:
    """Without it a phone renders at desktop width and scales down, so every media query below
    is dead and the page is unreadable regardless of what the CSS says."""
    assert re.search(r'<meta name="viewport"[^>]*width=device-width', _source())


def test_wide_tables_scroll_inside_their_own_box() -> None:
    """A table wider than the screen widens the page itself, and the whole layout scrolls
    sideways. Wrapping it keeps the overflow local, which is why every console table is wrapped
    rather than the body being set to overflow-x:hidden — hiding it clips content instead."""
    source = _source()
    assert source.count("<table>") == source.count('class="tablewrap"'), (
        "every table needs a scroll wrapper"
    )
    assert re.search(r"\.tablewrap\{[^}]*overflow-x:auto", source)


def test_the_nav_survives_narrow_screens() -> None:
    """It used to be display:none below 820px, which left a phone with no navigation at all —
    the sections were reachable only by scrolling past everything above them."""
    source = _source()
    rule = re.search(r"@media\(max-width:820px\)\{(.*?)\n\}", source, re.S)
    assert rule, "820px breakpoint not found"
    assert "display:none" not in rule.group(1).split(".navtabs a")[0]


def test_narrow_screens_are_covered_by_breakpoints() -> None:
    """A phone is ~390px wide. Without a rule at or below 640 the desktop layout applies."""
    widths = [int(w) for w in re.findall(r"@media\(max-width:(\d+)px\)", _source())]
    assert widths, "no max-width breakpoints at all"
    assert min(widths) <= 640, f"narrowest breakpoint is {min(widths)}px"


def test_the_architecture_diagram_is_theme_aware() -> None:
    """Hardcoded colours would render invisible in one theme or the other. Every fill and stroke
    in the diagram has to come from a token, since the same SVG serves both."""
    source = _source()
    block = re.search(r'<svg viewBox="0 0 880 400".*?</svg>', source, re.S)
    assert block, "architecture diagram not found"
    literal_colours = re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]{3,8}|rgb[^"]*)"', block.group(0))
    assert not literal_colours, f"hardcoded colours in the diagram: {literal_colours}"


def test_the_diagram_scrolls_rather_than_widening_the_page() -> None:
    """It has a minimum width to stay legible, so on a phone it must scroll inside its own box
    rather than making the whole page scroll sideways."""
    source = _source()
    rule = re.search(r"\.arch\{([^}]*)\}", source)
    assert rule and "overflow-x:auto" in rule.group(1)


def test_the_diagram_has_an_accessible_description() -> None:
    """It carries the argument of the page. A screen reader landing on an unlabelled SVG gets
    nothing at all."""
    source = _source()
    block = re.search(r'<svg viewBox="0 0 880 400"[^>]*', source, re.S)
    assert block and 'role="img"' in block.group(0) and "aria-label" in block.group(0)

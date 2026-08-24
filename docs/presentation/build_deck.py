#!/usr/bin/env python3
"""Build the BugMine deck.

The generator is checked in rather than only the .pptx because a binary with no source is
a file nobody can correct. Every figure below is either measured from our own deployment or
carries the study it comes from — regenerate with:

    uv run --with python-pptx python docs/presentation/build_deck.py

Live figures (records, sources, coverage) are read from the public stats endpoint when it is
reachable, so a rebuilt deck cannot quietly quote last month's catalog. It falls back to the
values recorded here, with the date they were true.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = Path(__file__).parent / "bugmine.pptx"
STATS_URL = "https://bugmine-5j2s4vtc.uc.gateway.dev/v1/public/stats"

# Measured 2026-08-24; used only if the endpoint is unreachable at build time.
FALLBACK = {"records": 24363, "components": 263, "sources": 313, "beyond_security": 20205}

BG = RGBColor(0x0F, 0x10, 0x13)
FG = RGBColor(0xE9, 0xE9, 0xEE)
MUTED = RGBColor(0x9A, 0x9A, 0xA8)
ACCENT = RGBColor(0x81, 0x8C, 0xF8)
LINE = RGBColor(0x26, 0x27, 0x2E)
WARN = RGBColor(0xE0, 0xA0, 0x6A)
GOOD = RGBColor(0x7F, 0xB0, 0xF5)

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.85)


def stats() -> dict[str, int]:
    try:
        with urllib.request.urlopen(STATS_URL, timeout=10) as r:
            live = json.load(r)
        return {k: int(live[k]) for k in FALLBACK if k in live} or FALLBACK
    except Exception:
        return FALLBACK


S = stats()
BEYOND_PCT = round(S["beyond_security"] / S["records"] * 100)


def deck() -> Presentation:
    p = Presentation()
    p.slide_width, p.slide_height = W, H
    return p


def slide(prs: Presentation):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = BG
    return s


def text(
    s, x, y, w, h, body, *, size=18, color=FG, bold=False, align=PP_ALIGN.LEFT, space=1.25
):
    box = s.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for i, line in enumerate(body if isinstance(body, list) else [body]):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        para.line_spacing = space
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = "Inter"
    return box


def kicker(s, label):
    text(s, MARGIN, Inches(0.6), Inches(8), Inches(0.4), label.upper(), size=12, color=ACCENT,
         bold=True)


def title(s, t, *, size=40):
    text(s, MARGIN, Inches(1.05), W - 2 * MARGIN, Inches(1.5), t, size=size, bold=True)


def sub(s, t, y=Inches(2.15)):
    text(s, MARGIN, y, Inches(10.2), Inches(1.0), t, size=17, color=MUTED, space=1.35)


def rule(s, y):
    ln = s.shapes.add_shape(1, MARGIN, y, W - 2 * MARGIN, Emu(9525))
    ln.fill.solid()
    ln.fill.fore_color.rgb = LINE
    ln.line.fill.background()
    ln.shadow.inherit = False


def stat_row(s, y, items, *, num_size=34):
    """Big number over its caption, evenly spread. Captions carry the source."""
    gap = Inches(0.3)
    w = (W - 2 * MARGIN - gap * (len(items) - 1)) / len(items)
    for i, (n, cap, col) in enumerate(items):
        x = MARGIN + i * (w + gap)
        text(s, x, y, w, Inches(0.8), n, size=num_size, bold=True, color=col)
        text(s, x, y + Inches(0.72), w, Inches(1.6), cap, size=12.5, color=MUTED, space=1.3)


def bullets(s, y, items, *, size=16, gap=Inches(0.92), width=None):
    for i, (head, body) in enumerate(items):
        yy = y + i * gap
        text(s, MARGIN, yy, width or Inches(11.6), Inches(0.4), head, size=size, bold=True)
        if body:
            text(s, MARGIN, yy + Inches(0.34), width or Inches(11.6), Inches(0.6), body,
                 size=13.5, color=MUTED, space=1.3)


def note(s, t):
    text(s, MARGIN, Inches(6.55), W - 2 * MARGIN, Inches(0.6), t, size=11.5, color=MUTED,
         space=1.3)


def build() -> None:
    prs = deck()

    # 1 — title
    s = slide(prs)
    text(s, MARGIN, Inches(2.5), Inches(11), Inches(1.2), "BugMine", size=64, bold=True)
    text(s, MARGIN, Inches(3.6), Inches(10), Inches(1.4),
         "A holistic bug catalog — breaking changes, deprecations, regressions and security "
         "— narrowed to what your code actually reaches.", size=20, color=MUTED, space=1.35)
    text(s, MARGIN, Inches(5.3), Inches(10), Inches(0.5),
         f"{S['records']:,} records · {S['components']} components · "
         f"{BEYOND_PCT}% with no CVE", size=15, color=ACCENT, bold=True)

    # 2 — the problem
    s = slide(prs)
    kicker(s, "the problem")
    title(s, "Scanners are 92% wrong, and silent about the rest")
    sub(s, "Dependency scanners report every package containing a known defect. Most of those "
           "defects are unreachable from the code that imported them — so the report is mostly "
           "noise, and the noise trains people to ignore it.")
    rule(s, Inches(3.5))
    stat_row(s, Inches(3.85), [
        ("92%", "scanner false-positive rate, measured across 2,414 repositories", WARN),
        ("61.9%", "of those false alarms removed by reachability analysis alone", GOOD),
        ("70%", "of vulnerable dependencies need a minor or major upgrade to fix — which may "
                "break you a different way", WARN),
        ("67%", "of Maven packages have violated semver, so a version number is not a promise",
         WARN),
    ])
    note(s, "Figures from published research, cited in our design notes.")

    # 3 — what nobody catalogs
    s = slide(prs)
    kicker(s, "the gap")
    title(s, "Security is the minority of what breaks")
    sub(s, "Every incumbent is built around the CVE. But a deprecation that silently changes "
           "behaviour, a regression in a patch release, or a breaking change buried in a "
           "changelog has no advisory ID and no scanner that looks for it.")
    rule(s, Inches(3.5))
    by = {"deprecation": 9880, "breaking_change": 8336, "security": 4158, "functional": 1891,
          "performance": 98}
    stat_row(s, Inches(3.85), [
        (f"{by['deprecation']:,}", "deprecations", ACCENT),
        (f"{by['breaking_change']:,}", "breaking changes", ACCENT),
        (f"{by['security']:,}", "security — the only class incumbents cover", MUTED),
        (f"{by['functional']:,}", "functional regressions", ACCENT),
        (f"{BEYOND_PCT}%", "of our catalog has no CVE at all", GOOD),
    ], num_size=30)
    note(s, "Our own catalog, measured 2026-08-24. The four non-security classes are the "
            "product.")

    # 4 — market
    s = slide(prs)
    kicker(s, "market")
    title(s, "The cost of the problem, not the size of the tool category")
    sub(s, "Tooling spend is the wrong denominator. What matters is the cost of the defects "
           "the tooling exists to prevent — and the distance between the two.")
    rule(s, Inches(3.5))
    stat_row(s, Inches(3.85), [
        ("$2.41T", "annual cost of poor software quality, US alone — CISQ, 2022", WARN),
        ("$1.52T", "of that is accumulated technical debt: defects already shipped", WARN),
        ("$300B", "global output lost each year to developer inefficiency — Stripe, 2018", WARN),
        ("42%", "of a developer's week spent on maintenance and debugging — same study", WARN),
    ])
    note(s, "Against that, application-security and composition testing together are usually "
            "put in the region of $10–15B a year — estimates vary enough that the range "
            "matters more than any point figure. That gap is the opportunity, and most of "
            "today's spend chases the security minority.")

    # 5 — the LLM projection
    s = slide(prs)
    kicker(s, "projection — labelled as one")
    title(s, "What changes when models write the software")
    sub(s, "The adoption figures are measured. The conclusion drawn from them is an argument, "
           "and it is stated as one.")
    rule(s, Inches(3.35))
    bullets(s, Inches(3.6), [
        ("14% → 90% — enterprise engineers using AI code assistants, 2024 to a projected 2028 "
         "(Gartner)",
         "More than a quarter of new code at Google was already model-generated as of late "
         "2024. The direction has not been in doubt for two years."),
        ("Verification scales with code volume. Review capacity scales with headcount.",
         "Every tool bounding this problem today is bounded by a person reading a diff. "
         "Generation is no longer bounded by a person, so the two curves separate — and the "
         "gap between them is the market."),
        ("The dependency being consumed has no version at all.",
         "A hosted model ships no release notes and cannot be pinned. $10–15B of existing "
         "tooling assumes versioned artifacts and explicit upgrades; none of it has anything "
         "to grip when what changed was weights behind a stable endpoint name."),
    ], gap=Inches(1.05))

    # 6 — what it is
    s = slide(prs)
    kicker(s, "what bugmine is")
    title(s, "One catalog. Four origins in, one question out.")
    sub(s, "Does this defect reach your code? Everything else in the system exists to make "
           "that question answerable and the answer citable.")
    rule(s, Inches(3.35))
    bullets(s, Inches(3.6), [
        ("Holistic, not security-only",
         f"Breaking changes, deprecations, regressions, performance and security. "
         f"{BEYOND_PCT}% of what we hold has no CVE."),
        ("Reachability before reporting",
         "A defect in a package you never call is suppressed and counted, not reported. "
         "Suppression requires a confirmed negative — undetermined stays in the report."),
        ("Every finding cites its record",
         "A finding without a citation is a bug in us, not a judgement call. Components the "
         "catalog has never seen are named as not covered rather than omitted."),
    ], gap=Inches(1.0))

    # 7 — pipeline
    s = slide(prs)
    kicker(s, "how it works")
    title(s, "Four stages", size=36)
    stages = [
        ("1 · Discover", "Four origins", "Crawl reads release notes from "
         f"{S['sources']} sources. OSV contributes declared ranges. Scans feed back what "
         "customers hit. Evals go looking."),
        ("2 · Catalog", "One record per defect", "Content-hashed and deduplicated by defect, "
         "not by release. Every record says whether it was introduced or fixed."),
        ("3 · Reach", "Narrow against your code", "Lockfile resolves locally; imports and call "
         "sites parsed for Python, JavaScript, JVM and Go. Names leave your machine, source "
         "does not."),
        ("4 · Cite", "And name the gaps", "Each finding names the record grounding it. "
         "Uncovered components are listed, because an empty report and an unexamined "
         "dependency look identical."),
    ]
    gap = Inches(0.28)
    cw = (W - 2 * MARGIN - gap * 3) / 4
    for i, (n, head, body) in enumerate(stages):
        x = MARGIN + i * (cw + gap)
        card = s.shapes.add_shape(5, x, Inches(2.35), cw, Inches(3.5))
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0x17, 0x18, 0x1D)
        card.line.color.rgb = ACCENT if i == 2 else LINE
        card.shadow.inherit = False
        text(s, x + Inches(0.25), Inches(2.6), cw - Inches(0.5), Inches(0.4), n, size=14,
             bold=True, color=ACCENT if i == 2 else MUTED)
        text(s, x + Inches(0.25), Inches(3.05), cw - Inches(0.5), Inches(0.5), head, size=19,
             bold=True)
        text(s, x + Inches(0.25), Inches(3.75), cw - Inches(0.5), Inches(1.9), body, size=12.5,
             color=MUTED, space=1.3)
    note(s, "Stage three is the one every incumbent skips, and the reason a BugMine report is "
            "short enough to read.")

    # 8 — measured
    s = slide(prs)
    kicker(s, "measured, not claimed")
    title(s, "One real scan: python-poetry/poetry")
    sub(s, "The central claim of the product, run against a real repository and reported "
           "whole — including the part that is not good news.")
    rule(s, Inches(3.5))
    stat_row(s, Inches(3.85), [
        ("80", "dependencies resolved from the lockfile", FG),
        ("224", "catalog records matched those dependencies", FG),
        ("168", "suppressed — the code never calls them (75%)", GOOD),
        ("56", "reported, each citing its record", ACCENT),
        ("76", "not covered — we hold nothing, and say so", WARN),
    ], num_size=32)
    note(s, "75% suppression is in range of the 61.9% published for reachability analysis. "
            "The 76 uncovered components are the honest weakness: a thin catalog is a real "
            "limitation and naming it is the only defensible way to ship one.")

    # 9 — evals
    s = slide(prs)
    kicker(s, "evals")
    title(s, "The origin that finds bugs instead of reading about them")
    sub(s, "Three of the four origins are downstream of someone else's writing. If nobody "
           "published a changelog entry, nothing arrives. Evals are how a defect enters the "
           "catalog when no such entry will ever exist.")
    rule(s, Inches(3.5))
    bullets(s, Inches(3.75), [
        ("Why hosted models need this and nothing else provides it",
         "A model has no version to pin and ships no release notes. Behaviour changes under a "
         "fixed endpoint name, so there is no artifact for a lockfile to hold. The only way to "
         "know it regressed is to have measured it before."),
        ("Not only models",
         "The same probe-and-compare method applies to anything whose behaviour can be "
         "exercised: a database's edge-case query handling, an API's response to a malformed "
         "request, a runtime across a version bump."),
        ("Corroboration by rate, not by repetition",
         "Probabilistic systems fail intermittently, so 'it failed twice' proves nothing. A "
         "result is promoted when the Wilson interval on its failure rate clears the tolerated "
         "floor, refuted when it falls entirely below — otherwise undetermined, and it says so."),
    ], gap=Inches(0.95))
    note(s, "Status, plainly: the corroboration maths is built and tested. The scheduler that "
            "runs probes on a cadence is not. Evals are design, not something filling the "
            "catalog today.")

    # 10 — delivery
    s = slide(prs)
    kicker(s, "delivery")
    title(s, "Where the answer shows up")
    sub(s, "The value is in the answer arriving at the moment the decision is made, which is "
           "rarely in a dashboard.")
    surfaces = [
        ("MCP", "Cursor · Claude Code", "Ask before you add the dependency, without leaving "
         "the editor."),
        ("GitHub App", "PR check run", "Reachable findings on the diff, plus an explicit "
         "not-covered line."),
        ("CLI", "bugmine check", "Same answer in CI, exit code and all."),
        ("Console", "search · scans", "The catalog is searchable with no account at all."),
        ("Advisor", "before you build", "What is known about a stack you have not adopted yet."),
    ]
    gap = Inches(0.24)
    cw = (W - 2 * MARGIN - gap * 4) / 5
    for i, (n, sublbl, body) in enumerate(surfaces):
        x = MARGIN + i * (cw + gap)
        card = s.shapes.add_shape(5, x, Inches(3.45), cw, Inches(2.5))
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0x17, 0x18, 0x1D)
        card.line.color.rgb = LINE
        card.shadow.inherit = False
        text(s, x + Inches(0.22), Inches(3.7), cw - Inches(0.44), Inches(0.4), n, size=17,
             bold=True)
        text(s, x + Inches(0.22), Inches(4.15), cw - Inches(0.44), Inches(0.3), sublbl,
             size=11.5, color=ACCENT)
        text(s, x + Inches(0.22), Inches(4.6), cw - Inches(0.44), Inches(1.2), body, size=12,
             color=MUTED, space=1.3)

    # 11 — what is built
    s = slide(prs)
    kicker(s, "state of the build")
    title(s, "What runs today, and what does not")
    sub(s, "Separated deliberately. A roadmap that reads as shipped is the fastest way to lose "
           "the reader's trust in every other number here.")
    rule(s, Inches(3.35))
    text(s, MARGIN, Inches(3.6), Inches(6.0), Inches(0.4), "Running in production", size=16,
         bold=True, color=GOOD)
    text(s, MARGIN, Inches(4.05), Inches(6.0), Inches(2.6),
         "Ingestion — crawl, extract, OSV, scheduled sweep\n"
         "Catalog with direction and tagged-union applicability\n"
         "Reachability for Python, JavaScript, JVM, Go\n"
         "Server-side scans, dependency check, public search\n"
         "Console, MCP server, GitHub App, CLI, advisor\n"
         "Multi-tenant isolation enforced by row-level security",
         size=13, color=MUTED, space=1.5)
    text(s, Inches(7.2), Inches(3.6), Inches(5.3), Inches(0.4), "Not built", size=16,
         bold=True, color=WARN)
    text(s, Inches(7.2), Inches(4.05), Inches(5.3), Inches(2.6),
         "Eval scheduler — the probe cadence\n"
         "Own-code analysis wired into the scan path\n"
         "Reachability for Swift, Rust, Ruby\n"
         "Cross-tenant promotion at scale\n"
         "Subscriptions and scheduled reports",
         size=13, color=MUTED, space=1.5)

    # 12 — against
    s = slide(prs)
    kicker(s, "the case against")
    title(s, "What argues against all of this")
    sub(s, "Included because an argument that only presents one side is worth less than "
           "nothing.")
    rule(s, Inches(3.35))
    bullets(s, Inches(3.6), [
        ("Reachability is being commoditised",
         "Endor Labs, Snyk and Semgrep all ship it and market it on exactly this alert-fatigue "
         "argument. The 92% figure is a known problem under active attack by funded "
         "incumbents. BugMine is not first."),
        ("The raw material is free",
         "NVD, OSV and GitHub Advisory are public. Aggregation is not defensible — what we add "
         "has to be the reachability, the non-security classes and original discovery, not the "
         "collecting."),
        ("Eval platforms already exist",
         "Langfuse, LangSmith, Braintrust and Arize are established. The distinction is real "
         "but narrow: they evaluate your application, one customer at a time. None maintains a "
         "shared, versioned catalog of model behaviour."),
    ], gap=Inches(0.98))

    # 13 — close
    s = slide(prs)
    text(s, MARGIN, Inches(2.6), Inches(11), Inches(1.0),
         "An empty report and an unexamined dependency", size=34, bold=True)
    text(s, MARGIN, Inches(3.35), Inches(11), Inches(1.0),
         "look exactly the same.", size=34, bold=True, color=ACCENT)
    text(s, MARGIN, Inches(4.5), Inches(10), Inches(1.2),
         "Only one of them is good news. Everything BugMine does comes from refusing to let "
         "those two states share a rendering.", size=17, color=MUTED, space=1.35)
    text(s, MARGIN, Inches(6.0), Inches(10), Inches(0.5),
         f"{S['records']:,} records · {S['sources']} sources · {BEYOND_PCT}% beyond CVEs · "
         "searchable with no account", size=14, color=ACCENT, bold=True)

    prs.save(OUT)
    print(f"  wrote {OUT} — {len(prs.slides.__iter__.__self__._sldIdLst)} slides")


if __name__ == "__main__":
    build()

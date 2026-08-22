# ADR-0006 — Reachability: narrow statically, judge with a model

**Status:** **Proposed** — not yet accepted. This is the largest cost fork in BugMine and the
decision is the user's to make; the ADR exists to have the argument before committing.
**Date:** 2026-08-22
**Related:** FR-12, FR-33, [`../architecture/scanner.md`](../architecture/scanner.md), ADR-0002

## Context

BugMine's positioning is built on one number: *90.5% of flagged vulnerabilities are
non-exploitable*. The claim is that existing tools report every dependency containing a known
defect, most of which the user never actually touches, and that BugMine reports the ones that
**reach** the user's code.

Reachability is therefore not a refinement. It is the product claim. And how it is implemented
determines engineering cost more than any other decision in the system, because the honest
implementations scale per-language rather than once.

The distribution matters for choosing: the large majority of dependency findings are eliminated
by a coarse fact — **the project never calls the affected code at all**. A much smaller residue
requires knowing whether a call path is live under real conditions.

## Options

**A. Manifest-level only.** Report every catalog match for a resolved dependency. This is what
Dependabot-class tools do. Trivial to build, works for every ecosystem immediately. It is also
*precisely the behavior BugMine positions against* — it reproduces the 90.5% problem and makes
the central claim false. Rejected on those grounds, not on cost.

**B. Static call-graph analysis.** Build a call graph from entry points and determine whether the
affected symbol is reachable. Genuinely accurate, explainable, and cheap per scan once built. But
a call graph is a **per-language compiler-grade artifact**: a real implementation for Java, then
another for Python, then TypeScript, Go, Ruby. Dynamic dispatch, reflection, and dynamic imports
degrade it badly in exactly the languages most affected by dependency churn. Cost scales with
ecosystems supported (S-D2), which is the axis the product needs to grow along fastest.

**C. LLM-judged usage.** Give a model the bug description and the relevant code and ask whether it
is reached. Language-agnostic on day one, handles dynamic patterns statically-impossible to
resolve, and matches the infographic's "analyzing how your code actually calls dependencies". But
it is probabilistic on the claim the product is sold on, costs tokens per candidate finding, and
scales cost with catalog size — a better catalog means more candidates means a larger bill per
scan.

**D. Hybrid — narrow statically, judge with a model.** Cheap language-agnostic symbol and import
analysis eliminates candidates where the affected surface is never referenced at all. The residue,
where a reference exists, goes to a model with the actual call sites as context.

## Decision

**Option D, proposed.** Two stages:

1. **Static narrowing.** Resolve whether the affected symbol, module, or entry point is
   referenced anywhere in the project's own code or in a dependency that itself is referenced.
   Import and symbol analysis requires a per-language *parser*, not a per-language *compiler* —
   an order of magnitude less work than option B, and available off the shelf for every major
   ecosystem.
2. **Model judgment on the residue.** For candidates that survive, supply the call sites and ask
   whether the defect's conditions are met — right version, right configuration, right call
   shape.

The reasoning is that the two options split the problem by cost-effectiveness rather than by
technique. The bulk of false positives fall to the coarse test, which is cheap and deterministic;
the expensive, probabilistic test runs only on the residue, so token cost scales with *genuine*
candidates rather than with catalog size.

**Failure direction is specified:** where either stage is uncertain, the finding is **reported
with reduced confidence, never suppressed.** A false positive is visible and dismissible (FR-57);
a false negative is a bug the user never hears about, and there is no feedback path that could
ever reveal it.

## Consequences

**Easier.** New ecosystems need a parser, not an analyzer, so coverage grows at roughly the pace
the product needs. Dynamic-language patterns that defeat static call graphs are handled by the
model stage. Token cost is bounded by the residue rather than by total candidates.

**Harder.** Two mechanisms to build, tune, and explain rather than one. The confidence model must
combine a deterministic signal with a probabilistic one into something a user can act on — and
"reachable" is no longer a boolean, which affects ranking (FR-33), presentation, and the
disposition taxonomy in [`../requirements/feedback.md`](../requirements/feedback.md) where
`not applicable` is exactly a reachability complaint.

**Accepted downsides.** Static narrowing can wrongly exclude reflective or dynamically-constructed
calls, producing the false negatives that are hardest to detect — mitigated by treating dynamic
constructs as "cannot exclude" rather than "not referenced", which deliberately widens the
residue and raises cost. And the model stage means reachability, the core claim, is partly
probabilistic; FR-62's precision metric must therefore break out `not applicable` dispositions
specifically, since those measure exactly this stage.

**New obligations.** Reachability decisions need to be explainable — a user told a finding does
not reach them will ask why, and "the model said so" is not an answer that survives contact with
a security team. Every judgment must carry its call sites. And the confidence combination needs
validation against a labelled set before any precision claim is published (F3).

## Why this is Proposed rather than Accepted

Option B remains defensible if BugMine's ecosystem coverage is deliberately narrow — one or two
languages, deeply — since a real call graph is more accurate, cheaper per scan, and fully
explainable. That is a product-strategy question about breadth versus depth, not a technical one,
and it is not mine to settle.

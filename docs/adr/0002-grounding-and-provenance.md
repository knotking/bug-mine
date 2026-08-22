# ADR-0002 — Enforce finding provenance structurally, not by prompting

**Status:** Accepted
**Date:** 2026-08-22
**Decided by:** Parag Agarwal (product direction), drafted in session

## Context

The advisor and the scanner both use an LLM to turn catalog data into findings (FR-15, FR-32). An
LLM handed a technology stack will produce fluent, plausible, specific problems whether or not
any evidence supports them — and its ungrounded output is stylistically indistinguishable from
its grounded output. The reader cannot tell them apart, which means the system cannot ship them
undifferentiated.

This is existential rather than cosmetic. BugMine's entire claim is that it knows things about
real software. A product that mixes real catalog evidence with confident invention is worse than
no product, because it is trusted and wrong.

## Options

**A. Instruct the model to only use provided data.** Cheapest, and standard practice. Reduces
ungrounded output substantially. But it degrades silently and unpredictably — under an unusual
stack, a long context, or a model swap, compliance drops with no signal. There is no point at
which the system can assert grounding held.

**B. Post-hoc verification.** Generate freely, then check each finding against the catalog with a
second pass. Catches inventions A misses, but costs a second model call per finding and inherits
the same reliability problem one level up — the verifier is also a model.

**C. Structural enforcement at the boundary.** The reasoner receives only the retrieved record
set. Its output schema requires a catalog record ID per finding. Any finding citing an ID outside
the retrieved set is dropped or diverted to an unverified bucket, by set membership.

## Decision

**Option C**, with the unverified bucket retained and rendered visually distinct rather than
discarded.

Hallucinated citations fail a set-membership test. They do not need to be detected, judged, or
reasoned about — they cannot pass, and the guarantee holds regardless of model, prompt, or
context length.

Retaining rather than discarding the unverified bucket is deliberate: a model observation with no
catalog record behind it may still be worth reading, and may indicate a genuine gap in catalog
coverage. It simply must never wear the same authority as a cited finding.

## Consequences

**Easier.** "Is this finding grounded?" becomes a schema question with a definite answer. Model
and prompt changes cannot silently weaken grounding. The unverified bucket doubles as a signal of
where the catalog is thin.

**Harder.** Retrieval quality now bounds advice quality absolutely — anything not retrieved
cannot be said. A weak retriever produces a weak advisor with no way for the model to compensate,
which is the intended trade but a real constraint. The reasoner's output must be structured, so
free-form prose output is unavailable.

**Accepted downsides.** Genuinely useful model knowledge that lacks a catalog record is demoted
to the unverified bucket even when correct. Findings that emerge from the *interaction* of two
components (FR-30) may have no single record to cite; the citation model must accept multiple
record IDs per finding, and interaction findings grounded in two records each covering half the
claim remain a weak spot this ADR does not fully solve.

**New obligation.** A test asserting that a stack of components absent from the catalog produces
zero grounded findings. This is the regression that matters most, and it is invisible in normal
use — every other test can pass while this one silently breaks.

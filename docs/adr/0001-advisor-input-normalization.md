# ADR-0001 — Normalize all advisor inputs into one Stack Profile

**Status:** Accepted
**Date:** 2026-08-22
**Decided by:** Parag Agarwal (product direction), drafted in session

## Context

The advisor accepts four input types (FR-21 – FR-24): a declared stack, a design or architecture
document, an existing repository, and a free-text intention. They differ enormously in structure
— one is a machine-readable list, another is prose whose meaning must be inferred.

Everything downstream is identical regardless: retrieve catalog records, assess sufficiency,
reason, rank, report. The question is where the four paths converge.

The pressure to answer now rather than later: intake type count is expected to grow, and each
additional path multiplies against every downstream behavior if they have not already merged.

## Options

**A. One pipeline per input type.** Each intake owns its whole path end to end. Simplest to build
the first one, and each path can exploit its input's peculiarities — repo intake could inspect
lockfiles at reasoning time rather than committing to a profile up front. Cost: four behaviors
that drift. A heuristic added to the free-text path is absent from the document path, and the
divergence is invisible until two inputs describing the same stack produce different advice.

**B. Converge late, at the reasoner.** Shared reasoning, but each path retrieves for itself. Some
sharing, but sufficiency assessment (FR-26) has to be reimplemented per path, since it depends on
knowing what is unknown — and each path represents unknowns its own way.

**C. Converge immediately, at an intermediate representation.** Every intake produces a Stack
Profile; nothing downstream knows the input type. Requires designing an IR expressive enough for
all four, and the IR becomes a coupling point every intake must satisfy.

## Decision

**Option C.** All four intakes normalize into a Stack Profile, and the entire downstream pipeline
operates only on that.

The deciding property is FR-26 — distinguishing "no known problems" from "not enough
information". That requires unknowns to be represented uniformly, which is only achievable if
there is one representation. Under A or B, "enough information" means something different per
input type, and the advisor's central honesty guarantee becomes four guarantees of varying
quality.

## Consequences

**Easier.** Adding an intake type is one adapter and no downstream change. Sufficiency,
provenance, and ranking are implemented once. Two inputs describing the same stack demonstrably
produce the same advice, because they produce the same profile — which is testable.

**Harder.** The IR must be expressive enough for the vaguest input (free text) and precise enough
for the most exact (declared stack), and it is now a schema every intake is coupled to. Changing
it touches all four adapters.

**Accepted downsides.** Input-specific signal that does not fit the IR is lost at the boundary —
a design document's *rationale* for choosing a component, for instance, is dropped even though it
might inform advice. If that turns out to matter, the fix is extending the IR, not bypassing it;
bypassing it re-creates option A one field at a time.

**New obligation.** The IR is now load-bearing for correctness, not just tidiness. It needs
schema tests asserting that equivalent inputs across all four types produce equivalent profiles.

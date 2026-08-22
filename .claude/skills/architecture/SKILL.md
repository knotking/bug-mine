---
name: architecture
description: Produce or update an architecture document — the components and their responsibilities, the boundaries between them, how data and control flow across those boundaries, the technology choices, and the trade-offs behind them. Use when the user asks to "design the architecture", "how should this be structured", "draw the system diagram", or is about to build something spanning more than one service, process, or module. Not for one module's internals (use `documentation`) and not for recording a single isolated decision (use `adr`).
user-invocable: true
---

# architecture — the shape of the system and why it has that shape

An architecture document earns its place by explaining the parts of the design that reading
the code will never reveal: why the boundaries fall where they do, what was considered and
rejected, and which properties the structure exists to protect. A component inventory that
restates the directory tree is not architecture.

## Workflow

1. **Start from the constraints, not the boxes.** Architecture is mostly a response to
   non-functional requirements — a system that must survive a datacenter loss looks nothing
   like one that must merely restart cleanly. Read `docs/requirements/` if it exists; if it
   doesn't, extract the operative constraints from the user before drawing anything, because
   a diagram drawn without them is arbitrary.

2. **Map the current state first.** For anything other than a greenfield build, read the
   real code and describe what's actually there before proposing changes. Follow imports and
   call paths across files rather than documenting one file in isolation. A proposal written
   against an imagined current state is worse than no proposal.

3. **Define components by responsibility.** For each: what it owns, what it must never do,
   what it depends on, and what it exposes. The useful test is whether you can state each
   component's job in one sentence without the word "and" — if you can't, the boundary is
   probably in the wrong place.

4. **Make the boundaries explicit.** For every arrow between components, say what crosses
   it: the protocol, the contract, whether it's synchronous, and what happens when the far
   side is slow or gone. Unstated failure behavior at a boundary is where most production
   incidents actually live.

5. **Trace the important flows end to end.** Pick the two or three that matter — the primary
   request path, the main write path, and the one everyone gets confused about — and follow
   each through every component, including where state is written and where it can fail
   partway.

6. **Record technology choices with their alternatives.** Each significant choice gets what
   was picked, what else was seriously considered, and the property that decided it. A choice
   with no rejected alternative was not a decision; it was a default, and it should be
   labelled as one so a later reader knows it's cheap to revisit.

7. **Name the failure modes.** What happens under partial failure, dependency outage, load
   spikes, and bad deploys. Include what degrades gracefully and what doesn't.

8. **Draw it.** A mermaid diagram in the doc, kept small enough to read — components and the
   flows between them, not every class. Prefer several focused diagrams over one exhaustive
   one nobody can follow.

9. **Split out the live decisions.** Anything still genuinely open goes to an `adr` rather
   than sitting unresolved in prose, so the decision has a record when it's finally made.

## Output

Write to `docs/architecture/<system-or-feature>.md` (adapt to the project's existing docs
layout if it differs). Where the document asserts a numeric property — throughput, latency,
retention — cite the requirement it comes from rather than restating it, so the two can't
silently drift apart.

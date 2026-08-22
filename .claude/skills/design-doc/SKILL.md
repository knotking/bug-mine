---
name: design-doc
description: Produce a complete technical design document for a substantial piece of work by sequencing the design skills — `requirements`, `data-model`, `api-design`, `architecture`, and `adr` — and carrying each one's output into the next, ending with a single doc that links them. Use when the user asks for "a design doc", "a tech spec", "an RFC", or describes work big enough that jumping to `plan` would mean deciding scope, schema, and contracts implicitly while writing code. Not for work scoped to a single phase (invoke that skill directly) and not for the step-by-step build sequence (use `plan`).
user-invocable: true
---

# design-doc — sequence the design phases into one coherent spec

This skill doesn't reimplement requirements gathering, modelling, or architecture — it runs
the existing skills in the order where each one's output is the next one's input, and
assembles the result. The order matters: entities designed before the requirements are
settled get remodelled, and an architecture chosen before the NFRs are known is a guess.

## Workflow

1. **Scope first, and be willing to stop here.** Establish what's being built and how much of
   the sequence it actually warrants. A single endpoint on an existing service needs an API
   contract and nothing else. Running all five phases on small work produces documents nobody
   reads, which teaches the team to ignore the ones that matter.

2. **Requirements.** Invoke `requirements`. Everything downstream depends on the functional
   requirements and especially the NFRs — carry the requirement IDs forward so later
   documents cite them rather than restating the numbers.

3. **Data model.** Invoke `data-model` against those requirements. Entities usually surface
   ambiguity in the requirements ("is an invitation a thing, or a state on a membership?") —
   when they do, resolve it back in the requirements doc rather than papering over it in the
   schema.

4. **API design.** Invoke `api-design`, using the entity model but not mirroring it. Where
   the wire shape and the stored shape deliberately differ, that's a decision worth recording
   in the doc.

5. **Architecture.** Invoke `architecture` last of the four, since it's a response to the
   NFRs and the contracts above it. If the architecture turns out to make a requirement
   unachievable at acceptable cost, go back and renegotiate the requirement explicitly —
   don't quietly design a system that misses it.

6. **ADRs for the contested calls.** Any choice with a real alternative that was seriously
   considered gets an `adr`. The design doc references them rather than reproducing the
   argument inline.

7. **Assemble and check for contradictions.** Write the top-level doc — problem, summary of
   the approach, links to each artifact, risks, and open questions. Then read the set as a
   whole specifically hunting for drift: a latency target in the requirements that the
   architecture can't meet, an entity named differently in the API than in the schema, a
   requirement no component actually implements. Cross-document contradictions are the
   characteristic failure of a multi-part spec and only show up on a deliberate pass.

## Between phases

- Check in briefly after each phase — a sentence or two on what was decided and what's next.
  Don't chain all five silently; the user should be able to redirect before the next phase
  builds on a wrong turn.
- When a phase surfaces a decision only the user can make — a scope trade-off, a cost
  ceiling, a compliance obligation — stop and ask rather than assuming and continuing, since
  every later phase inherits the assumption.

## Output

Write the top-level document to `docs/design/<feature>.md` (adapt to the project's existing
docs layout if it differs), linking out to the requirements, data-model, API, architecture,
and ADR files rather than duplicating their content. Hand off to `plan` for the build
sequence once the design is settled.

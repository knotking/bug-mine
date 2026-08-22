---
name: requirements
description: Write or update a requirements document — functional requirements (what the system must do, as observable behavior with acceptance criteria) and non-functional requirements (performance, scale, availability, security, compliance, operability). Use when the user asks to "write the requirements", "define the scope", "what are the NFRs", "spec this out", or starts a feature where what-to-build isn't pinned down yet. Not for how-to-build decisions (use `architecture`) and not for the step-by-step build plan (use `plan`).
user-invocable: true
---

# requirements — pin down what must be true before deciding how

Requirements work fails in two predictable ways: functional requirements written so vaguely
that nobody can tell when they're met, and non-functional requirements written as adjectives
("fast", "secure", "scalable") that no test can ever fail. Both are avoided the same way —
every requirement here must be something a person could write a test against.

## Workflow

1. **Establish the problem and the users.** Who is this for, what can't they do today, and
   what changes for them when it ships? If you can't answer these from the conversation or
   the repo, ask — writing requirements on top of a guessed problem produces a document
   that reads well and describes the wrong system.

2. **Separate what from how.** Functional requirements describe observable behavior at the
   system boundary, not the implementation. "Store sessions in Redis" is an architecture
   decision that leaked into the requirements; the requirement behind it is "a signed-in
   user stays signed in across server restarts."

3. **Write functional requirements as testable statements.** Each gets a stable ID (`FR-1`,
   `FR-2`), a single behavior, and acceptance criteria in given/when/then form. If a
   requirement needs the word "and" to describe it, it's usually two requirements. Include
   the unhappy paths — what the system does on invalid input, missing permissions, an
   unavailable dependency — since those are where the real work turns out to be.

4. **Write non-functional requirements with numbers.** Walk the categories explicitly and
   record "N/A — why" rather than dropping a heading, so a reader can tell the difference
   between *considered and irrelevant* and *forgotten*:
   - **Performance** — latency targets at a named percentile and load (p95 < 200ms at 500 rps)
   - **Scale** — data volume, request volume, concurrent users, growth horizon
   - **Availability** — uptime target, acceptable downtime, degradation behavior
   - **Durability & recovery** — what may never be lost, RPO/RTO
   - **Security & privacy** — authn/authz model, data classification, PII handling, audit needs
   - **Compliance** — named regimes (GDPR, SOC 2, HIPAA) and the concrete obligations they impose
   - **Operability** — what must be observable, alertable, and debuggable in production
   - **Compatibility** — browsers, clients, API versions, migration constraints

5. **State what's out of scope.** An explicit non-goals list prevents more rework than any
   other section, because it's the part people disagree with while it's still cheap.

6. **Mark open questions, don't resolve them silently.** Anything you had to assume goes in
   an `Open questions` section with the assumption you proceeded under, so the reader can
   correct it rather than discover it later encoded in the code.

## Output

Write to `docs/requirements/<feature>.md` (adapt to the project's existing docs layout if it
differs). Keep the requirement IDs stable once written — the architecture, API, and test
documents will reference them, and renumbering breaks those links.

Hand off to `data-model` and `api-design` for the entities and contracts these requirements
imply, and to `architecture` for the system that satisfies the NFRs.

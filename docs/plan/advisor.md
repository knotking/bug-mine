# Advisor — Implementation Plan

**Status:** Draft, for approval
**Requirements:** FR-21 – FR-35
**Architecture:** [`../architecture/advisor.md`](../architecture/advisor.md)
**Decisions:** [ADR-0001](../adr/0001-advisor-input-normalization.md) (one IR),
[ADR-0002](../adr/0002-grounding-and-provenance.md) (structural provenance)

---

## What is already true

More of this is built than it looks. The advisor is a *read* surface over a catalog that now
exists, and the pieces it needs are mostly in place:

| Needed | State |
| --- | --- |
| Catalog with retrieval and version matching | **Built** |
| Three-valued matching (`unconfirmed` is not `no`) | **Built** — the sufficiency gate depends on it |
| Not-covered reporting | **Built** |
| Job substrate, queues, cost ceilings | **Built** |
| Tenancy, RLS, principals | **Built** |
| Lockfile inventory (Python, npm) | **Built** — this *is* repo intake |

So the advisor is not a new subsystem. It is an intake layer, a sufficiency gate, and a
reasoning step over machinery that already runs.

## The order that matters

**Ship the intake types in ascending difficulty, not all at once.** Each one below the IR is
identical, so the value of the IR only shows up if the later types are genuinely cheap — and
the only way to know that is to add them one at a time and see.

### Stage 1 — Stack Profile and declared-stack intake

The IR from [`../data-model/stack-profile.md`](../data-model/stack-profile.md): components with
version constraints, topology, workload, and **unknowns as first-class values**.

Declared stack is the intake with no inference at all — a list in, a profile out. Building it
first means the IR gets exercised before anything can hide behind extraction quality.

*Proves:* the IR is expressive enough for the simplest case without special-casing.

### Stage 2 — Sufficiency gate

Partitions the profile into what can be advised on and what cannot, and emits **specific**
questions for the second. Not a validator: FR-28 requires partial advice where the profile
supports it.

*Proves:* the advisor can say "I don't know enough about X" rather than producing confident
generic output. This is the single behaviour that distinguishes it from a chatbot with a
catalog attached.

### Stage 3 — Retrieval and the provenance gate

Retrieval already exists. The new part is the gate: the reasoner sees **only** the retrieved
record set, its output schema requires a record ID per finding, and IDs outside that set fail a
set-membership check.

*Proves:* a stack of components absent from the catalog yields **zero** grounded findings. This
is the test an LLM-backed system fails silently, and it should be written before the reasoner
exists so it fails for the right reason first.

### Stage 4 — Reasoning and ranking

Per-component findings, then interaction findings (FR-30), ranked by likelihood × impact **for
this stack** rather than by generic severity (FR-33).

*Blocked:* Vertex generative models are not enabled on `bugmine-dev`. Stages 1–3 do not need a
model; this one does.

### Stage 5 — Repo intake

`bugmine.inventory` already resolves lockfiles. This stage is an adapter from `Inventory` to
`StackProfile` — genuinely small, which is the IR paying off.

### Stage 6 — Document and free-text intake

The two that need a model to parse. Deliberately last: they are where extraction quality is
hardest to judge, and doing them first would make every downstream problem look like a
reasoning problem.

## The decision this plan does not make

**Whether the advisor is grounded-only.**

ADR-0002 means the advisor can warn about *documented defects* and nothing else. It cannot flag
a capacity mismatch, an architectural anti-pattern, or an operational hazard, because there is
no record to cite.

That is narrower than "tell me what problems I'll run into" suggests, and it has been an open
question since the architecture was written. Two coherent answers:

- **Grounded-only.** Every finding cites a record. Trustworthy, defensible, and quiet on things
  a good architect would flag.
- **Two evidence classes.** Grounded findings plus explicitly-labelled unverified observations,
  rendered distinctly — the same split FR-37 already draws for own-code findings.

The second is more useful and more dangerous: the moment unverified output appears alongside
cited output, the citation stops being the thing that distinguishes them and presentation has to
carry that weight instead.

**This needs answering before Stage 4**, because it changes what the reasoner is asked to do.

## Sizing

| Stage | Depends on | Needs a model |
| --- | --- | :-: |
| 1 Stack Profile + declared intake | — | no |
| 2 Sufficiency gate | 1 | no |
| 3 Retrieval + provenance gate | 2 | no |
| 4 Reasoning + ranking | 3 | **yes** |
| 5 Repo intake | 1 | no |
| 6 Document / free-text intake | 1 | **yes** |

Stages 1–3 and 5 are buildable today. Stages 4 and 6 are blocked on Vertex.

## What would make this wrong

- **If the catalog stays thin**, the advisor's honest answer is almost always "not covered",
  and a correct advisor that says nothing useful is indistinguishable from a broken one. The
  advisor is worth building *after* ingestion breadth, not before.
- **If reachability lands first**, the scanner answers a sharper version of the same question
  for code that already exists, and the advisor's distinct value narrows to the pre-commit case.
- **If evals land first**, the catalog gains records nobody else has, and the advisor becomes
  the natural way to surface them — which is an argument for sequencing evals ahead of this.

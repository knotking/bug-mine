# Trajectory — Why the Gap Widens

**Researched:** 2026-08-22. Extrapolation from the evidence in [`problem.md`](problem.md);
labelled as projection, not measurement.

---

## The structural argument

Every trend below pushes in the same direction: **the volume and variety of things that break
grows, while the tooling stays CVE-shaped.** The gap is not closing through incremental
improvement because it is not a tuning problem — it is a mismatch between the question the tools
answer and the question teams have.

## 1. Code volume decouples from review capacity

AI assistants raise the rate at which code is produced without raising the rate at which it is
understood. The measured consequences are already here — ~19.7% of AI-suggested packages do not
exist, and 43% of those hallucinations reproduce on every run, making them registrable by
attackers.

The second-order effect matters more than the first. More generated code means **more
dependencies pulled in by someone who did not evaluate them**, and dependency count is the input
to every problem in `problem.md`. The 92% false-positive rate is a rate; the absolute volume of
alerts it produces scales with the graph.

## 2. LLMs become load-bearing dependencies with no versioning discipline

This is the sharpest trend and the one with the least existing tooling.

57% of surveyed teams already run agents in production. Those agents depend on hosted models that
change without notice — GPT-4's code-execution success fell 52%→10% in three months with no
version change, and the drift concentrates in exactly the machine-consumable behaviors production
systems depend on (structured output, format compliance).

Every supply-chain tool ever built assumes dependencies are versioned and change is deliberate.
**A growing share of the critical path now violates that assumption**, and no amount of SBOM
improvement addresses it, because there is nothing to put in the SBOM.

Projection: as agents move from experiment to production, silent model drift graduates from a
quality annoyance to an incident class with its own postmortems. The tooling that exists today
watches *your application's* outputs, one customer at a time — nobody maintains shared,
cross-customer knowledge of which model changed and when.

## 3. Agents add failure modes nothing catalogs

Agent systems fail in ways that are neither traditional software defects nor model defects:
tool-calling errors, non-termination, error-recovery failures, multi-step task collapse. The
category is new enough that a shared vocabulary barely exists, let alone a defect catalog.

BugMine's FR-48 targets this. It is the least-validated of the four eval classes and possibly the
most valuable, because it is the newest — but "no incumbent" and "no demand" are hard to
distinguish this early, and that is an honest risk rather than an opportunity.

## 4. Rarer, more expensive failures shift value toward prevention

The Uptime Institute reports per-site outage frequency falling for a fifth consecutive year while
per-incident cost rises — one in five organizations now say their most recent impactful outage
cost over $1M.

This changes what a tool is worth. When failures were frequent and cheap, fast detection and
response dominated. When they are rare and expensive, **preventing one is worth more than
responding to several** — which favors the advisor (act before the code exists) and evals (know
before the incident) over the scanner (find what is already there).

It also makes the sale harder, not easier. Prevented incidents are invisible. A product that stops
things happening has a permanent attribution problem that a product which speeds up response
never has.

## 5. Regulation pulls toward inventory, not toward reachability

SBOM mandates and supply-chain regulation continue to expand what organizations must *record*
about their dependencies. That is a tailwind for adoption of dependency tooling generally.

But note what it does *not* do: compliance regimes ask what you contain, not what actually affects
you. Regulation drives demand for the 92%-false-positive behavior, because an inventory is what is
being audited. **Compliance pressure and BugMine's actual differentiator point in different
directions** — worth knowing before building a compliance story on a precision claim.

## What would falsify this

- **Reachability becomes table stakes and free.** If every SCA vendor ships it well, BugMine's
  scanner differentiator evaporates and only the non-security bug classes remain distinctive.
- **Model providers adopt real versioning.** If hosted models ship immutable, versioned snapshots
  with changelogs, the drift problem shrinks to something crawlable and the LLM eval origin loses
  its uniqueness.
- **Teams keep not paying for non-security bug intelligence.** The most likely quiet failure: the
  problem is real, everyone agrees it is real, and it never acquires a budget line because nothing
  forces it.

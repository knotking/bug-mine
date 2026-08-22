# BugMine Advisor — Functional Requirements (high level)

**Status:** Draft, for discussion
**Sources:** This session's design conversation (2026-08-22). The advisor appears in **neither**
`~/Bug Mine.pdf` nor the positioning infographic — it is new scope, so nothing here traces to a
written source the way `bugmine.md` does.
**Altitude:** Capability level only. IDs continue from FR-20 in
[`bugmine.md`](bugmine.md) and never restart.

---

## What the advisor does

The scanner (FR-12 – FR-16) answers *what is wrong with the code I have*. The advisor answers
the question asked earlier: **given what I am proposing to build, what will I run into?**

Same catalog, different input, different tense. The user supplies a proposal rather than a
repository, and the advisor predicts problems ahead of the work instead of finding them after.

---

## 1. Intake — four ways in, one representation

| ID | Requirement |
| --- | --- |
| **FR-21** | Accept a **declared stack** — components with versions, listed explicitly. |
| **FR-22** | Accept a **design or architecture document** and infer the stack from it. |
| **FR-23** | Accept an **existing repository** and infer the stack from its manifests and code. |
| **FR-24** | Accept a **free-text intention** describing what the user means to build. |
| **FR-25** | Normalize every intake type into a single **Stack Profile**, so all downstream behavior operates on one representation regardless of how the user arrived. |

## 2. Sufficiency — knowing when it cannot answer

| ID | Requirement |
| --- | --- |
| **FR-26** | Distinguish **"no known problems"** from **"not enough information to say"**, and never present the second as the first. |
| **FR-27** | When information is insufficient, ask for the **specific missing fact** rather than issuing a generic request for detail. |
| **FR-28** | Produce partial advice where the profile supports it, scoped to what is actually known, instead of refusing wholesale. |

## 3. Advice — predicting the problems

| ID | Requirement |
| --- | --- |
| **FR-29** | Retrieve catalog records matching the profile's components, honoring version ranges. |
| **FR-30** | Identify **interaction problems between components**, not only defects in each one alone. |
| **FR-31** | Account for the workload the user described — a bug that only manifests at scale is advice only if the stated scale reaches it. |
| **FR-32** | **Ground every finding in cited catalog records.** Anything asserted without a record is labelled unverified and kept visually separate from grounded findings. |
| **FR-33** | Rank findings by likelihood and impact **for this specific stack**, not by the bug's severity in general. |
| **FR-34** | Emit a report of the findings, each traceable to the catalog records behind it. |

## 4. Execution

| ID | Requirement |
| --- | --- |
| **FR-35** | Run as a job on the shared job substrate — state readable via FR-6, telemetry emitted via FR-20, same as crawler and scan workers. |

---

## Non-goals

- **Proposing fixes or alternative designs.** The advisor states what will go wrong. Whether it
  also recommends what to do instead is unanswered (Q7 in `bugmine.md`) and deliberately excluded
  until it is.
- **Judging the design on general merit.** The advisor speaks from catalog evidence about named
  components. A general architecture critique is a different product and would dilute the
  grounding requirement (FR-32) that makes this one trustworthy.

---

## Open questions

| # | Question |
| --- | --- |
| **A1** | Is the advisor interactive — a conversation that asks FR-27's questions and refines — or a one-shot report? FR-27 implies a round trip; FR-34 implies a document. Both are buildable; they are different products. |
| **A2** | For FR-23, does "existing repository" mean advising on a *proposed change* to that repo, or advising on the repo as it stands? The latter overlaps heavily with the scanner. |
| **A3** | What does the advisor do when the catalog has no records for a component at all — silence, or an explicit "this component is not covered"? Silence is indistinguishable from a clean bill of health. |
| **A4** | Does advisor usage bill under FR-17's token/scan model, and does an advice run count as a "scan" for FR-19's metrics? |

---

## Next

`docs/architecture/advisor.md` for the pipeline, `docs/data-model/stack-profile.md` for the IR
and version-range matching. A3 is worth settling early — it changes what the report must say
when the catalog is thin, which it will be at launch.

# BugMine — Discovery: How Bugs Enter the Catalog

**Status:** Draft, for discussion
**Requirements:** FR-40 – FR-56. Extends FR-3/FR-4, which assumed crawling was the only origin.
**Date:** 2026-08-22

---

## 1. Three origins, three epistemics

Crawling was the only origin in the original requirements. There are now three, and they differ
in something more fundamental than mechanism — **what each is capable of knowing**.

| Origin | Knows | Cannot know | Cost | Breadth |
| --- | --- | --- | --- | --- |
| **Crawled** | What someone has already published | Anything unpublished; it is structurally always behind | Low | Very broad |
| **Scan-derived** | What actually breaks real systems in production use | Anything nobody in the customer base has hit | Marginal — a by-product of work already being done | Grows with customers |
| **Eval-derived** | What a probe can provoke, including things never reported | Anything an eval doesn't test for | High per subject | Narrow, deliberate |

This is the substance of the strategy, not an implementation detail. **A catalog built only by
crawling is a search engine over other people's bug reports** — replicable by anyone with a
crawler, and permanently downstream of the sources it reads. The other two origins produce
records that exist nowhere else.

### Origin coverage varies by subject, and one domain has only one viable origin

| Subject | Crawl | Scan-derived | Eval-derived |
| --- | :-: | :-: | :-: |
| Repos / libraries | ●● issue trackers, changelogs | ●● | ● |
| Languages / runtimes | ●● release notes, JEPs, PEPs | ● | ●● |
| Databases | ●● issue trackers | ●● | ●● |
| Messaging queues | ●● | ●● | ●● |
| Operating systems | ●● CVE feeds, kernel lists | ● | ● |
| SaaS platforms | ● status pages, changelogs — behavior changes often go unannounced | ●● | ●● |
| **LLM models** | ✗ **no bug tracker exists** | ●● | ●●● **primary** |

**For LLM models, evals are not an enhancement — they are the only viable origin.** No vendor
publishes a defect tracker for model behavior, and behavior shifts under a stable identifier with
no changelog to crawl. LLM models were the *first* subject listed in the original requirements,
so the catalog cannot serve its own stated scope by crawling alone.

## 2. Origin is part of every record

| ID | Requirement |
| --- | --- |
| **FR-40** | Every catalog record carries its origin — `crawled`, `scan-derived`, or `eval-derived` — visible on the record and on every finding that cites it. |

A user weighing a finding needs to know whether it rests on a vendor's published advisory, on
observation across other customers, or on a reproducible probe. These are different kinds of
claim and must not be presented as interchangeable.

---

## 3. The scan feedback loop

Analyzing a repository produces knowledge about third-party software. That knowledge belongs in
the catalog, where the next scan of a different customer can use it. Each scan makes the catalog
better, which makes every subsequent scan better — the flywheel that makes the catalog
proprietary rather than merely assembled.

| ID | Requirement |
| --- | --- |
| **FR-41** | Bugs identified during a scan **about third-party software** are recorded as catalog candidates. |
| **FR-42** | Findings about a tenant's **own code** (FR-36) are never eligible for the shared catalog, under any corroboration. |
| **FR-43** | Scan-derived records enter with status `candidate` and are not visible to other tenants. |
| **FR-44** | A candidate is promoted to the shared catalog only on **corroboration** — independent observation across ≥ *k* unaffiliated tenants, or a matching public source. |
| **FR-45** | Evidence on a promoted record is sanitized of tenant code, tenant identity, and stack composition before any other tenant can see it. |
| **FR-46** | A tenant always sees its own scan-derived records immediately, whatever their promotion status. |

### The rule that makes this safe

FR-41 and FR-42 together draw the only line that matters: **a scan-derived record is shareable if
and only if its subject is third-party software.** "langchain 0.1.2 mishandles X" is a fact about
langchain and belongs to everyone. "This service leaks connections under retry" is a fact about
the customer and belongs to them alone.

This maps exactly onto the FR-36/FR-37 split already drawn for findings — the same distinction,
now load-bearing for privacy rather than only for presentation.

### Why corroboration, not just sanitization

Sanitization alone is insufficient for two independent reasons, and both must be addressed:

**Privacy.** A single-tenant observation can identify its source even after code is stripped. A
record saying "this library fails when used with a rare combination of two other components" may
describe exactly one customer's stack. Requiring *k* unaffiliated tenants makes the record a
statement about software rather than about anyone.

**Quality.** A scan-derived candidate is an LLM's inference about code it read once. Promoting
that directly into a catalog other customers rely on would let one model error propagate to every
tenant — and, under ADR-0002, arrive wearing a citation, which is the very authority that
mechanism exists to confer. Corroboration is what converts a hypothesis into a record.

---

## 4. Evals — the origin that finds things first

Marked "mostly future work" in the original requirements. Now a first-class origin, and the only
one that can discover a defect nobody has reported.

| ID | Requirement |
| --- | --- |
| **FR-47** | **LLM evals** — run eval suites against LLM models and revisions to discover behavioral defects, regressions, and drift. |
| **FR-48** | **AI agent evals** — evaluate agent harnesses and frameworks: tool-calling correctness, loop and termination behavior, error recovery, multi-step task completion. |
| **FR-49** | **Software evals** — run suites against software versions (databases, queues, libraries) to discover regressions between releases. |
| **FR-50** | **Language and runtime evals** — evaluate language and runtime behavior across versions for semantic changes, performance regressions, and compatibility breaks. |
| **FR-51** | Evals run as jobs on the shared job substrate, with state via FR-6 and telemetry via FR-20. |
| **FR-52** | Every eval-derived record is **reproducible**: it carries the eval, the eval's version, the target revision, and the environment needed to re-run it. |
| **FR-53** | Evals re-run automatically against new versions and revisions, so a regression is detected rather than waiting to be reported. |
| **FR-54** | Eval results are versioned over time (FR-5), because a target's behavior can change with no version change to record it against. |
| **FR-55** | Eval-derived candidates are corroborated by **reproduction** — *n* consecutive reproducing runs — not by tenant count. |
| **FR-56** | Eval coverage is reported per subject: which subjects have evals, what they test, and when they last ran. |

### Corroboration differs by origin, because the evidence differs

| Origin | Corroborated by | Fails when |
| --- | --- | --- |
| Crawled | The publishing source itself | The source is wrong, stale, or **hostile** — see [ADR-0005](../adr/0005-untrusted-content-in-model-pipelines.md) |
| Scan-derived | *k* unaffiliated tenants (FR-44) | The bug is rare, so it never reaches *k* |
| Eval-derived | *n* reproducing runs (FR-55) | The defect is non-deterministic — which is the common case for LLM evals |

Corroboration also runs in reverse: cross-tenant **dismissal** (FR-63 in
[`feedback.md`](feedback.md)) is evidence that a record should not be in the shared catalog, and
routes to the retraction path in [`record-lifecycle.md`](record-lifecycle.md). Without it the
system could learn a record is real but never learn it is false.

FR-55's weakness is worth stating plainly rather than discovering later: **LLM defects are often
probabilistic.** A model that emits malformed structured output 4% of the time will not reproduce
consistently, and a naive "reproduces *n* times in a row" rule would reject a real, expensive
defect. Eval corroboration for the LLM and agent domains needs a *rate* with a confidence
interval, not a boolean — this is the sharpest open problem in this document (see D2).

### Evals close the loop with the taxonomy

FR-53 is what makes the LLM-model row of the coverage matrix tractable at all. A model alias
repointing to a new snapshot (see [`bug-taxonomy.md`](bug-taxonomy.md) §3) produces no
announcement, no version bump, and no crawlable artifact. A scheduled eval that starts failing is
the *only* signal such a change ever generates.

---

## 5. Consequences worth confronting

**The catalog becomes partly derived from customer data.** That changes its legal and contractual
character: terms must state that third-party observations from scans feed a shared catalog, and
customers will ask. FR-42 and FR-45 are the substance of the answer, and they need to be
defensible to a security reviewer, not merely implemented.

**Evals are an ongoing operating cost, not a build.** FR-53 means evals re-run indefinitely
against every tracked revision. For LLM subjects that is a recurring inference bill scaling with
tracked models × eval suites × frequency, and it is a *cost of goods*, not R&D — it belongs in the
same conversation as NFR-39/40.

**Coverage becomes multi-dimensional.** FR-39 requires stating coverage per subject; with three
origins there are now three coverage stories per subject, and "covered by crawling" is a much
weaker claim than "covered by a maintained eval". FR-56 exists so this stays visible.

**The flywheel is real but slow to start.** Corroboration at *k* tenants means the loop
contributes nothing until the customer base is large enough for independent observation. At
year-one scale (NFR-6, ~300 teams) most candidates will not reach *k*, so scan-derived value
accrues mostly to the originating tenant at first. Planning as though the flywheel is immediate
would be a mistake.

---

## 6. Open decisions

*The `D` series here is distinct from `I-D` in [`../architecture/ingestion.md`](../architecture/ingestion.md).*

| # | Decision |
| --- | --- |
| **D1** | The value of *k* in FR-44. Too low leaks; too high starves the catalog at the scale where it is most needed. Likely varies by how specific the record is. |
| **D2** | How probabilistic eval failures are corroborated and expressed. A failure *rate* with a confidence interval, not a boolean — the hardest open problem here. |
| **D3** | Whether customers can contribute or author eval suites, and whether a tenant-authored eval can produce shared-catalog records. |
| **D4** | Whether a tenant may opt out of contributing scan-derived candidates, and whether opting out affects what it receives from the shared catalog. |
| **D5** | Whether eval-derived records about a vendor's product carry disclosure obligations — finding a real defect in a commercial product creates a responsibility that crawling never does. |
| **D6** | Whether agent evals (FR-48) test *frameworks* or *deployed agent configurations*. The second is far more valuable to a customer and far closer to own-code analysis. |

D5 deserves early attention. Once evals discover genuine, unpublished defects in commercial
software, BugMine is a security researcher whether or not it intends to be, and needs a
disclosure policy before that happens rather than after.

# BugMine — Record Lifecycle and Retraction

**Status:** Draft, for discussion
**Requirements:** FR-64 – FR-71.
**Date:** 2026-08-22

---

## Why this exists

Two facts the requirements had not confronted.

**Bugs get fixed.** A record saying "affects Couchbase 7.0 – 7.2.3" is not wrong once 7.2.4 ships
— it is still true of 7.2.3, and still the reason to upgrade. But nothing modelled the difference
between a live defect and a fixed one, and advice that cannot tell them apart cannot say the most
useful thing it knows: *there is a version where this stops.*

**Records can be wrong.** Two of the three origins are inference-based — a scan finding is a
model's reading of code it saw once, an eval result is a probe's interpretation. Under ADR-0002
findings *cite* records, so a false record does not merely sit there being wrong: it lends its
citation to every finding built on it. Without retraction, the mechanism that makes findings
trustworthy is the same mechanism that propagates errors.

---

## 1. States

| ID | Requirement |
| --- | --- |
| **FR-64** | Every catalog record carries a lifecycle state: `candidate`, `active`, `fixed`, `disputed`, `retracted`, or `superseded`. |

| State | Meaning | Grounds new findings? |
| --- | --- | --- |
| `candidate` | Scan- or eval-derived, not yet corroborated (FR-43) | Only for the originating tenant |
| `active` | Believed true and currently applicable | Yes |
| `fixed` | A fix exists upstream; still applicable to unfixed versions | **Yes** — see FR-65 |
| `disputed` | Evidence conflicts; under review | Yes, marked as disputed |
| `retracted` | Believed false | **No** |
| `superseded` | Merged into a canonical record | No — the canonical record does |

| ID | Requirement |
| --- | --- |
| **FR-65** | A `fixed` record remains fully queryable and continues to match affected versions. A fix does not erase applicability — it adds a resolution. |
| **FR-66** | Findings citing a `fixed` record state the version in which the fix landed. |

FR-65 is the correction to the obvious wrong design. Treating "fixed upstream" as "no longer
relevant" would silently stop reporting to every user still on an affected version — which is
most of them, since being on an old version is the normal condition. The fix is the *remedy*, and
a finding that names it (FR-66) is strictly more useful than one that does not.

## 2. Retraction

| ID | Requirement |
| --- | --- |
| **FR-67** | A record can be retracted. Retraction is a **state change, never a delete** — the record, its history, and the reason remain auditable. |
| **FR-68** | Retracting a record **invalidates every finding that cites it**, and affected tenants are notified. |
| **FR-69** | All state transitions are auditable: who or what caused them, when, and on what evidence. |

FR-68 is the requirement that makes retraction mean something, and the one most likely to be
skipped because it is uncomfortable. If BugMine told a customer their system had a problem and
that turns out to be false, the customer has to be told — they may have scheduled an upgrade,
filed a ticket, or delayed a release on the strength of it. A retraction that quietly stops
future findings while leaving past ones standing is not a correction; it is a cover-up with a
database change.

Retraction sources, all of which must reach the same path: internal review, a corrected upstream
source, cross-tenant dismissal (FR-63), an eval that stops reproducing, or a vendor dispute.

## 3. Identity and merging

| ID | Requirement |
| --- | --- |
| **FR-70** | Every record carries a **bug identity key** enabling records describing the same defect to be matched across origins. |
| **FR-71** | Matched records are merged under a canonical record; the others become `superseded`, and citations to them resolve to the canonical record rather than breaking. |

This is load-bearing rather than tidy. The same defect can arrive three ways — a crawler reads
the changelog, a scan hits it in a customer's code, an eval reproduces it — and FR-44 already
depends on recognising that: it promotes a candidate on corroboration by "a matching public
source", which is exactly a cross-origin identity match. Without FR-70, that clause cannot be
implemented, and cross-origin corroboration silently never fires.

FR-71's citation redirection matters because of ADR-0002: findings cite record IDs, and merging
records would otherwise orphan every finding pointing at the loser.

**How identity is actually resolved is unsolved and hard.** Component plus version range plus
symptom is a starting heuristic, but the same defect is routinely described in three different
vocabularies by three different origins. Recorded as L1.

---

## Consequences

**Every read path must filter on state.** Retrieval currently filters on visibility (FR-11) and
promotion (FR-43); state is a third dimension, and a retrieval that forgets it will happily
ground findings in retracted records.

**Retraction is a customer-facing event, not a data operation.** FR-68 means there is a
notification path, a support conversation, and a trust cost. That argues for making promotion
conservative — the cheapest retraction is the one that was never promoted.

**Precision reporting must be state-aware.** A record retracted after grounding findings should
count against precision (FR-62) in the period those findings were issued, not silently vanish
from the denominator.

**Disputed is not a waiting room.** A record can sit in `disputed` indefinitely while still
grounding findings, which is the worst of both. It needs a review deadline, after which it
resolves to `active` or `retracted`.

## Open

| # | Question |
| --- | --- |
| **L1** | How is bug identity (FR-70) resolved across origins that describe the same defect in different vocabularies? The hardest problem in this document. |
| **L2** | How long does a `disputed` record keep grounding findings before it must resolve? |
| **L3** | Does FR-68's notification reach every tenant that ever received the finding, or only those with it currently open? |
| **L4** | Can a vendor dispute a record about their own product, and what standing does that have? This is a policy question with legal edges, related to D5 in [`discovery.md`](discovery.md). |
| **L5** | Do `fixed` records eventually age out, or is a bug in a version nobody runs kept forever? NFR-7's sizing assumes something here. |

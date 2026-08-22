# BugMine — Finding Feedback

**Status:** Draft, for discussion
**Requirements:** FR-57 – FR-63.
**Date:** 2026-08-22

---

## Why this exists

BugMine's central claim is precision. The positioning leads with *90.5% of flagged
vulnerabilities are non-exploitable* and *security tools cause massive alert fatigue* — the whole
argument is that existing tools drown users in findings that do not matter.

Nothing in the requirements so far lets a user say a BugMine finding did not matter.

That is not a missing feature so much as a missing instrument. A product whose differentiator is
precision but which cannot measure its own precision has no way to know whether it is delivering
the thing it sells, and no way to notice when it stops. It is also the highest-value signal the
system could collect: users telling you, for free, which findings were wrong.

---

## 1. Disposition

| ID | Requirement |
| --- | --- |
| **FR-57** | A user can set a **disposition** on any finding: `confirmed`, `not applicable`, `already handled`, `wrong`, or `deferred`. |
| **FR-58** | A disposition may carry a reason, and the system records who set it and when. |
| **FR-59** | Dispositions are tenant-private. Neither the disposition nor its reason may leak a tenant's stack, code, or identity to any other tenant. |

The five values are chosen to be distinguishable in aggregate, because they mean different
things about the *system*:

| Disposition | What it says about BugMine |
| --- | --- |
| `confirmed` | The finding was right and useful |
| `already handled` | Right, but late — a freshness or prioritization problem, not an accuracy one |
| `not applicable` | The bug is real but does not reach this user — a **reachability** failure |
| `wrong` | The bug is not real — an **accuracy** failure |
| `deferred` | Right, not now — carries no quality signal |

Collapsing `not applicable` and `wrong` into one "dismissed" bucket would destroy the most useful
distinction in the set. They indict entirely different parts of the system: one says the catalog
is right and the reachability analysis is wrong, the other says the catalog itself is wrong. A
product positioned against non-exploitable findings needs to know which of those it is producing.

## 2. Suppression

| ID | Requirement |
| --- | --- |
| **FR-60** | A dismissed finding does not re-alert on subsequent scans of the same code **unless it materially changes** — the underlying record is updated, its severity for this stack increases, or the code path involved changes. |
| **FR-61** | Suppressed findings remain visible on request. Suppression hides them from alerts, never from the record. |

FR-60's escape clause is the whole requirement. Permanent suppression keyed on a finding ID
recreates the failure mode BugMine exists to fix, one dismissal at a time: a user dismisses a
finding as not-applicable, the code later changes so that it *is* applicable, and the system
stays silent because it remembers being told to. Suppression must be scoped to the *state* that
was dismissed, not to the finding's identity.

## 3. Precision as a product metric

| ID | Requirement |
| --- | --- |
| **FR-62** | Precision is measured from dispositions and reported by subject domain, bug type, and record origin — separating accuracy failures (`wrong`) from reachability failures (`not applicable`). |
| **FR-63** | A record accumulating dismissals across unaffiliated tenants is flagged for review and possible retraction. |

FR-62 is a product metric, not an internal dashboard. "We are more precise than CVE scanners" is
either a measured claim or marketing, and the breakdown by origin is what makes it actionable:
if crawled records are precise and scan-derived records are not, that is a specific, fixable
finding about the promotion threshold rather than a vague sense that quality is off.

FR-63 closes the loop with discovery. FR-44 promotes a candidate on corroboration across *k*
tenants — dismissal is the same evidence pointing the other way, and ignoring it would mean the
system can learn a record is real but never learn it is false. Cross-tenant dismissal is the
strongest available signal that a record should not be in the shared catalog, and it feeds
directly into the retraction path in [`record-lifecycle.md`](record-lifecycle.md).

---

## Consequences

**Dispositions become a privacy surface.** A reason field is free text a user may fill with code
or internal system names. Under FR-59 it is tenant data, subject to NFR-20 and NFR-24 — and if
aggregate dispositions influence the shared catalog (FR-63), the aggregation must carry no
tenant-identifying content, exactly as FR-45 requires for promotion.

**Silence stops being neutral.** Once feedback exists, a finding nobody dispositions is ambiguous
— unread, or read and ignored? Response rate has to be tracked alongside precision, or the metric
quietly becomes a measure of engaged users rather than of correctness.

**Gaming risk.** If dismissals influence the shared catalog, a tenant can degrade the catalog for
everyone by dismissing indiscriminately. FR-63's threshold must require unaffiliated tenants, the
same defence FR-44 uses for promotion.

## Open

| # | Question |
| --- | --- |
| **F1** | Do dispositions apply to a finding, or to the (record × component) pair? The second suppresses more usefully but is harder to scope. |
| **F2** | Can a team share dispositions across its repos, and should a tenant-level dismissal apply to a newly onboarded repo? |
| **F3** | Does precision (FR-62) get published to customers, or held internally? Publishing it is a strong differentiating claim and a commitment that constrains the roadmap. |
| **F4** | Is there a disposition for "this is a real bug you got wrong in the details"? Partial correctness currently has no expression, and it is probably common. |

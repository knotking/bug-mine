# ADR-0004 — Scan-derived records enter as candidates and require corroboration

**Status:** Accepted
**Date:** 2026-08-22
**Decided by:** Parag Agarwal (product direction), drafted in session
**Related:** [ADR-0002](0002-grounding-and-provenance.md) (provenance), FR-41 – FR-46

## Context

Scanning a customer's repository produces knowledge about third-party software. Feeding that back
into the catalog creates a flywheel — every scan improves the catalog, which improves every
subsequent scan — and is the difference between a catalog anyone with a crawler could assemble
and one that exists only here.

Two forces push against doing it naively.

**Privacy.** The observation originates in customer code. Even with code stripped, a record
derived from one tenant can describe that tenant: "library X fails when combined with Y and Z"
may match exactly one customer's stack. BugMine also now offers a self-hosted option specifically
for customers unwilling to send code out (NFR-34), so the population most affected is the one
most sensitive to it.

**Quality.** A scan finding is an LLM's inference about code it read once. Promoted directly, a
single model error becomes a shared-catalog record that every other tenant's scan then cites —
and under ADR-0002 it arrives wearing a citation, which is precisely the authority that mechanism
exists to confer. A hallucination laundered into evidence is worse than a hallucination.

## Options

**A. Do not feed scans back.** No privacy exposure, no quality risk, and no flywheel. The catalog
stays as good as its crawlers, which means permanently downstream of published sources and
replicable by any competitor with the same crawl targets.

**B. Feed back with sanitization only.** Strip code and identifiers, then publish. Captures the
flywheel immediately and is simple. But sanitization addresses only the code, not the
*inference* — a record can still be re-identifying through the specificity of the stack it
describes, and nothing filters model error.

**C. Candidate status plus corroboration before promotion.** Scan-derived records are private to
their originating tenant until independently observed across *k* unaffiliated tenants or matched
to a public source; only then are they sanitized and promoted.

**D. Human review before promotion.** Highest quality, and a real option at low volume. Does not
scale with the customer base, and the flywheel's entire value is that it scales with it.

## Decision

**Option C**, with the shareability rule stated as a hard boundary:

> A scan-derived record is eligible for the shared catalog **if and only if its subject is
> third-party software.** Findings about a tenant's own code (FR-36) are never eligible, under any
> amount of corroboration.

Corroboration is required for two reasons that happen to have one mechanism. It makes a record a
statement about *software* rather than about a customer, addressing privacy. And it requires
independent observation, addressing model error — one model's mistake will not recur across
unaffiliated tenants' unrelated codebases, while a real defect will.

Option D is not rejected permanently: human review of candidates near the promotion threshold is
compatible with this decision and worth adding while volume is low.

## Consequences

**Easier.** The catalog accumulates knowledge that cannot be crawled, and the accumulation rate
scales with the customer base. Privacy has a defensible answer — a record reaching *k*
unaffiliated tenants is, by construction, not about any one of them. Quality has a filter that
costs no additional inference.

**Harder.** Two record states now exist — candidate and promoted — with different visibility
rules, and every read path must respect the distinction. The promotion pipeline is new machinery.
Sanitization must be verified rather than assumed, since it is now a privacy control.

**Accepted downsides.** *Rare bugs never promote.* A real defect hit by one customer stays
invisible to everyone else — and rare, hard-to-find bugs are exactly the ones most worth sharing.
This is a genuine loss, accepted because the alternative is a re-identification risk that cannot
be bounded.

**The flywheel starts slow.** At year-one scale (NFR-6, ~300 teams) most candidates will not reach
*k*. Scan-derived value accrues almost entirely to the originating tenant at first, and roadmaps
that assume otherwise will be wrong.

**New obligations.** Customer terms must disclose that third-party observations from scans feed a
shared catalog — this is a contractual commitment, not only an implementation. A tenant opt-out
needs a decision (D4 in `../requirements/discovery.md`). And *k* itself is unset: too low leaks,
too high starves the catalog precisely when it is thinnest. It likely cannot be one number, since
a record's re-identification risk scales with how specifically it describes a stack.

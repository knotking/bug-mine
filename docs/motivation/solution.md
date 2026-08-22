# What BugMine Offers

Each element mapped to the evidence in [`problem.md`](problem.md), including where the evidence
**bounds** what can be claimed.

---

## 1. Reachability — against the 92%

**Evidence:** 92.0% scanner false-positive rate across 2,414 repositories; primary cause is
flagging defects in unreachable code.

**BugMine:** reachability is not a feature but the product claim
([ADR-0006](../adr/0006-reachability-analysis.md)) — narrow candidates with cheap per-language
symbol analysis, then judge the residue with a model, so the expensive test runs only where a
reference actually exists.

**What the evidence forbids claiming.** The same study found function call analysis prunes
**61.9%** of false alarms — not all of them. Roughly 38% survive. Any messaging implying
reachability eliminates noise contradicts the best available measurement. The honest claim is a
large, cited reduction, not elimination.

**Where BugMine is not first.** Endor Labs, Snyk, and Semgrep ship reachability and market it on
this exact argument. This is a competitive feature, not a moat.

## 2. Seven bug types — against the category nothing reports

**Evidence:** 67% of Maven packages have violated semver; 41.58% of client-impacting breaking
changes arrive in non-major versions; transitive changes are the leading cause.

**BugMine:** the [taxonomy](../requirements/bug-taxonomy.md) classifies every record on two axes,
and the dense columns are **functional** and **breaking change** — the ones CVE-shaped tooling
does not report at all. Security is the crowded column and the one BugMine competes least on.

**This is the genuine differentiator**, more than reachability. Nobody maintains a catalog of
non-security defects across an entire stack, and the semver data says the need is structural
rather than incidental.

## 3. The advisor — against the 70%

**Evidence:** 70% of vulnerable dependencies require a minor or major update that potentially or
actually breaks source code.

**BugMine:** the [advisor](../architecture/advisor.md) answers *what will this change cost me*
before the change is made — the question every remediation ticket raises and no tool answers.
Four input types normalize to one Stack Profile, and the sufficiency gate distinguishes "no known
problems" from "not enough information", which is the difference between advice and noise.

**This closes the loop the security tools leave open.** They create the ticket; BugMine prices it.
Arguably a stronger wedge than competing on finding the vulnerability at all, since it is
complementary to the incumbent rather than substitutive.

**Bounded by grounding.** [ADR-0002](../adr/0002-grounding-and-provenance.md) means the advisor
can only warn about *documented defects* — it cannot flag a capacity mismatch or an architectural
anti-pattern, because there is no record to cite. That constraint is what makes its output
trustworthy and also narrower than the phrase "what problems will I run into" suggests.

## 4. Evals — against the dependency class with no versioning

**Evidence:** GPT-4 52%→10% with no version change; Anthropic's Aug 2025 routing errors on up to
16% of requests; drift concentrated in structured-output behaviors; "dashboards stay reassuringly
green".

**BugMine:** [evals](../architecture/evals.md) are the **only viable origin** for the LLM domain —
no bug tracker exists to crawl and no version changes to compare. Results are stored as *runs*
and aggregated into a rate with a confidence interval, which is what makes a 4% failure rate
expressible at all. The regression detector converts "passed at rate *r* last week, rate *r′*
today, non-overlapping intervals" into a catalog record.

**Independent validation.** The LLM supply-chain paper's own recommendations — production
contracts with measurable thresholds, risk-category testing, compatibility gates — describe this
surface almost exactly, arrived at from the other direction.

**The moat is temporal.** You can only detect that behavior changed if you were measuring before
it changed. That property compounds and cannot be bought later, which is unusual and worth more
than any feature in the system.

**And it addresses the fastest-growing market** — LLM observability at 36.2% CAGR versus SCA's
~16%, with the budget line already established (89% of teams running agents already fund
observability). See [`market.md`](market.md).

## 5. Measured precision — against alert fatigue itself

**Evidence:** false positives cause a *crying wolf* dynamic in which real findings are ignored.

**BugMine:** [dispositions](../requirements/feedback.md) keep `not applicable` and `wrong`
distinct — one indicts reachability, the other indicts the catalog — and FR-62 makes precision a
**measured, reported product metric broken down by origin**.

Given the entire category is defined by a 92% error rate, "here is our measured precision" is a
differentiating claim that competitors positioned on the same argument would find awkward to
match. It also means the claim can be defended rather than asserted.

## 6. Three origins — against a catalog anyone could assemble

**Evidence:** NVD, OSV, and GitHub Advisory are public. Aggregation is not defensible.

**BugMine:** [three origins](../requirements/discovery.md) with different capabilities — crawling
(broad, always behind), scans (what breaks real systems, scaling with customers), evals (finds
things first). The proprietary content comes from the second and third.
[ADR-0004](../adr/0004-scan-derived-catalog-entries.md) makes the scan flywheel safe via
corroboration across unaffiliated tenants.

**Bounded by cold start.** At ~300 teams most candidates never reach the corroboration threshold,
so the flywheel contributes little at first. FR-39 makes thin coverage *visible* — "not covered"
rather than silence — which is honest and makes early demos weaker. That tension is real and
unresolved.

---

## The one-paragraph version

Vulnerability scanners are wrong 92% of the time because they answer "do you contain this" rather
than "does this reach you". The larger problem is the one they never mention: two thirds of
packages break their versioning contract, and 70% of security fixes require a change that breaks
your code — so the tool that files the ticket is silent about what it costs. Meanwhile a whole
dependency class has stopped being versioned at all, with hosted models degrading five-fold
inside three months while dashboards stay green. BugMine maintains a catalog of what actually
breaks — across seven subject domains and seven bug types, not just security — fed by crawling,
by what it observes in customer code, and by evals that find defects nobody has reported. It tells
you which of them reach you, what a proposed change will cost before you make it, and when a model
you depend on changed behavior underneath you. Every finding cites the record behind it, and its
precision is measured rather than asserted.

## What we would need to be true

Stated plainly, because a motivation document that omits its own assumptions is a pitch deck.

1. **Teams will pay for non-security bug intelligence.** Nothing compels them to. Security
   scanning has compliance behind it; this does not.
2. **Reachability stays hard enough to matter.** If it commoditizes fully, that differentiator is
   gone and only the bug taxonomy remains.
3. **Model providers do not adopt real versioning.** If they ship immutable versioned snapshots
   with changelogs, the eval moat shrinks to something crawlable.
4. **Cross-tenant corroboration produces enough volume to matter** before the customer base is
   large — otherwise the flywheel is a late-stage benefit being used to justify early-stage work.

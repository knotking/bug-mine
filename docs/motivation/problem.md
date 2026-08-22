# The Problem — What Current Tooling Actually Does

**Researched:** 2026-08-22. Sources listed at the bottom.

---

## 1. Vulnerability scanners are wrong 92% of the time

The strongest available measurement, and it is better evidence than anything currently in the
BugMine pitch.

A study presented at CODASPY '26 analyzed **2,414 open-source repositories**. Working from
high-fidelity SBOMs generated with lock files and strong package managers — that is, removing
SBOM inaccuracy as a confound — the authors found:

> "downstream vulnerability scanners produce a staggering **92.0% false positive rate** in our
> case study. We pinpoint the primary cause as the flagging of vulnerabilities within
> **unreachable code**."

Two consequences follow, and the second is the one usually missed.

**The noise is not a tuning problem.** It is structural: the tool answers "does your dependency
graph contain a package with a known defect", which is a different question from "are you
affected". Ninety-two percent of the time those questions have different answers.

**Reachability helps enormously but does not finish the job.** The same study found function call
analysis prunes **61.9%** of the false alarms. That is a large improvement and also a ceiling —
roughly 38% of false positives survive call analysis, so any product claiming reachability solves
the problem is overselling. This directly bounds what BugMine can promise.

Practitioner reporting is consistent with the research: enterprise SOC false-positive rates
"frequently exceed 50%, with some organizations reporting rates as high as 80%", and vendors
implementing reachability report alert reductions "up to 95%" on their own data.

The downstream effect is not merely annoyance. Alert fatigue produces a *crying wolf* dynamic in
which real findings are ignored because they are indistinguishable from the noise around them —
so a tool with a 92% false positive rate does not just waste time, it degrades the response to the
8% that matter.

## 2. The larger category of breakage is not reported at all

Security tooling reports security defects. Most of what breaks a working system is not a security
defect, and the research on dependency evolution is unambiguous:

| Finding | Figure |
| --- | --- |
| Maven packages that have violated semantic versioning at least once | **67%** |
| Dependency updates containing client-impacting breaking changes | **11.58%** |
| Client-impacting breaking changes arriving in a **non-major** version | **41.58%** |
| Leading cause of breaking changes | **Transitive** dependency changes |

Semantic versioning is the contract every dependency tool assumes. Two thirds of packages have
broken it, and four in ten breaking changes arrive where the contract promises there will be none
— in a patch or minor bump, exactly the upgrades teams automate and do not review.

### The finding that connects the two problems

> **70% of vulnerable dependencies require a minor or major version update that either
> potentially or actually breaks source code.**

This is the sharpest single fact in the research. The security scanner creates the ticket and is
**silent about what fixing it costs.** A team told to upgrade to remediate a CVE has a 70% chance
of absorbing a breaking change, and no tool in their stack will tell them which one, where it
lands, or whether it affects them. The remediation advice and the consequence of following it live
in two different information universes, and only one of them has a product.

## 3. AI-generated code is adding a new defect class

Package hallucination — models confidently importing packages that do not exist — is now measured
rather than anecdotal:

| Measurement | Figure |
| --- | --- |
| AI-suggested packages that do not exist (576,000-sample study) | **19.7%** |
| Open-source model average | **21.7%** |
| Commercial model average | **5.2%** |
| Worst measured families (CodeLlama configurations) | **>33%** |
| Best measured (GPT-4 Turbo) | **3.59%** |

The finding that turns this from a quality issue into a supply-chain attack: when identical
prompts were re-run ten times, **43% of hallucinated package names reappeared on every run.** The
hallucinations are *stable*, which makes them predictable, which makes them registrable by an
attacker — the practice now called slopsquatting.

It propagates the way you would expect. In early 2026 researchers tracked one hallucinated npm
name originating in 47 AI-generated agent skills in a single commit, spreading through forks to
**over 230 repositories** before anyone noticed.

> **Note on BugMine's own materials:** the infographic's "34% of AI-suggested package names are
> hallucinated or malicious" sits at the extreme of this range — it matches only the worst
> open-source model families, not the ~20% overall or the ~5% commercial figure. Using it as a
> general claim is not defensible.

## 4. Hosted LLMs break the assumption every supply-chain tool rests on

Every dependency tool assumes the same thing: **dependencies are versioned, and change is an
explicit act.** You pin, you review a diff, you upgrade deliberately.

Hosted LLM services violate this outright. Providers update weights, safety tuning, and serving
infrastructure without changing the endpoint or the model identifier. The dependency changes
underneath you and no artifact records it.

The measured consequences are severe:

- **GPT-4's direct code-execution success rate fell from 52% to 10% over three months**
  (March–June 2023) **with no version change.** A five-fold degradation in a load-bearing
  capability, invisible to every versioning mechanism in existence.
- Anthropic's August 2025 incident involved routing errors affecting **up to 16% of requests**
  plus output corruption injecting non-ASCII characters.
- Exploratory validation across model transitions found **structured JSON tasks showed higher
  drift** than SQL or authentication functions, and one backend function "passed all tests with
  Sonnet 4, but failed a test for safe encoding next day, suggesting silent infrastructure
  changes."

And the detection problem, stated by practitioners in terms that should sound familiar:

> "Large language model drift rarely announces itself — in most production systems, the model
> continues to respond, users continue to get answers, and **dashboards stay reassuringly green**
> with nothing obviously broken."

This is precisely the silent-failure shape BugMine's NFR-29 was written for, arrived at
independently.

**There is no crawlable artifact here.** No bug tracker, no changelog, no version bump. The only
signal a behavioral change generates is a measurement that used to pass and now does not — which
means the only way to know is to have been measuring beforehand.

## 5. What it costs when this fails

| Measure | Figure |
| --- | --- |
| Median hourly cost of IT downtime, mid-size and large enterprise (ITIC 2024) | **>$300,000/hr** |
| Large enterprises reporting $1M–$5M+ per hour | **41%** |
| Organizations whose most recent impactful outage cost >$1M (Uptime Institute 2026) | **1 in 5** |
| CrowdStrike outage cost to the Fortune 500, July 2024, over a few days | **$5.4B** |

The trend matters as much as the level: the Uptime Institute's 2026 analysis reports per-site
outage frequency falling for the **fifth consecutive year** while individual outages get more
expensive. Failures are rarer and each one hurts more — which shifts the value of prevention
upward and makes a *pre-incident* product more valuable relative to a faster-response one.

---

## What argues against all this

Deliberately included, because motivation documents that only argue one side are worth less than
nothing.

- **Reachability is being commoditized.** Endor Labs, Snyk, and Semgrep all ship reachability
  analysis and market it on exactly the alert-fatigue argument above. The 92% figure is a known
  problem under active attack by funded incumbents; BugMine will not be first.
- **The catalog's raw material is free.** NVD, OSV, and GitHub Advisory are public. The
  defensible part must be what BugMine adds — reachability, non-security bug classes, and
  original discovery — not aggregation.
- **LLM eval platforms already exist.** Langfuse, LangSmith, Braintrust, and Arize are
  established. The distinction is real but must be stated precisely: they evaluate *your
  application*, one customer at a time. None maintains a shared, versioned catalog of *model*
  defects across customers — but "nobody does this yet" is a hypothesis about demand, not proof
  of it.
- **The non-security bug catalog is unproven as a purchase.** Teams demonstrably buy security
  scanning because compliance requires it. Nothing requires them to buy breaking-change
  intelligence, and "obviously useful" is not the same as "budgeted".

---

## Sources

- [A Reality Check on SBOM-based Vulnerability Management: An Empirical Study and A Path Forward](https://arxiv.org/abs/2511.20313) — CODASPY '26; 2,414 repositories; 92.0% false positive rate; 61.9% pruned by function call analysis
- [Test Before You Deploy: Governing Updates in the LLM Supply Chain](https://arxiv.org/html/2604.27789v1) — GPT-4 52%→10% with no version change; Anthropic August 2025 incident; drift by task category
- [Semantic Versioning versus Breaking Changes: A Study of the Maven Repository](https://www.researchgate.net/publication/276269673_Semantic_Versioning_versus_Breaking_Changes_A_Study_of_the_Maven_Repository) — 67% semver violation
- [Automatically Fixing Dependency Breaking Changes](http://www0.cs.ucl.ac.uk/staff/j.krinke/publications/fse25.pdf) — 11.58% of updates; 41.58% in non-major versions
- [Dependency Updates: Version Bumps Don't Keep Code Secure — Moderne](https://moderne.ai/blog/security-dependency-updates-unmasked) — 70% of vulnerable dependencies require breaking updates
- [Slopsquatting: AI Code Hallucinations Fuel Supply Chain Risk — Cloud Security Alliance](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/04/CSA_research_note_slopsquatting-ai-supply-chain_20260419-csa-styled-1.pdf) — hallucination rates by model class; 43% reproduction
- [Slopsquatting: New AI Hallucination Threats & Mitigation Strategies — Snyk](https://snyk.io/articles/slopsquatting-mitigation-strategies/)
- [Who Vets AI's Code? The Scale Challenge Facing Open Source Ingestion — BleepingComputer](https://www.bleepingcomputer.com/news/security/who-vets-ais-code-the-scale-challenge-facing-open-source-ingestion/) — react-codeshift propagation
- [Cost of IT Downtime: $300K+/hr Median — OutageCost.com](https://outagecost.com/cost-of-it-downtime) — ITIC and Uptime Institute figures
- [How to Reduce Security Alerts and Eliminate Alert Fatigue — Endor Labs](https://www.endorlabs.com/learn/how-to-reduce-security-alerts-and-eliminate-alert-fatigue) — reachability as the largest false-positive driver
- [Monitoring LLM behavior: Drift, retries, and refusal patterns — VentureBeat](https://venturebeat.com/infrastructure/monitoring-llm-behavior-drift-retries-and-refusal-patterns)
- [Catching Silent LLM Degradation — Traceloop](https://www.traceloop.com/blog/catching-silent-llm-degradation-how-an-llm-reliability-platform-addresses-model-and-data-drift) — "dashboards stay reassuringly green"

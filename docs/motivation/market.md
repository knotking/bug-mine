# The Money

**Researched:** 2026-08-22. Market-sizing reports vary widely by firm and by how they draw
category boundaries; ranges are given rather than single figures where sources disagree, and
disagreement itself is reported.

---

## 1. The markets BugMine touches

| Market | 2026 size | Forecast | CAGR |
| --- | --- | --- | --- |
| **Software Composition Analysis** | $0.41B – $1.5B *(sources disagree by ~4×)* | $1.8B – $5.7B by 2033–2035 | 15.7% – 17.8% |
| **Application Security Testing** | ~$6.4B | $14.2B by 2034 | ~10.9% |
| **DevSecOps** | $11.07B | $26.05B by 2033 | 13.0% |
| **LLM observability** | **$2.69B** *(from $1.97B in 2025)* | **$9.26B by 2030** | **36.2%** |
| **AI observability** (broader) | $3.86B | $44.20B by 2035 | 31.1% |

The SCA spread — $412M at one firm, $1.5B at another for the same year — is worth noting rather
than averaging away. It reflects genuine disagreement about whether SCA is a product category or
a feature of a larger platform, which is itself a signal about how defensible a standalone
dependency scanner is.

## 2. The uncomfortable finding

**SCA is the smallest and slowest-growing market examined here.** LLM observability is roughly
2–6× larger depending on which SCA estimate you accept, and growing at **more than twice the
rate**.

That inverts the current build order. The scanner — the surface the positioning material calls the
product, and the one whose architecture took the most work — competes in the smallest, slowest
market, against funded incumbents (Snyk, Endor Labs, Semgrep) who already ship the reachability
analysis that is BugMine's differentiator there. The eval surface, which had five one-line
requirements until recently, addresses the fastest-growing market with no direct competitor for
what it specifically does.

Two adoption figures reinforce this:

- Gartner expects LLM observability investment to attach to **50% of GenAI deployments by 2028,
  up from 15% in early 2026** — a category tripling its attach rate inside two years.
- LangChain's State of Agent Engineering survey found **57% of respondents run agents in
  production**, with **~89% having implemented observability** for them. The buyers exist, are
  already spending, and have already accepted that this class of system needs monitoring.

The second figure is the more actionable one. It says the budget line already exists and the
purchasing argument has already been won by someone else — BugMine would be competing for an
established spend rather than creating one, which is a substantially easier sale than convincing
teams to fund breaking-change intelligence they have never bought before.

## 3. What the problem costs, which is the real ceiling

Market sizes measure what is currently spent on tools. The value at risk is larger by orders of
magnitude:

| | |
| --- | --- |
| Median IT downtime, mid-size and large enterprise | **>$300,000/hour** |
| Large enterprises reporting | **$1M–$5M+/hour** (41% of them) |
| One bad day | **$5.4B** to the Fortune 500 (CrowdStrike, July 2024) |

At $300k/hour, **a single prevented four-hour incident pays for a substantial annual contract.**
That is the value argument, and it is unusually easy to make — but only if the product can
credibly claim to have prevented something, which is exactly what a pre-incident product struggles
to prove and a post-incident one never has to.

The Uptime Institute trend sharpens this: outage frequency has fallen for five consecutive years
while per-incident cost has risen. Rarer, more expensive failures shift value toward prevention
and away from response — which favors the advisor and eval surfaces over the scanner, since both
act before the code exists rather than after.

## 4. Pricing implications from BugMine's own design

Three constraints from the architecture bear directly on the business model:

**Inference cost is cost of goods, and evals make it recurring without a customer request.** FR-53
re-runs evals indefinitely across tracked targets × suites × frequency. Nobody is billed for a
scheduled eval directly, and the spend scales with *catalog ambition* rather than with revenue —
structurally unlike every other cost in the system, which is why `metering.md` gives it a separate
budget line.

**Per-tenant inference tiers mean the cheap tier is also the weak one.** NFR-21 lets a tenant pin
to self-hosted inference. That tenant gets materially worse findings, which means quality varies
by price point in a way the pricing page has to be honest about.

**Self-hosting the catalog is continuous distribution, not a software licence.** NFR-35: the
catalog *is* the product, so a self-hosted deployment must keep receiving it. That is a
subscription with a delivery mechanism and a revocation story, not a perpetual licence.

## 5. Where BugMine would actually sit

There is no established category for "versioned catalog of non-security defects across an entire
stack". The nearest neighbours are all partial:

| Category | Overlaps | Does not do |
| --- | --- | --- |
| SCA (Snyk, Endor Labs) | Dependency vulnerabilities, reachability | Non-security bug classes; LLM models; SaaS behavior changes |
| LLM observability (Langfuse, Arize, Braintrust) | Model behavior measurement | Shared cross-customer catalog; anything outside the AI stack |
| Status/incident (Statuspage aggregators) | Live outages | Anything not currently on fire |
| Upgrade tooling (Dependabot, Renovate) | Version bumps | What the bump will break |

Being between categories cuts both ways honestly: there is no incumbent to displace, and there is
no budget line to land in. The pragmatic reading is that **the entry point should be a category
that already has budget**, with the catalog as the thing that makes it better — and on the numbers
above, that entry point is more likely the AI stack than the dependency scanner.

---

## Sources

- [Software Composition Analysis Market Size, Trends & Forecast 2026–2033 — Coherent Market Insights](https://www.coherentmarketinsights.com/market-insight/software-composition-analysis-market-2078)
- [Software Composition Analysis (SCA) Software Market — Verified Market Reports](https://www.verifiedmarketreports.com/product/software-composition-analysis-sca-software-market/)
- [Global Software Composition (SCA) Tools Market 2026–2036 — MarkWide Research](https://markwideresearch.com/global-software-composition-sca-tools-market)
- [Application Security Testing (AST) Tools Market Size Overview, 2035 — Business Research Insights](https://www.businessresearchinsights.com/market-reports/application-security-testing-ast-tools-market-105941)
- [DevSecOps Market Size, Trends & Forecast 2026–2033 — Coherent Market Insights](https://www.coherentmarketinsights.com/industry-reports/devsecops-market)
- [Large Language Model (LLM) Observability Platform Market Report 2026 — Research and Markets](https://www.researchandmarkets.com/reports/6215671/large-language-model-llm-observability)
- [AI Observability Market Size & Share Analysis, 2035 — Next Move Strategy Consulting](https://www.nextmsc.com/report/ai-observability-market-ic5403)
- [Top LLM Observability and Evaluation Platforms in 2026 — MarkTechPost](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) — Gartner 50%-by-2028 projection; LangChain agent-adoption survey
- [Cost of IT Downtime: $300K+/hr Median — OutageCost.com](https://outagecost.com/cost-of-it-downtime)

# Why BugMine

Research-backed motivation for the product: what is wrong with the tooling today, where it is
heading, what the money looks like, and what BugMine does about it.

**Researched:** 2026-08-22. Every statistic is attributed; where a claim could not be sourced,
that is stated rather than omitted.

| Document | Contents |
| --- | --- |
| [`problem.md`](problem.md) | The evidence that current tooling is the wrong shape — measured false-positive rates, breaking-change data, AI-generated code defects, LLM behavioral drift |
| [`trajectory.md`](trajectory.md) | Where this goes over the next three years, and why the gap widens rather than closes |
| [`market.md`](market.md) | Market sizes and growth rates, and the uncomfortable finding about which of them BugMine's flagship surface competes in |
| [`solution.md`](solution.md) | What BugMine offers, mapped line by line to the evidence — including where the evidence bounds the solution |
| [`blog.md`](blog.md) | **The union of every document in `docs/` in one narrative piece** — problem, market, product, taxonomy, discovery, architecture, decisions, and open questions. Start here if you only read one thing. |

---

## Executive summary

**The measured problem is worse than the pitch claims.** A peer-reviewed study of 2,414
repositories found downstream vulnerability scanners produce a **92.0% false positive rate**, and
identified the primary cause as flagging vulnerabilities in code that is never reached. The
infographic's "90.5% non-exploitable" figure understates it slightly.

**The bigger problem is the one nothing reports at all.** 67% of Maven packages have violated
semantic versioning; 41.58% of client-impacting breaking changes arrive in *non-major* upgrades.
And the finding that ties the two together: **70% of vulnerable dependencies require a minor or
major update that potentially or actually breaks source code.** Fixing a security issue means
absorbing a breaking change nobody warned you about — the security tool creates the ticket and is
silent about what it costs.

**A whole dependency class has no versioning discipline at all.** Hosted LLMs break the assumption
every supply-chain tool is built on. GPT-4's direct code-execution success rate fell from 52% to
10% over three months **with no version change**. There is no changelog to crawl and no version to
pin, so the only signal such a change generates is a measurement that used to pass and now does
not.

**The money is real but not where the pitch points.** SCA — where a dependency scanner competes —
is the smallest and slowest-growing market examined here, roughly $0.4–1.5B in 2026. LLM
observability is **$2.69B growing at 36.2% CAGR**, and Gartner expects observability attached to
50% of GenAI deployments by 2028, up from 15% in early 2026. BugMine's least-designed surface
addresses the fastest-growing market.

**What it costs when this fails:** median IT downtime exceeds $300,000 per hour for mid-size and
large enterprises; 41% of large enterprises report $1M–$5M+ per hour.

## Verdict on the infographic's claims

Earlier flagged as unsourced. After research:

| Claim | Verdict |
| --- | --- |
| 90.5% of flagged vulnerabilities are non-exploitable | **Corroborated, and conservative.** Peer-reviewed measurement puts scanner false positives at 92.0% |
| 34% of AI-suggested package names are hallucinated or malicious | **High-end cherry-pick.** Broad studies find ~19.7% overall; 21.7% for open-source models vs 5.2% commercial. Only the worst open-source model families exceed 33% |
| 75% of breakages are non-security issues | **Not sourced.** Directionally supported by the breaking-change data but the specific figure could not be verified — do not use it publicly as stated |
| 2,500% projected increase in AI-introduced defects | **Not sourced.** Could not be verified; recommend dropping it |

Two of four hold up. The strongest available number — 92.0%, from a peer-reviewed study on 2,414
repositories — is better than anything currently in the pitch, and it comes with a citation.

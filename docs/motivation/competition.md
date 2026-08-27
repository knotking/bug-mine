# The Agents

**Researched:** 2026-08-27. Every coding agent worth naming now ships vulnerability scanning.
This document records what each one actually covers, measured against BugMine surface by surface,
and states where BugMine loses.

[`market.md`](market.md) asks which market BugMine competes in. This asks a narrower and more
urgent question: whether the coding agent sitting in the developer's terminal has already
absorbed the product.

---

## 1. What shipped

Between March and July 2026 three of the largest engineering organisations in the industry
shipped agent-native security scanning. None of it existed when [`solution.md`](solution.md) named
Snyk, Endor Labs and Semgrep as the incumbents to beat.

| | Shipped | What it does | Scope |
| --- | --- | --- | --- |
| **OpenAI Codex Security** | Preview 6 Mar 2026; CLI + SDK open-sourced 29 Jul 2026 | Scans commit-by-commit, builds a project-specific threat model, **validates each finding in an isolated sandbox**, opens a patch PR | First-party code. Security only |
| **Gemini CLI security extension** | 2026 | `/security:analyze` for diff SAST; `/security:scan-deps` wrapping **OSV-Scanner against OSV.dev**. Claimed 90% precision / 93% recall on the OpenSSF CVE benchmark | Dependencies + first-party. Security only |
| **GitHub Copilot** | Agentic autofix public preview 10 Jul 2026 | Explores the codebase, patches, reruns the analysis to confirm the alert closes, opens a PR | Code-scanning alerts only — Dependabot and secret scanning are separate alert families |
| **Claude Code** | — | No dependency scanner. `/security-review` and `/code-review` are scoped to the pending diff. Has a shell and a research loop | First-party code, plus ad-hoc research |
| **OpenHands · Cline · Goose · Aider · OpenCode** | — | Harnesses. No catalog; all shell out; most speak MCP | Whatever is on `PATH` |

Codex Security scanned **1.2M commits in its first 30 days**, surfacing 792 critical and 10,561
high-severity findings, including in Chromium and OpenSSL. This is not a demo.

**The open-source agents are not competitors.** They are the surface BugMine's MCP server plugs
into. Treat them as distribution.

## 2. Coverage, surface by surface

| Capability | Claude Code | Codex Security | Gemini CLI | GitHub | OSS agents | **BugMine** |
| --- | --- | --- | --- | --- | --- | --- |
| Dependency inventory from lockfile | shell | no | yes | yes | shell | **yes** |
| CVE / advisory findings | shell | no | yes | yes | shell | **yes** |
| Deprecations & breaking changes | ad hoc | no | no | no | ad hoc | **yes** |
| Performance & functional regressions | ad hoc | no | no | no | ad hoc | **yes** |
| Reachability suppression | no | no | no | first-party | no | **4 langs** |
| Findings validated by execution | no | **yes** | no | rerun | no | *evals only* |
| Model behaviour drift | no | no | no | no | no | *no scheduler* |
| Cross-customer corroboration | no | no | no | no | no | *never run* |
| Subscribe / notify over time | no | CI | no | yes | no | **yes** |
| Durable, dated record of every finding | no | scan history | no | yes | no | **yes** |
| Triage state that persists across scans | no | no | no | yes | no | *not built* |
| Advice before code exists | native | threat model | no | no | native | *contested* |
| Every finding cites a record | no | partial | OSV id | CVE id | no | **enforced** |
| Bundled where developers already are | yes | yes | yes | yes | n/a | **no** |

**Every one of them is security-only.** Not one covers deprecations, breaking changes, or
functional and performance regressions — which is 20,131 of the catalog's 24,286 records. The
differentiator [`solution.md`](solution.md) §2 calls "the genuine one" is confirmed, from the
adversarial direction.

## 3. Four rows BugMine loses

**Findings validated by execution.** Codex Security sandbox-validates a finding before reporting
it. That is empirical proof rather than inference, and it is the mechanism
[`solution.md`](solution.md) §4 identifies as the eval surface's moat — arrived at independently,
shipped first, at a scale BugMine will not reach. BugMine's equivalent runs only in evals, and
FR-53 has no scheduler, so it does not run at all.

**Advice before code exists.** The advisor is the most exposed surface in the product. It is
native LLM territory: no lockfile to resolve, no artifact to cite, and the agent already holds
the user's entire design in context while [ADR-0001](../adr/0001-advisor-input-normalization.md)
gives BugMine a Stack Profile distilled from it. Being strictly downstream of something that
knows more about the user's intent than the input format can carry is a structural disadvantage,
not a gap to close. Grounding is the one defence — [ADR-0002](../adr/0002-grounding-and-provenance.md)
means every advisor warning cites a record, and an agent's answer cites nothing.

**Triage state.** Dependabot alerts carry open, dismissed and fixed, with a dismissal reason
and the actor who set it, rolled up across every repository in the organisation. BugMine has no
disposition column at all, and `finding` is keyed to `scan_id`, so nothing persists between
scans. This is the row most worth losing loudly, because it is also by far the cheapest to win
— see §6.

**Distribution.** Gemini's dependency scan is one command in an already-installed CLI. GitHub's
runs inside the repository host. BugMine needs a URL, an API key and a decision. For a product
nothing compels anyone to adopt, distribution is not a gap alongside the others — it is the one
that decides whether the others are ever seen.

## 4. Two rows nothing contests

**Model behaviour drift.** No agent, no scanner and no advisory database records that a hosted
model changed underneath a stable identifier. There is no changelog, no CVE and no version diff,
so retrieval cannot reach it — only measurement can. Six months of well-funded competitive
shipping did not touch this. It remains what [`solution.md`](solution.md) §4 claims it is.

**Cross-customer corroboration.** GitHub holds the data to do this and does not. It is the second
proprietary origin in [`discovery.md`](../requirements/discovery.md), and
[ADR-0004](../adr/0004-scan-derived-catalog-entries.md) makes it safe.

**Both are listed in the README under *What is not built*.** The two defensible origins are
unshipped; the most commoditizable surface is live. The build order is inverted.

## 5. How fast could they close it

The question is not whether a model vendor *could* replicate BugMine. It is how long each piece
would take, and the answer is uneven enough that the unevenness is itself the strategy.

| Capability | Time for an agent vendor | Why |
| --- | --- | --- |
| Dependency scanning | **weeks** | Bundle OSV-Scanner behind a skill — precisely what Gemini did. A third-party plugin needs no roadmap decision at all |
| Ad-hoc breakage research | **shipped** | Works today in any agent with web access |
| Reachability suppression | **a quarter** | An agent reads code natively. Lockfile → OSV hits → "does this repository call the affected symbol" yields respectable suppression with no static analysis whatever |
| The non-CVE catalog | **off-strategy** | 24,286 versioned, cited, queryable records is a data-operations business. OpenAI and Anthropic sell models, which is why neither runs a CVE database either |
| Shared multi-tenant state | **a product line** | A local CLI process becomes a backend with tenancy, auth, retention and compliance. Defensible against the agents; not against GitHub, which already has all of it |
| Model drift measurement | **structurally impossible** | See below |
| Cross-tenant corroboration | **blocked by commitment** | Vendors see the code; their privacy posture forbids pooling it |

**Independence is the moat, not effort.** A model vendor publishing the finding that its own model
degraded is a conflict of interest, not an engineering problem — the same reason auditors are not
employed by the firms they audit. No amount of investment closes that gap. It is the only claim in
this document with that property, and it is a stronger form of the argument
[`solution.md`](solution.md) §4 makes on temporal grounds alone: even a competitor who *had* been
measuring from the beginning could not credibly publish the result.

**But the exposed asset is the demo, not the product.** A competitor does not need the hard half to
do damage. If any agent ships a competent dependency skill with model-judged reachability, the
75%-suppression-on-`poetry` opener stops looking differentiated — while the catalog underneath it
still is. That opener is what gets meetings, and it is the most replicable thing in the system.

This reorders §8 rather than contradicting it. The ledger and the drift feed are not merely the
defensible surfaces; they are the parts of a demonstration that **cannot be reproduced in a
terminal during the meeting where they are shown**.

## 6. State is the enterprise argument

The strongest claim available is also the only one that runs on what is **already built**.
`scan` and `finding` are persisted per tenant with repo, commit SHA, timestamp and the citation
that grounds each one, alongside the honest counters — `uncovered_components`,
`unresolved_manifests`, `suppressed_unreachable`. That is a durable, queryable, auditable record.
An agent's answer lives in one developer's terminal scrollback and is gone.

Two corrections before this goes in a deck, because in its loose form it loses to the first
technical buyer who pushes.

**"Agents don't remember" is false.** Claude Code has `CLAUDE.md` and a persistent memory
directory. What no agent has is *shared, queryable, multi-tenant, auditable* state — per-developer
markdown on one laptop is not a system of record. Claim that, not amnesia.

**The competitor here is GitHub, not Claude Code.** Dependabot alerts already carry
open / dismissed / fixed state, a dismissal reason, an actor, and an org-wide rollup in the
security overview — bundled, and free on public repositories. So "we have state and they don't"
loses on contact. The claim that survives is narrower:

> GitHub gives you a triage ledger for CVEs. Nothing gives you one for the 82% that isn't a CVE.

What that buys, stated as claims rather than features:

- **A finding is an asset, not an answer** — dated, cited, attributable to a commit. Ask an agent
  twice and you get two answers and no evidence either was asked.
- **Triage once, not every week.** Teams abandon scanners over re-litigating the same 200 findings
  every Monday, not over the first scan.
- **Exposure across the estate.** "How many of our 400 services are on the affected version" is one
  query against a catalog, and 400 agent sessions otherwise.
- **The record compounds and cannot be bought later** — the same temporal property
  [`solution.md`](solution.md) §4 claims for evals, but over the surface that already works.
- **Answers what we knew and when.** A dated finding with a citation survives an audit; a chat
  transcript does not.

**Two gaps stand between this and a demo.** There are no dispositions — `feedback.md` specifies
`not applicable` against `wrong`, and FR-62 measures precision by origin, but `disposition` appears
nowhere in `src/` or `tests/`. And `finding` is keyed to `scan_id`, so the same problem on two
commits is two unrelated rows, with no way to express *still open*, *recurred* or *fixed on 14 Aug*.

Today this is **scan history, not issue tracking**. Closing it is a stable finding key and a status
column — small, well-scoped, no research risk — and it converts a capability that already exists
into the enterprise pitch. Unlike the eval scheduler and cross-tenant corroboration, nothing about
it is blocked on scale.

## 7. The uncomfortable finding

[`market.md`](market.md) §2 has one: SCA is the smallest and slowest market BugMine touches. This
is the second, and it points the same way.

Six months of aggressive shipping by OpenAI, Google and GitHub — effectively unlimited budget,
every incentive to expand scope — and not one added deprecations or breaking changes. The
generous reading is a wide-open category. The honest reading is that the category may be empty
for a reason.

Security scanning exists because of forces unrelated to usefulness: CVE identifiers make findings
addressable, CVSS makes them rankable, SOC 2 and FedRAMP make them mandatory, and someone owns a
budget line for them. Dependency breakage has no identifier scheme, no severity standard, no
auditor asking after it and no budget owner. Teams absorb it as ordinary engineering friction.

This is assumption 1 in [`solution.md`](solution.md)'s *What we would need to be true* — "teams
will pay for non-security bug intelligence" — and this research is the strongest evidence yet
that it is the assumption the business rests on. Nothing in the product can settle it. Only a
renewal can.

## 8. What it changes

- **Do not compete on scanning.** The commodity layer is now free and bundled in three CLIs. A
  better wrapper around OSV is not a business.
- **A stable finding key and a status column are the cheapest unbuilt thing**, and the only one
  that turns an existing capability into a pitch without waiting for scale (§6).
- **The scheduler is the highest-value unbuilt thing.** Drift detection without periodic re-runs
  is a diagram, not a capability, and it is the only row on the board with no occupant.
- **Sell measurement, not retrieval.** Everything retrievable is being retrieved for free by
  something else. "We ran the experiment" survives contact with agents; "we have a bigger list"
  does not.
- **Ship into the harnesses.** MCP into Claude Code, Goose, Cline and OpenHands. They have
  distribution and no facts of their own; BugMine has the inverse.
- **Test willingness to pay for breakage before building more of it.** That is now the
  highest-leverage unknown in the business, and it is a sales question rather than an
  engineering one.

Independently of [`market.md`](market.md), which reached the same conclusion from market sizing,
this points at the eval surface rather than the scanner. Two arguments from unrelated evidence
converging is worth more than either alone.

## 9. What argues against this document

- **Six months is a short window.** Absence of a competitor's feature in one release cycle is
  weak evidence about a roadmap. OpenAI and Google may both be building toward breakage
  intelligence and simply have not shipped it.
- **The reachability comfort in this document is too generous.** §2 records that no agent bundles
  reachability, which reads as a durable gap and is not one. Nobody has bundled it because nobody
  has bothered — an agent that reads the code can approximate the verdict from a prompt, with none
  of the per-language symbol analysis in `reach/`. Those modules are a real asset defending a line
  that is cheap to cross.
- **Codex Security's sandbox validation is for security findings.** Extending it to "will this
  upgrade break my build" is a substantially harder problem than the current one, and treating
  the extension as inevitable overstates the threat.
- **The system-of-record argument is partly an argument for Jira.** Enterprises already track
  issues somewhere, and a buyer will reasonably ask why this is another console rather than an
  integration into the ledger they own. §6 does not answer that.
- **The capability matrix is compiled from vendor documentation and press coverage**, not from
  running each tool against a common corpus. It records claimed scope. A measured comparison
  would be better and does not exist yet.

---

## Sources

- [Codex Security: now in research preview — OpenAI](https://openai.com/index/codex-security-now-in-research-preview/)
- [OpenAI Rolls Out Codex Security Vulnerability Scanner — SecurityWeek](https://www.securityweek.com/openai-rolls-out-codex-security-vulnerability-scanner/)
- [OpenAI Open-Sources Codex Security CLI to Find, Validate, and Fix Code Vulnerabilities — GBHackers](https://gbhackers.com/openai-open-sources-codex-security-cli-to-find-vulnerabilities/)
- [OpenAI's Daybreak uses Codex Security to identify risky attack paths — Help Net Security](https://www.helpnetsecurity.com/2026/05/12/openai-daybreak-openai-daybreak-vulnerability-validation-initiative/)
- [gemini-cli-extensions/security — Google](https://github.com/gemini-cli-extensions/security)
- [Agentic autofix for code scanning alerts in public preview — GitHub Changelog](https://github.blog/changelog/2026-07-10-agentic-autofix-for-code-scanning-alerts-in-public-preview/)
- [About Copilot Autofix for code scanning — GitHub Docs](https://docs.github.com/en/code-security/concepts/code-scanning/copilot-autofix-for-code-scanning)
- [9 Open-Source AI Coding Agents Worth Self-Hosting — Security Boulevard](https://securityboulevard.com/2026/06/9-open-source-ai-coding-agents-worth-self-hosting/)

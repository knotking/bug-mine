# SaaS status sources

**Status:** List, probed and not yet registered
**Date:** 2026-08-24
**Feeds:** [`saas-status-sources.json`](saas-status-sources.json)
**Relates to:** [`ingestion-sources.md`](ingestion-sources.md) step 5, `saas_platform` domain

---

## Why this list changes the ingestion plan

[`ingestion-sources.md`](ingestion-sources.md) put SaaS behind the model path, on the assumption
that a status page is prose and therefore costs tokens. **That was wrong for most of it.**

Of 59 vendors probed, **46 expose a machine-readable feed** — 38 of them through Atlassian
Statuspage's `/api/v2/incidents.json`, which returns incident history as structured JSON. Statuspage is close to a monopoly in this category, and one parser reads all
38. The three hyperscalers each publish their own feed, and a handful more publish Atom, RSS or
a bespoke JSON API.

So `saas_platform` is mostly a **deterministic** domain, not a model-path one, and it moves up
the order accordingly. The model path is needed only for the 13 vendors below that publish a
status page with no feed behind it.

Every URL in this document returned HTTP 200 with the content type its kind implies, probed on
2026-08-24. None of them are recalled from memory — a hallucinated status URL fails by silently
monitoring nothing, which is the failure mode this catalog exists to argue against.

**Use `incidents.json`, not `summary.json`.** Verified against GitHub's page on 2026-08-24:
`summary.json` returned **0 incidents** because nothing was wrong at the time — it reports only
*currently active* incidents. `incidents.json` returned 50, all resolved, each with
`started_at`, `resolved_at`, `impact`, `status` and the full `incident_updates` trail. A feed
that is empty whenever the vendor is healthy would have looked like a working integration that
never found anything.

## What a status feed is worth to the catalog

The `saas_platform` domain holds one record today. It is also the domain where the applicability
union's `TimeWindow` variant exists — a SaaS vendor ships no version, so a behaviour change is
bounded by dates and nothing else. An incident feed is exactly a stream of dated windows with a
start, an end and an impact, which is the only source shaped like `TimeWindow` we have found.

Worth stating before anyone builds it: **an outage is not a bug record.** A three-hour regional
incident that is resolved and never recurs does not belong in a catalog of defects a design will
hit. What belongs is the *durable* subset — a behaviour change announced through the status
page, a degradation that recurs, a deprecation, an incident whose postmortem describes a
lasting change. Ingesting every incident indiscriminately would fill the catalog with resolved
noise and make the SaaS domain the least trustworthy one in it.

## Feeds confirmed (46)

| Component | Kind | Feed |
| --- | --- | --- |
| `adyen` | Statuspage v2 JSON | `https://status.adyen.com/api/v2/incidents.json` |
| `airtable` | Statuspage v2 JSON | `https://status.airtable.com/api/v2/incidents.json` |
| `atlassian` | Statuspage v2 JSON | `https://status.atlassian.com/api/v2/incidents.json` |
| `box` | Statuspage v2 JSON | `https://status.box.com/api/v2/incidents.json` |
| `circleci` | Statuspage v2 JSON | `https://status.circleci.com/api/v2/incidents.json` |
| `cloudflare` | Statuspage v2 JSON | `https://www.cloudflarestatus.com/api/v2/incidents.json` |
| `confluent` | Statuspage v2 JSON | `https://status.confluent.cloud/api/v2/incidents.json` |
| `datadog` | Statuspage v2 JSON | `https://status.datadoghq.com/api/v2/incidents.json` |
| `digitalocean` | Statuspage v2 JSON | `https://status.digitalocean.com/api/v2/incidents.json` |
| `discord` | Statuspage v2 JSON | `https://discordstatus.com/api/v2/incidents.json` |
| `dropbox` | Statuspage v2 JSON | `https://status.dropbox.com/api/v2/incidents.json` |
| `elastic` | Statuspage v2 JSON | `https://status.elastic.co/api/v2/incidents.json` |
| `figma` | Statuspage v2 JSON | `https://status.figma.com/api/v2/incidents.json` |
| `flyio` | Statuspage v2 JSON | `https://status.flyio.net/api/v2/incidents.json` |
| `github` | Statuspage v2 JSON | `https://www.githubstatus.com/api/v2/incidents.json` |
| `hubspot` | Statuspage v2 JSON | `https://status.hubspot.com/api/v2/incidents.json` |
| `jfrog` | Statuspage v2 JSON | `https://status.jfrog.io/api/v2/incidents.json` |
| `linode` | Statuspage v2 JSON | `https://status.linode.com/api/v2/incidents.json` |
| `mailgun` | Statuspage v2 JSON | `https://status.mailgun.com/api/v2/incidents.json` |
| `mongodb` | Statuspage v2 JSON | `https://status.mongodb.com/api/v2/incidents.json` |
| `netlify` | Statuspage v2 JSON | `https://www.netlifystatus.com/api/v2/incidents.json` |
| `newrelic` | Statuspage v2 JSON | `https://status.newrelic.com/api/v2/incidents.json` |
| `npm` | Statuspage v2 JSON | `https://status.npmjs.org/api/v2/incidents.json` |
| `openai` | Statuspage v2 JSON | `https://status.openai.com/api/v2/incidents.json` |
| `plaid` | Statuspage v2 JSON | `https://status.plaid.com/api/v2/incidents.json` |
| `planetscale` | Statuspage v2 JSON | `https://www.planetscalestatus.com/api/v2/incidents.json` |
| `pypi` | Statuspage v2 JSON | `https://status.python.org/api/v2/incidents.json` |
| `redis` | Statuspage v2 JSON | `https://status.redis.io/api/v2/incidents.json` |
| `render` | Statuspage v2 JSON | `https://status.render.com/api/v2/incidents.json` |
| `replicate` | Statuspage v2 JSON | `https://www.replicatestatus.com/api/v2/incidents.json` |
| `sendgrid` | Statuspage v2 JSON | `https://status.sendgrid.com/api/v2/incidents.json` |
| `sentry` | Statuspage v2 JSON | `https://status.sentry.io/api/v2/incidents.json` |
| `shopify` | Statuspage v2 JSON | `https://www.shopifystatus.com/api/v2/incidents.json` |
| `snowflake` | Statuspage v2 JSON | `https://status.snowflake.com/api/v2/incidents.json` |
| `squareup` | Statuspage v2 JSON | `https://www.issquareup.com/api/v2/incidents.json` |
| `supabase` | Statuspage v2 JSON | `https://status.supabase.com/api/v2/incidents.json` |
| `twilio` | Statuspage v2 JSON | `https://status.twilio.com/api/v2/incidents.json` |
| `vercel` | Statuspage v2 JSON | `https://www.vercel-status.com/api/v2/incidents.json` |
| `google-cloud` | Vendor JSON | `https://status.cloud.google.com/incidents.json` |
| `heroku` | Vendor JSON | `https://status.heroku.com/api/v4/current-status` |
| `slack` | Vendor JSON | `https://slack-status.com/api/v2.0.0/current` |
| `anthropic` | Atom | `https://status.anthropic.com/history.atom` |
| `twitch` | Atom | `https://status.twitch.tv/history.atom` |
| `zoom` | Atom | `https://status.zoom.us/history.atom` |
| `aws` | RSS | `https://status.aws.amazon.com/rss/all.rss` |
| `azure` | RSS | `https://azure.status.microsoft/en-us/status/feed/` |

## Status page live, no feed found (13)

These resolve but the probe found no machine-readable endpoint. Each needs a manual check
before being written off to the model path — several are Statuspage-hosted behind a WAF that
answered the API path with HTML rather than JSON, and would be free if the right URL is found.

| Component | Status page |
| --- | --- |
| `auth0` | https://status.auth0.com/ |
| `databricks` | https://status.databricks.com/ |
| `dockerhub` | https://www.dockerstatus.com/ |
| `fastly` | https://status.fastly.com/ |
| `gitlab` | https://status.gitlab.com/ |
| `huggingface` | https://status.huggingface.co/ |
| `notion` | https://status.notion.so/ |
| `okta` | https://status.okta.com/ |
| `pagerduty` | https://status.pagerduty.com/ |
| `paypal` | https://www.paypal-status.com/ |
| `postmark` | https://status.postmarkapp.com/ |
| `stripe` | https://status.stripe.com/ |
| `zendesk` | https://status.zendesk.com/ |

## Measured: these feeds carry almost nothing the catalog wants

The parser is built (`worker/structured.py`, `looks_like_statuspage` / `extract_statuspage`)
and wired into the extract worker's auto-detection beside the GitHub releases reader. It was
then run against real feeds, and the result is the most useful thing in this document.

**551 settled incidents across twelve vendors produced zero records.**

| | |
| --- | :-: |
| Vendors sampled | 12 |
| Settled incidents read | 551 |
| Durable-change records produced | **0** |

The first pass, with durable markers only, matched three — and two were false positives. npm's
*"Failures publishing, deprecating, and installing packages"* is an outage of the deprecate
command, not a deprecation; MongoDB's *"Temporary failures"* matched a word in an update body.
Adding a transient veto (`outage`, `degraded`, `elevated`, `temporary`, `latency`, …) removed
both, and removed the third with them.

### What that means, and what it invalidates

**Status pages do not announce durable changes.** They carry transient operational state, by
design and by the vendors' own convention. Deprecations, breaking changes and behaviour shifts
are announced in changelogs, API-version pages, blogs and email — not on the status page.

So the correction this document made to [`ingestion-sources.md`](ingestion-sources.md) was half
right. The feeds *are* structured and free, and the parser costs nothing to run. But moving the
`saas_platform` domain up the order on the strength of them was wrong: they are a cheap source
of almost no catalog records. The original judgement — that SaaS durable changes need the model
path over prose changelogs — stands, against different sources than the ones listed here.

### Where these 46 feeds do belong

BugMine's scope is a catalog of known bugs **and live outages**. These feeds are an excellent
source of the second and a poor source of the first. A live-outage surface reading
`incidents.json` on a short interval is a different product surface with a different data
shape — current state, not versioned history — and it is worth building on exactly this list.

The parser stays and stays wired: it costs nothing, and it catches a genuine announcement if a
vendor ever makes one through this channel. Its expected yield is approximately zero, which is
recorded in `tests/test_structured.py` so that an empty result is never mistaken for a broken
integration.

## Open decisions

| # | Decision |
| --- | --- |
| ~~S1~~ | **Decided and implemented.** A settled incident whose text carries a durable-change marker and no transient vocabulary. Measured yield: zero records from 551 incidents, which is the rule working rather than failing |
| **S2** | Whether an unresolved incident enters the catalog at all, or only its postmortem does. Entering early means the record is written before anyone knows what changed |
| **S3** | Polling interval per vendor — now a question for the live-outage surface rather than the catalog, since the catalog yield is zero |
| **S4** | Whether component refs match how a customer names the dependency (`gcp` versus `google-cloud` versus `Google Cloud Platform`), which is the alias registry's problem and is worse here than anywhere else |

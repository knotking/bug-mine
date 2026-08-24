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
Statuspage's `/api/v2/summary.json`, which returns incidents, components and impact as
structured JSON. Statuspage is close to a monopoly in this category, and one parser reads all
38. The three hyperscalers each publish their own feed, and a handful more publish Atom, RSS or
a bespoke JSON API.

So `saas_platform` is mostly a **deterministic** domain, not a model-path one, and it moves up
the order accordingly. The model path is needed only for the 13 vendors below that publish a
status page with no feed behind it.

Every URL in this document returned HTTP 200 with the content type its kind implies, probed on
2026-08-24. None of them are recalled from memory — a hallucinated status URL fails by silently
monitoring nothing, which is the failure mode this catalog exists to argue against.

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
| `adyen` | Statuspage v2 JSON | `https://status.adyen.com/api/v2/summary.json` |
| `airtable` | Statuspage v2 JSON | `https://status.airtable.com/api/v2/summary.json` |
| `atlassian` | Statuspage v2 JSON | `https://status.atlassian.com/api/v2/summary.json` |
| `box` | Statuspage v2 JSON | `https://status.box.com/api/v2/summary.json` |
| `circleci` | Statuspage v2 JSON | `https://status.circleci.com/api/v2/summary.json` |
| `cloudflare` | Statuspage v2 JSON | `https://www.cloudflarestatus.com/api/v2/summary.json` |
| `confluent` | Statuspage v2 JSON | `https://status.confluent.cloud/api/v2/summary.json` |
| `datadog` | Statuspage v2 JSON | `https://status.datadoghq.com/api/v2/summary.json` |
| `digitalocean` | Statuspage v2 JSON | `https://status.digitalocean.com/api/v2/summary.json` |
| `discord` | Statuspage v2 JSON | `https://discordstatus.com/api/v2/summary.json` |
| `dropbox` | Statuspage v2 JSON | `https://status.dropbox.com/api/v2/summary.json` |
| `elastic` | Statuspage v2 JSON | `https://status.elastic.co/api/v2/summary.json` |
| `figma` | Statuspage v2 JSON | `https://status.figma.com/api/v2/summary.json` |
| `flyio` | Statuspage v2 JSON | `https://status.flyio.net/api/v2/summary.json` |
| `github` | Statuspage v2 JSON | `https://www.githubstatus.com/api/v2/summary.json` |
| `hubspot` | Statuspage v2 JSON | `https://status.hubspot.com/api/v2/summary.json` |
| `jfrog` | Statuspage v2 JSON | `https://status.jfrog.io/api/v2/summary.json` |
| `linode` | Statuspage v2 JSON | `https://status.linode.com/api/v2/summary.json` |
| `mailgun` | Statuspage v2 JSON | `https://status.mailgun.com/api/v2/summary.json` |
| `mongodb` | Statuspage v2 JSON | `https://status.mongodb.com/api/v2/summary.json` |
| `netlify` | Statuspage v2 JSON | `https://www.netlifystatus.com/api/v2/summary.json` |
| `newrelic` | Statuspage v2 JSON | `https://status.newrelic.com/api/v2/summary.json` |
| `npm` | Statuspage v2 JSON | `https://status.npmjs.org/api/v2/summary.json` |
| `openai` | Statuspage v2 JSON | `https://status.openai.com/api/v2/summary.json` |
| `plaid` | Statuspage v2 JSON | `https://status.plaid.com/api/v2/summary.json` |
| `planetscale` | Statuspage v2 JSON | `https://www.planetscalestatus.com/api/v2/summary.json` |
| `pypi` | Statuspage v2 JSON | `https://status.python.org/api/v2/summary.json` |
| `redis` | Statuspage v2 JSON | `https://status.redis.io/api/v2/summary.json` |
| `render` | Statuspage v2 JSON | `https://status.render.com/api/v2/summary.json` |
| `replicate` | Statuspage v2 JSON | `https://www.replicatestatus.com/api/v2/summary.json` |
| `sendgrid` | Statuspage v2 JSON | `https://status.sendgrid.com/api/v2/summary.json` |
| `sentry` | Statuspage v2 JSON | `https://status.sentry.io/api/v2/summary.json` |
| `shopify` | Statuspage v2 JSON | `https://www.shopifystatus.com/api/v2/summary.json` |
| `snowflake` | Statuspage v2 JSON | `https://status.snowflake.com/api/v2/summary.json` |
| `squareup` | Statuspage v2 JSON | `https://www.issquareup.com/api/v2/summary.json` |
| `supabase` | Statuspage v2 JSON | `https://status.supabase.com/api/v2/summary.json` |
| `twilio` | Statuspage v2 JSON | `https://status.twilio.com/api/v2/summary.json` |
| `vercel` | Statuspage v2 JSON | `https://www.vercel-status.com/api/v2/summary.json` |
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

## Open decisions

| # | Decision |
| --- | --- |
| **S1** | Which incidents become records. The durable subset above needs a definition sharp enough to implement, not a judgement call per incident |
| **S2** | Whether an unresolved incident enters the catalog at all, or only its postmortem does. Entering early means the record is written before anyone knows what changed |
| **S3** | Polling interval per vendor. The sweep's rate-limit budget is already the constraint on the GitHub sources, and 46 feeds on a short interval competes with it |
| **S4** | Whether component refs match how a customer names the dependency (`gcp` versus `google-cloud` versus `Google Cloud Platform`), which is the alias registry's problem and is worse here than anywhere else |

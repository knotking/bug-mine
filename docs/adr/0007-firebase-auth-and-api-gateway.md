# ADR-0007 — Firebase for user identity, API Gateway for programmatic access

**Status:** Accepted
**Date:** 2026-08-23
**Supersedes:** the API-key-only console login introduced with the web console
**Related:** FR-76 – FR-81, [ADR-0002](0002-grounding-and-provenance.md)

## Context

Two different callers need to authenticate, and they are not alike:

- **A person in a browser**, who expects to type an email and password, stay signed in, and
  reset a forgotten password without contacting an operator.
- **A program** — CI, the CLI, the MCP server, a GitHub Action — which needs a long-lived
  credential it can put in a secret store and rotate on its own schedule.

Until now both were served by one mechanism: a `bmk_` API key pasted into the console. That was
honest about the absence of an identity provider, but it is not a login. It cannot be reset, it
is bearer-only, and treating a machine credential as a human one means the same secret grants
both interactive and programmatic access with no way to revoke one without the other.

There is also a deployment constraint that has blocked the product for the whole build. The
`buildgeek.ai` org enforces `constraints/iam.allowedPolicyMemberDomains`, which forbids granting
`run.invoker` to `allUsers`. Every request to Cloud Run must therefore carry a Google identity
token, which a browser cannot produce — so the console and the public search surface have been
unreachable except through `gcloud run services proxy`.

## Options

**A. Keep API keys for everything.** Nothing to build. But there is no password reset, no
session, no separation between human and machine credentials, and the deployment stays
unreachable.

**B. Build password authentication in the application.** Full control, no dependency. Also
means storing password hashes, and doing it properly requires reset flows, rate limiting,
lockout, and breach monitoring — a large amount of security-sensitive work that is not this
product's problem to solve, and which would be discarded on any move to SSO.

**C. Firebase Authentication for people, API Gateway for programs.** Firebase owns password
storage, reset, and verification, and issues a signed JWT the backend verifies against public
keys. API Gateway sits in front of Cloud Run, validates API keys itself, and calls the backend
as its own service account.

## Decision

**Option C.**

Firebase Authentication (Identity Platform) handles email/password sign-in. The console
exchanges credentials for a Firebase ID token and sends it on every request; the API verifies
the token's signature and maps the Firebase UID to a local `User` row. **We never see or store
a password.** That is the main reason to prefer this over B: the safest way to handle a
credential class is not to hold it.

API Gateway fronts the API for programmatic callers. It validates the API key, then authenticates
to Cloud Run with a service account.

**Every user gets an API key at the moment their account is created**, bound to them as a
principal. This keeps FR-80 intact — a key belongs to exactly one user or one team, never a
tenant — and means a person who signs up can immediately use the CLI and MCP server without a
second, operator-mediated step.

### The constraint this incidentally resolves

API Gateway is not Cloud Run, so `iam.allowedPolicyMemberDomains` does not apply to it. The
gateway can be publicly reachable and hold `run.invoker` itself:

```
browser / CLI  →  API Gateway (public)  →  Cloud Run (IAM, gateway's SA)
```

**Anonymous public search (FR-73) becomes implementable, and the console becomes reachable in a
browser** — both of which have been blocked since the first deployment. That was not the reason
for this decision, but it is the largest single consequence of it.

## Consequences

**Easier.** Password storage, reset, and verification stop being our problem. Human and machine
credentials become separable — revoking a session does not break CI, and rotating a CI key does
not sign anyone out. The public surface stops depending on an org-policy exception. And the
gateway is a natural place for rate limiting and quotas, which currently exist nowhere.

**Harder.** Two authentication paths to implement, test, and reason about, where there was one.
Firebase becomes a hard dependency for interactive access — if it is down, nobody signs in,
though API keys keep working. And the gateway is another deployable with its own configuration
that can drift from the OpenAPI spec it is generated from.

**Accepted downsides.** A Firebase ID token is short-lived and must be refreshed, so the console
needs token-refresh handling that a pasted key did not. Firebase user records and local `User`
rows can diverge — a user deleted in Firebase leaves a local row, so lookup must treat a valid
token for an unknown UID as an error rather than silently creating an account. And API Gateway
adds a hop, which costs latency on the endpoint most sensitive to it: `check/dependencies`,
called once per lockfile from an IDE.

**New obligations.** Invite acceptance now has two halves — creating the Firebase user and the
local membership — which must not half-succeed. Email verification status becomes something the
application can see and must decide how to treat. And the API key issued at signup is shown once;
the console has to make that clear at the only moment it can.

## What this does not change

Authorisation. Firebase establishes *who* the caller is; every existing rule about what they may
see is unchanged. Row-level security still enforces tenant isolation, principals still resolve
to exactly one user or team, and privacy scope still governs the catalog. An identity provider
is not an authorisation model, and treating a verified token as permission would undo the
isolation the whole schema is built around.

# BugMine Authentication — Architecture

**Status:** Draft, for discussion
**Requirements:** FR-76 – FR-86
**Decision:** [ADR-0007](../adr/0007-firebase-auth-and-api-gateway.md)

---

## 1. Two callers, two paths

| | Person in a browser | Program |
| --- | --- | --- |
| Credential | Email + password | API key |
| Issued by | Firebase | BugMine, at account creation |
| Lifetime | Short-lived token, refreshed | Long-lived, rotated deliberately |
| Reset | Self-service, via Firebase | Mint a new one, revoke the old |
| Reaches the API via | API Gateway | API Gateway |

Both resolve to the same `Principal`. Everything downstream — row-level security, privacy
scope, per-principal attribution — is unchanged and does not know which path a request took.
**Authentication answers who; it does not answer what they may see.**

## 2. Shape

```
                    ┌──────────────────────────────────────────┐
  browser ─────────▶│            API GATEWAY (public)          │
  CLI / MCP / CI ──▶│  validates x-api-key · rate limits       │
                    └────────────────────┬─────────────────────┘
                                         │ OIDC as the gateway's service account
                                         ▼
                    ┌──────────────────────────────────────────┐
                    │        CLOUD RUN (IAM-protected)         │
                    │  verifies Firebase ID token when present │
                    │  resolves api key → principal otherwise  │
                    └────────────────────┬─────────────────────┘
                                         ▼
                              RLS · privacy scope
```

**The gateway is what makes the deployment reachable at all.** The org policy forbids granting
`run.invoker` to `allUsers`, so a browser cannot call Cloud Run directly — it cannot produce a
Google identity token. API Gateway is not Cloud Run, so the policy does not apply to it, and it
holds `run.invoker` itself. Anonymous public search and a browser-reachable console both become
possible without an org-policy exception.

## 3. Sign-in

```
1. Console        → Firebase: email + password
2. Firebase       → Console:  ID token (JWT, ~1h) + refresh token
3. Console        → API:      Authorization: Bearer <ID token>
4. API            → verifies signature against Firebase public keys
5. API            → maps uid → local User → Membership → Principal
```

Step 5 is where the design says no. **A valid token for an unknown UID is an error, not an
implicit signup.** An identity provider will mint an account for anyone who completes a form,
and treating a verified token as sufficient would quietly reintroduce self-serve — which FR-76
exists to prevent and which the tenancy model assumes is impossible. Accounts come from
invitations; Firebase authenticates them, it does not create them.

## 4. Account creation

Invite acceptance now spans two systems, so it can half-succeed:

```
accept(token, email, password)
  ├─ validate the invite            (single-use, unexpired, email matches)
  ├─ create the Firebase user       ← external, can fail
  ├─ create User + Membership       ← local
  └─ mint an API key for the user   ← FR-84
```

The order matters. Creating the Firebase user first means a local failure leaves an orphaned
Firebase account, which is recoverable — the invite is still unredeemed and the next attempt
can adopt the existing UID. Creating the local rows first would mean a Firebase failure leaves
a member who can never sign in, which looks like a working account until they try.

**The API key is issued here rather than on request** (FR-84) so somebody who has just signed
up can use the CLI and MCP server immediately. It is shown once, at the only moment the console
can show it.

## 5. What the gateway does and does not do

| Does | Does not |
| --- | --- |
| Validate API keys | Know anything about tenants |
| Rate limit and quota | Enforce privacy scope |
| Authenticate to Cloud Run | Verify Firebase tokens |
| Terminate TLS, provide a stable host | Make authorisation decisions |

Keeping Firebase verification in the application rather than the gateway is deliberate: the
mapping from token to principal needs the database, and a gateway that could approve requests
without one would be a second authorisation path to keep correct.

## 6. Failure modes

- **Firebase unavailable.** Nobody signs in interactively; API keys keep working, so CI, the
  GitHub App and the MCP server are unaffected. That asymmetry is the point of separating them.
- **Gateway config drifts from the OpenAPI spec.** The gateway is generated from the same
  contract the SDKs are, so a route added in code and not in the spec is simply unreachable —
  which is a confusing failure unless the spec is treated as the source of truth.
- **A Firebase user deleted out of band.** The local row survives and its API key still works.
  Deleting an account has to mean both, or revocation is incomplete.
- **Token replay.** ID tokens are bearer credentials with an hour's life. The gateway is the
  only place a global revocation could be enforced, and it does not verify them — so a
  compromised token is valid until expiry.

## 7. Open

| # | |
| --- | --- |
| **A-A1** | Whether unverified email addresses may sign in, or only verified ones |
| **A-A2** | Session length and refresh policy — Firebase's default hour, or shorter |
| **A-A3** | Whether the gateway enforces per-tenant quotas, or the application does |
| **A-A4** | Whether deleting a user cascades to Firebase, and what happens to their findings |

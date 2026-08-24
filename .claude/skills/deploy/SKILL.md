---
name: deploy
description: Deploy the BugMine MVP to GCP, or tear it down. Use when asked to "deploy", "ship it", "redeploy", "push to dev", "tear it down", "destroy the infra", or "stop the billing". Covers the full cycle so the environment can be removed to stop costs and rebuilt later from scratch. Not for local development, which needs only Docker Postgres and no cloud at all.
user-invocable: true
---

# deploy — stand BugMine up on GCP, and take it down again

The environment is designed to be disposable. Everything except the Terraform state bucket can
be destroyed and rebuilt, which matters because **the fixed cost is roughly $165/month whether
or not anything is running** — Cloud SQL and Cloud NAT bill on existence, not use.

**Target:** project `bugmine-dev`, org `buildgeek.ai`, region `us-central1`.

## Before anything

```bash
gcloud auth login                        # CLI credentials
gcloud auth application-default login    # Terraform credentials — SEPARATE, and the one
                                         # people forget. Expiry shows as invalid_rapt.
```

Those are two different credential stores. `gcloud` commands working tells you nothing about
whether Terraform can authenticate.

## Deploy

### 1. State bucket — once per project, ever

```bash
cd infra/terraform/bootstrap && terraform init && terraform apply
```

Separate directory on purpose: a `destroy` in the main configuration must never be able to take
the state with it.

### 2. Infrastructure

```bash
cd infra/terraform && terraform init && terraform apply
```

86 resources. Cloud SQL takes ~5 minutes; everything else is seconds.

### 3. Image

```bash
gcloud builds submit --project=bugmine-dev --config=cloudbuild.yaml \
  --substitutions=_TAG=vN \
  --service-account=projects/bugmine-dev/serviceAccounts/bugmine-build@bugmine-dev.iam.gserviceaccount.com \
  --gcs-source-staging-dir=gs://bugmine-dev-build-source/source
```

The service account and staging bucket are both required. The default compute account cannot
write to the registry, and that is deliberate — see [Why the build is awkward](#why-the-build-is-awkward).

### 4. Deploy the image, then migrate

```bash
terraform apply -var=image_tag=vN
gcloud run jobs execute bugmine-migrate --region=us-central1 --project=bugmine-dev --wait

# Then assert the schema is actually current. A migrate job running the *previous* image finds
# nothing to apply and reports success, so a completed job does not mean a migrated database.
# This has shipped two production 500s — a missing quota table, then a missing scan column —
# both behind a green apply and a green migrate.
gcloud run jobs execute bugmine-admin --region=us-central1 --project=bugmine-dev --wait \
  --args="schema,check"   # must print "schema is current"
```

**Check the result explicitly — `--wait` returning is not success:**

```bash
E=$(gcloud run jobs executions list --job=bugmine-migrate --region=us-central1 \
      --project=bugmine-dev --format='value(name)' --limit=1)
gcloud run jobs executions describe $E --region=us-central1 --project=bugmine-dev \
  --format='value(status.succeededCount)'   # must be 1
```

Do **not** read this from a `table[no-heading]` format string. Empty count fields collapse and
the columns shift, so a failed execution reads as a successful one — which has already caused
a deployment to be declared working while the database had no tables at all.

**Migrations run as a Cloud Run job, not from a laptop**, because Cloud SQL has no public IP.
Giving it one to run a migration trades a durable security posture for a one-off convenience.

### 5. Verify — do not skip

```bash
curl -s "$(terraform output -raw api_url)/readyz"    # {"status":"ok"} — NOT /healthz
terraform plan            # must report "No changes"
```

**Not `/healthz`.** The Google edge answers that path itself on a `*.run.app` host, with an
HTML 404 carrying neither `server: Google Frontend` nor a trace id — the request never reaches
the container. The route was registered and the startup probe on it passed the whole time, so
this check reported the service down on every deploy it has ever run. Probes are internal and
never cross that edge, which is why the app was healthy and the check was not.

Then the check that matters most, because it is the one that silently fails open:

```bash
# RLS must hold in Cloud SQL, not just locally. The role setup differs — Cloud SQL grants
# cloudsqlsuperuser to the application user, and a role that bypasses RLS makes every
# isolation test pass against a database enforcing none of it.
```

Run the tenant-isolation tests against the deployed database before trusting it.

## Teardown

### The one thing that will block you

`google_sql_database_instance.main` has `deletion_protection = true`. A plain `terraform
destroy` **fails on it**, having already destroyed other resources. Clear it first:

```bash
cd infra/terraform
terraform apply -var-file=<(echo 'db_deletion_protection = false')   # if parameterised
# otherwise: edit database.tf, set deletion_protection = false, apply, then destroy
terraform destroy
```

The protection exists because bug version history is **not reconstructible by re-crawling** — a
re-crawl recovers a source's current state, never its past states. Turning it off is a decision,
which is the point of the friction.

### What survives

| Survives destroy | Why |
| --- | --- |
| `bugmine-dev-tfstate` | Separate bootstrap config |
| The project itself | Terraform never created it |
| Enabled APIs | `disable_on_destroy = false` — turning APIs off can break unrelated things |
| Artifact Registry images | Deleted with the repo; push again on rebuild |

### Stopping the bill without a full teardown

If the goal is cost rather than a clean slate, the two lines that bill are:

```bash
terraform apply -var=enable_nat=false        # ~$45/month
terraform apply -var=db_tier=db-custom-1-3840 # roughly halves the ~$120
```

Deleting only the Cloud SQL instance removes ~$120/month and keeps everything else, but the
database has to be recreated and re-migrated. **Turn `enable_nat` back on before shipping
tenant-supplied crawl sources** — it is the network half of the SSRF defence, and the
`nat_enabled` output exists so this is checkable rather than remembered.

## Redeploying later

Deploy steps 2–5. Step 1 is already done permanently. Terraform state in GCS means a rebuilt
environment matches the configuration exactly rather than approximately.

## Things that have already gone wrong here

Each of these cost real time. They are properties of the platform, not mistakes in the config,
and they will recur on a fresh environment.

| Symptom | Cause |
| --- | --- |
| `Queue ID can contain only letters, numbers, or hyphens` | Cloud Tasks rejects underscores. `validate` and `plan` both pass — the constraint is in the API, not the provider schema |
| `Invalid Tier (db-custom-2-8192) for (ENTERPRISE_PLUS)` | Cloud SQL defaults to ENTERPRISE_PLUS, which needs the pricier `db-perf-optimized-*` machines. **Pin `edition = "ENTERPRISE"`** — unpinned with a valid tier, this succeeds quietly at a much higher bill |
| `COPY failed: stat uv.lock: file does not exist` | gcloud's upload honours `.gitignore`. `uv.lock` must be committed — this is an application, not a library |
| Build fails immediately at step 1 | `--mount=type=cache` needs BuildKit, which the Cloud Build docker builder does not enable |
| `invalid interpolation syntax` from Alembic | `config.set_main_option` routes through configparser, which treats `%` as interpolation. A URL-encoded password containing `%3C` kills it. Build the engine directly instead |
| `does not have storage.objects.get access` | The default compute SA lacks build permissions on newer projects |
| Cloud Build wants `roles/storage.admin` | It defaults to a Google-owned logs bucket. Use `logging: CLOUD_LOGGING_ONLY` rather than granting it |
| `403 Forbidden` with an HTML body from the API | Cloud Run IAM, not the app. The org policy `iam.allowedPolicyMemberDomains` forbids `allUsers`, so the public route tree is unreachable from outside the org — see [Public access is blocked](#public-access-is-blocked) |
| `relation "bug_record" does not exist` | Migrations did not run. The job can report a terminal state while having failed |
| An app-level token sent on `Authorization` never arrives | Cloud Run consumes that header for its own IAM check. Since the org policy forces authenticated invocation, any second credential needs its own header — the operator token uses `X-BugMine-Operator` |
| `new row violates row-level security policy` from admin commands | The session had no tenant context. Anything touching `team`, `membership`, `api_key`, `job` or `scan` must be scoped first; only `invite` is readable without it, because its hashed token is the credential |

### Why the build is awkward

A dedicated `bugmine-build` service account exists instead of using the default compute
account. The default is shared by anything that forgets to name an identity, so granting it
registry write would make every such workload a supply-chain path into our images. The extra
flags on `builds submit` are the cost of that.

## Public access is blocked

`FR-73` requires anonymous public search, which needs `roles/run.invoker` for `allUsers`. The
`buildgeek.ai` org enforces `constraints/iam.allowedPolicyMemberDomains`, restricted to one
customer ID, so that binding is rejected:

```
Error 400: One or more users named in the policy do not belong to a permitted customer,
perhaps due to an organization policy.
```

Nothing in this repository can fix that — it is an org-level decision. The options, in
increasing order of effort:

1. **An org-policy exception for `bugmine-dev`**, granted by an org admin. Simplest, and scoped
   to one project.
2. **A load balancer in front of Cloud Run**, with the service kept internal. More moving parts
   and more cost, but it is where Cloud Armor and a custom domain would live anyway.
3. **Ship without public search.** Everything else works — the API authenticates by key — but
   FR-73 goes unimplemented, and with it the acquisition argument that public bug pages are
   indexable.

Until one is chosen, test the deployed API with `gcloud auth print-identity-token` as a bearer
token.

## Triggering ingestion

```bash
TOKEN=$(gcloud auth print-identity-token)                       # Cloud Run IAM
OP=$(gcloud secrets versions access latest \
       --secret=bugmine-operator-token --project=bugmine-dev)   # operator gate

curl -X POST "$(cd infra/terraform && terraform output -raw api_url)/v1/admin/ingest/crawl" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-BugMine-Operator: $OP" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://api.github.com/repos/OWNER/REPO/releases","component_ref":"repo","ecosystem":"pypi"}'
```

**`ecosystem` is the registry the component ships from, not the shape of the feed.** A GitHub
releases URL says nothing about it: `transformers` is `pypi`, `aws-cdk` is `npm`, `moby` is
`go`, and `kotlin` is a language runtime with no package ecosystem at all. Copying `pypi` from
this example is how 313 registered sources ended up claiming it, and `check` derives the
subject domain *from the ecosystem the caller declares* — so a mismatched pair produces records
no scan can ever retrieve, reported as "nothing known about this component". `add_source` now
refuses a pair that contradicts itself, but it cannot know that `moby` is not on PyPI.

Extraction is chained by the crawl worker **only when the content hash changed**, so
re-triggering an unchanged source costs one HTTP request and no tokens.

## Provisioning a tenant

```bash
gcloud run jobs execute bugmine-admin --region=us-central1 --project=bugmine-dev --wait \
  --args="tenant,create,--name,Acme,--slug,acme,--admin-email,you@acme.test"
```

Locally, `bugmine-admin` runs against `BUGMINE_DATABASE_URL` directly.

## What is not deployed yet

`scan_fetch` and `scan_analyze` have queues, service accounts and an egress split defined but
no code. Those are the workers that will need the jobs-plus-dispatcher shape, because a scan
can exceed a request timeout where crawl and extract cannot.

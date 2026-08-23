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
curl -s "$(terraform output -raw api_url)/healthz"
terraform plan            # must report "No changes"
```

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

## What is not deployed yet

Only the API service and the migration job. The crawl, extract, scan_fetch and scan_analyze
workers have queues and service accounts but no Cloud Run jobs — their egress split is defined
in `network.tf` and `iam.tf` and will need `vpc_access` blocks matching it when they land.

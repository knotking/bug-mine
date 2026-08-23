# Infrastructure

Terraform for `bugmine-dev` (org `buildgeek.ai`, project number `433347874320`).

## Apply order

```bash
# 1. Once — creates the bucket holding state for everything else.
cd bootstrap && terraform init && terraform apply

# 2. Everything else.
cd .. && terraform init && terraform apply
```

The bootstrap step is separate so a `destroy` in the main configuration can never take the
state bucket with it.

## What starts billing

| Resource | Monthly | Note |
| --- | --- | --- |
| **Cloud SQL** `db-custom-2-8192` | **~$120** | The dominant fixed cost. `db-custom-1-3840` roughly halves it |
| **Cloud NAT** | **~$45** | The network half of the SSRF defence. `enable_nat = false` skips it, but tenant-supplied crawl sources (M8) must not ship without it |
| GCS, Artifact Registry, Tasks, Scheduler | ~$15 | |

Cloud Run scales to zero and Vertex is per-token, so neither appears until something runs.

## Two things this file enforces that code cannot

**The egress split.** `network.tf` denies egress to RFC1918 and link-local space for anything
tagged `fetch-worker`, and `iam.tf` grants `aiplatform.user` only to workers that have no
network. So a prompt injection in crawled content can control the model's output and still
reach nothing, and a fetch worker cannot be quietly handed a model. Both are the controls in
ADR-0005; expressing them in application configuration would make them a convention.

**Snapshot deletion.** `storage.tf` deletes repository snapshots by bucket lifecycle rule
rather than by a cleanup routine (NFR-20). A retention policy cannot be forgotten by a code
path.

## Deletion protection

`google_sql_database_instance.main` has `deletion_protection = true`. Bug version history is
not reconstructible by re-crawling — a re-crawl recovers a source's current state, never its
past states (NFR-16).

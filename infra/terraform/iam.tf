# Service accounts, one per workload, with least privilege.
#
# The `network` attribute on each is the security boundary from docs/plan/mvp.md §6. It is
# expressed here as a Cloud Run setting when the services are deployed; the accounts differ so
# that a misconfiguration cannot silently grant a model-bearing worker network access.

locals {
  workers = {
    crawl        = { egress = true, model = false }
    extract      = { egress = false, model = true }
    scan_fetch   = { egress = true, model = false }
    scan_analyze = { egress = false, model = true }
    package_pull = { egress = true, model = false }
    dispatcher   = { egress = false, model = false }
  }

  services = ["api", "mcp", "github-app"]
}

resource "google_service_account" "worker" {
  for_each = local.workers

  account_id   = "bugmine-w-${replace(each.key, "_", "-")}"
  display_name = "BugMine worker: ${each.key}"
}

resource "google_service_account" "service" {
  for_each = toset(local.services)

  account_id   = "bugmine-s-${each.value}"
  display_name = "BugMine service: ${each.value}"
}

# Every workload reaches the database.
resource "google_project_iam_member" "sql_client" {
  for_each = merge(
    { for k, v in google_service_account.worker : "w-${k}" => v.email },
    { for k, v in google_service_account.service : "s-${k}" => v.email },
  )

  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${each.value}"
}

# Only the model-bearing workers may call Vertex. Granting this to a fetch worker would
# undermine the point of separating them.
resource "google_project_iam_member" "vertex_user" {
  for_each = {
    for k, v in local.workers : k => google_service_account.worker[k].email if v.model
  }

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${each.value}"
}

# Artifact writes: crawl writes raw artifacts, extract only reads them.
resource "google_storage_bucket_iam_member" "artifacts_writer" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker["crawl"].email}"
}

resource "google_storage_bucket_iam_member" "artifacts_reader" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker["extract"].email}"
}

resource "google_storage_bucket_iam_member" "snapshots_writer" {
  bucket = google_storage_bucket.snapshots.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker["scan_fetch"].email}"
}

resource "google_storage_bucket_iam_member" "snapshots_reader" {
  bucket = google_storage_bucket.snapshots.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker["scan_analyze"].email}"
}

# Only the dispatcher may start job executions.
resource "google_project_iam_member" "dispatcher_run_invoker" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.worker["dispatcher"].email}"
}

# Enqueueing is limited to the surfaces that accept user requests.
resource "google_project_iam_member" "task_enqueuer" {
  for_each = toset(["api", "github-app"])

  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.service[each.value].email}"
}

resource "google_secret_manager_secret_iam_member" "db_password_readers" {
  for_each = merge(
    { for k, v in google_service_account.worker : "w-${k}" => v.email },
    { for k, v in google_service_account.service : "s-${k}" => v.email },
  )

  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

# Telemetry from every workload (FR-20).
resource "google_project_iam_member" "telemetry" {
  for_each = merge(
    { for k, v in google_service_account.worker : "w-${k}" => v.email },
    { for k, v in google_service_account.service : "s-${k}" => v.email },
  )

  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${each.value}"
}

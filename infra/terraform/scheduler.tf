# Scheduled ingestion.
#
# Until now every crawl needed a manual trigger, so the catalog only stayed current while
# somebody was watching it. This is what makes it self-sustaining.
#
# The schedule sweeps far more often than any source's interval. That is deliberate: dueness
# lives in the source row, not in the cron, so per-source cadence can change without touching
# infrastructure, and a sweep that finds nothing due costs one cheap query.

resource "google_service_account" "scheduler" {
  account_id   = "bugmine-scheduler"
  display_name = "BugMine: Cloud Scheduler"
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invokes_api" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_secret_manager_secret_iam_member" "scheduler_reads_operator_token" {
  secret_id = google_secret_manager_secret.operator_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "sweep" {
  name        = "bugmine-sweep"
  region      = var.region
  schedule    = var.sweep_schedule
  time_zone   = "UTC"
  description = "Enqueue every crawl source that is due."

  # A sweep that overruns should be skipped rather than queued behind itself — the next run
  # will pick up the same due sources anyway, since dueness is a property of the source.
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.api.uri}/v1/admin/ingest/sweep"

    headers = {
      "Content-Type" = "application/json"
      # The operator token travels on its own header because Cloud Run consumes Authorization
      # for the OIDC check below. Two credentials cannot share one header.
      "X-BugMine-Operator" = random_password.operator_token.result
    }

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.api.uri
    }
  }
}

variable "sweep_schedule" {
  description = <<-EOT
    Cron for the sweep. Sweeps often; each source is only enqueued when its own interval has
    elapsed, so frequency here costs a query rather than a crawl.
  EOT
  type        = string
  default     = "*/15 * * * *"
}

output "sweep_schedule" {
  value = google_cloud_scheduler_job.sweep.schedule
}

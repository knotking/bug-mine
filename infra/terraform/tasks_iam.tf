# The operator token gating ingestion triggers.
#
# Interim: the operator role is designed but not built, so the trigger is kept off the tenant
# key path entirely. No customer credential can start a crawl, which matters because a crawl
# fetches an arbitrary URL with our egress.
resource "random_password" "operator_token" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "operator_token" {
  secret_id = "bugmine-operator-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "operator_token" {
  secret      = google_secret_manager_secret.operator_token.id
  secret_data = random_password.operator_token.result
}

resource "google_secret_manager_secret_iam_member" "operator_token_reader" {
  secret_id = google_secret_manager_secret.operator_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.service["api"].email}"
}

# Cloud Tasks carries an OIDC token as this identity, so the workers can stay
# INGRESS_TRAFFIC_INTERNAL_ONLY and still refuse anything that is not Tasks. Making them
# publicly invokable to simplify the wiring would put an unauthenticated endpoint in front of
# the component that fetches attacker-supplied URLs.
resource "google_cloud_run_v2_service_iam_member" "invoke_crawl" {
  location = google_cloud_run_v2_service.crawl.location
  name     = google_cloud_run_v2_service.crawl.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker["dispatcher"].email}"
}

resource "google_cloud_run_v2_service_iam_member" "invoke_extract" {
  location = google_cloud_run_v2_service.extract.location
  name     = google_cloud_run_v2_service.extract.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker["dispatcher"].email}"
}

resource "google_cloud_run_v2_service_iam_member" "invoke_scan_fetch" {
  location = google_cloud_run_v2_service.scan_fetch.location
  name     = google_cloud_run_v2_service.scan_fetch.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker["dispatcher"].email}"
}

resource "google_cloud_run_v2_service_iam_member" "invoke_scan_analyze" {
  location = google_cloud_run_v2_service.scan_analyze.location
  name     = google_cloud_run_v2_service.scan_analyze.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker["dispatcher"].email}"
}

# Creating a task that authenticates as another service account requires acting as it.
resource "google_service_account_iam_member" "api_acts_as_invoker" {
  service_account_id = google_service_account.worker["dispatcher"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.service["api"].email}"
}

resource "google_service_account_iam_member" "crawl_acts_as_invoker" {
  service_account_id = google_service_account.worker["dispatcher"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.worker["crawl"].email}"
}

# The crawl worker chains extraction, so it enqueues too.
resource "google_project_iam_member" "crawl_enqueuer" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.worker["crawl"].email}"
}

output "operator_token_secret" {
  value = google_secret_manager_secret.operator_token.secret_id
}

# A dedicated build identity.
#
# The default compute service account is the path of least resistance and the wrong one: it is
# shared by anything that forgets to specify an identity, and granting it storage and registry
# write makes every such workload a supply-chain path into our images. This account does
# nothing but build.

resource "google_service_account" "build" {
  account_id   = "bugmine-build"
  display_name = "BugMine: Cloud Build"
}

resource "google_storage_bucket" "build_source" {
  name          = "${var.project_id}-build-source"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "build_source" {
  bucket = google_storage_bucket.build_source.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.build.email}"
}

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

# Cloud Build with a custom service account must be able to write its own logs, or the build
# fails before it starts.
resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

output "build_service_account" {
  value = google_service_account.build.email
}

output "build_source_bucket" {
  value = google_storage_bucket.build_source.name
}

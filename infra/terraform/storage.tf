# Buckets.

# Raw crawl artifacts, stored verbatim before extraction. This is what lets the whole corpus
# be re-extracted when the prompt or model improves, without re-crawling the internet.
resource "google_storage_bucket" "artifacts" {
  name          = "${var.project_id}-artifacts"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.enabled]
}

# Repository snapshots for server-side scans.
#
# NFR-20's delete-after-scan is enforced here, by bucket policy, rather than in application
# code. A retention rule cannot be forgotten by a code path; a cleanup routine can.
resource "google_storage_bucket" "snapshots" {
  name          = "${var.project_id}-snapshots"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = var.snapshot_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

# Package metadata fetched just-in-time. Public data, so cacheable across tenants — which is
# the incidental benefit of having split package_pull into its own job for security reasons.
resource "google_storage_bucket" "packages" {
  name          = "${var.project_id}-packages"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "bugmine"
  format        = "DOCKER"
  description   = "Worker and service images"

  depends_on = [google_project_service.enabled]
}

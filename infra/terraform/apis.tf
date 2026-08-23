# API enablement, codified rather than clicked.
#
# `disable_on_destroy = false` throughout: turning an API off can break resources in other
# projects that depend on it, and a `terraform destroy` should not have that blast radius.

locals {
  enabled_apis = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "sqladmin.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "vpcaccess.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "identitytoolkit.googleapis.com",
    "apigateway.googleapis.com",
    "servicecontrol.googleapis.com",
    "servicemanagement.googleapis.com",
    # Already enabled on the project, listed so state is complete rather than partial.
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.enabled_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

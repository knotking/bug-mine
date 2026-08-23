# Bootstrap — creates the bucket that holds Terraform state for everything else.
#
# Run once, with local state, before the main configuration. The main config then uses this
# bucket as its backend. Chicken-and-egg is unavoidable; keeping it in a separate directory
# means the bucket is never destroyed by an accidental `destroy` in the main config.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type    = string
  default = "bugmine-dev"
}

variable "region" {
  type    = string
  default = "us-central1"
}

resource "google_storage_bucket" "tf_state" {
  name          = "${var.project_id}-tfstate"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  # State files contain resource metadata and occasionally secrets. Versioning makes a bad
  # apply recoverable rather than terminal.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }

  public_access_prevention = "enforced"
}

output "state_bucket" {
  value = google_storage_bucket.tf_state.name
}

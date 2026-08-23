# Cloud Run.
#
# Direct VPC egress rather than a Serverless VPC Access connector: no connector instances to
# size or pay for, and it is the current recommended path to a private-IP Cloud SQL.

variable "image_tag" {
  description = "Image tag to deploy. Bump to roll forward."
  type        = string
  default     = "v1"
}

locals {
  image  = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/api:${var.image_tag}"
  db_url = "postgresql+psycopg://${google_sql_user.app.name}:%s@${google_sql_database_instance.main.private_ip_address}:5432/${google_sql_database.bugmine.name}"
}

# Migrations run as a job, in the VPC, because the database has no public IP. Running them
# from a laptop would mean giving the instance a public address — trading a durable posture
# for a one-off convenience.
resource "google_cloud_run_v2_job" "migrate" {
  name     = "bugmine-migrate"
  location = var.region

  deletion_protection = false

  template {
    template {
      service_account = google_service_account.worker["dispatcher"].email
      max_retries     = 0
      timeout         = "600s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = local.image
        command = ["alembic"]
        args    = ["upgrade", "head"]

        env {
          name  = "BUGMINE_DB_HOST"
          value = google_sql_database_instance.main.private_ip_address
        }
        env {
          name  = "BUGMINE_DB_USER"
          value = google_sql_user.app.name
        }
        env {
          name  = "BUGMINE_DB_NAME"
          value = google_sql_database.bugmine.name
        }
        env {
          name = "BUGMINE_DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_password.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_sql_database.bugmine]
}

resource "google_cloud_run_v2_service" "api" {
  name     = "bugmine-api"
  location = var.region

  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.service["api"].email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "BUGMINE_DB_HOST"
        value = google_sql_database_instance.main.private_ip_address
      }
      env {
        name  = "BUGMINE_DB_USER"
        value = google_sql_user.app.name
      }
      env {
        name  = "BUGMINE_DB_NAME"
        value = google_sql_database.bugmine.name
      }
      env {
        name = "BUGMINE_DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 10
      }
    }
  }

  depends_on = [google_cloud_run_v2_job.migrate]
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

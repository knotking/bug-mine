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
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BUGMINE_REGION"
        value = var.region
      }
      env {
        name  = "BUGMINE_CRAWL_URL"
        value = google_cloud_run_v2_service.crawl.uri
      }
      env {
        name  = "BUGMINE_EXTRACT_URL"
        value = google_cloud_run_v2_service.extract.uri
      }
      env {
        name  = "BUGMINE_SCAN_FETCH_URL"
        value = google_cloud_run_v2_service.scan_fetch.uri
      }
      env {
        name  = "BUGMINE_TASK_INVOKER_SA"
        value = google_service_account.worker["dispatcher"].email
      }
      env {
        # Only the api service serves the console, so only it needs the browser key.
        # Not a secret: Identity Platform treats it as a project selector, and every
        # client that signs in can read it. Injected so one image serves any environment.
        name  = "BUGMINE_FIREBASE_BROWSER_KEY"
        value = var.firebase_browser_key
      }
      env {
        name  = "BUGMINE_FIREBASE_PROJECT"
        value = var.project_id
      }
      env {
        name = "BUGMINE_OPERATOR_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.operator_token.secret_id
            version = "latest"
          }
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

# The service is reachable without Cloud Run IAM, and enforces its own authentication.
#
# This is deliberate, not a relaxation. FR-73 requires anonymous public search, so an IAM gate
# in front of the whole service would make that requirement unimplementable. Authorization
# lives in the application: /v1/public/* is a separate route tree restricted to public-scope
# records, and every other route resolves a principal from an API key before touching data.
# Row-level security is the second layer beneath both.
variable "allow_public_api" {
  description = <<-EOT
    Grant roles/run.invoker to allUsers so anonymous public search (FR-73) is reachable.

    Defaults false because the buildgeek.ai org enforces
    constraints/iam.allowedPolicyMemberDomains, which rejects allUsers outright. Leaving this
    on makes every apply fail on a binding that cannot succeed. Set true once an org-policy
    exception exists for this project, or replace with a load balancer.
  EOT
  type        = bool
  default     = false
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  count    = var.allow_public_api ? 1 : 0
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Verification runs in the VPC for the same reason migrations do: the database has no public
# IP, and the properties being checked only mean anything against the real one. A local pass
# says nothing about Cloud SQL, where the application role's grants differ.
resource "google_cloud_run_v2_job" "verify" {
  name                = "bugmine-verify"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.worker["dispatcher"].email
      max_retries     = 0
      timeout         = "300s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = local.image
        command = ["bugmine-verify"]

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
}

# --- Workers ---------------------------------------------------------------------------
#
# Two services, not one, because the egress split is the security boundary: crawl fetches
# untrusted content and holds no model; extract holds a model and cannot reach the network
# beyond Vertex. Running both in one service would collapse that into a convention.

# Operator commands need to reach the private database, so they run in the VPC too.
resource "google_cloud_run_v2_job" "admin" {
  name                = "bugmine-admin"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.worker["dispatcher"].email
      max_retries     = 0
      timeout         = "300s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = local.image
        command = ["bugmine-admin"]
        args    = ["tenant", "list"]

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
}

resource "google_cloud_run_v2_service" "crawl" {
  name                = "bugmine-crawl"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker["crawl"].email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    # ALL_TRAFFIC so fetches leave through Cloud NAT, where the deny rules for RFC1918 and
    # link-local apply. PRIVATE_RANGES_ONLY would send public fetches straight out, bypassing
    # the control entirely.
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image   = local.image
      command = ["uvicorn"]
      args    = ["--factory", "bugmine.worker:create_worker_app", "--host", "0.0.0.0", "--port", "8080"]

      ports { container_port = 8080 }

        env {
          # From Secret Manager rather than a variable: this is a real credential, unlike the
          # Firebase browser key, and must not sit in terraform state or a plan output.
          name = "BUGMINE_GITHUB_TOKEN"
          value_source {
            secret_key_ref {
              secret  = "bugmine-github-token"
              version = "latest"
            }
          }
        }
      env {
        name  = "BUGMINE_ARTIFACT_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BUGMINE_REGION"
        value = var.region
      }
      env {
        name  = "BUGMINE_EXTRACT_URL"
        value = google_cloud_run_v2_service.extract.uri
      }
      env {
        name  = "BUGMINE_TASK_INVOKER_SA"
        value = google_service_account.worker["dispatcher"].email
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
    }
  }
}

resource "google_cloud_run_v2_service" "extract" {
  name                = "bugmine-extract"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker["extract"].email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    # PRIVATE_RANGES_ONLY: this service feeds untrusted content to a model. It reaches Cloud
    # SQL and Vertex over Google's network and has no general internet egress, so a successful
    # prompt injection controls the model's output and reaches nothing.
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image   = local.image
      command = ["uvicorn"]
      args    = ["--factory", "bugmine.worker:create_worker_app", "--host", "0.0.0.0", "--port", "8080"]

      ports { container_port = 8080 }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "BUGMINE_ARTIFACT_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BUGMINE_MODEL"
        value = var.extraction_model
      }
      env {
        # `global` rather than a region: gemini-3.7-flash answers there for this project's
        # service account, and returns 404 NOT_FOUND from us-central1 — which reads as a wrong
        # model name rather than a wrong region, and cost a day of misdiagnosis once already.
        name  = "BUGMINE_VERTEX_LOCATION"
        value = "global"
      }
      env {
        # `global` rather than a region: gemini-3.7-flash answers there for this project's
        # service account, and returns 404 NOT_FOUND from us-central1 — which reads as a wrong
        # model name rather than a wrong region, and cost a day of misdiagnosis once already.
        name  = "BUGMINE_VERTEX_LOCATION"
        value = "global"
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
    }
  }
}

variable "extraction_model" {
  type    = string
  default = "gemini-3.7-flash"
}

output "crawl_url" {
  value = google_cloud_run_v2_service.crawl.uri
}

output "extract_url" {
  value = google_cloud_run_v2_service.extract.uri
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

variable "firebase_browser_key" {
  description = "Identity Platform browser API key, injected into the console at serve time."
  type        = string
  default     = ""
}

# The scan pair, split for the same reason crawl and extract are: fetch reaches an arbitrary
# repository a tenant named, analyse reads a model. Neither may do both — a model reachable from
# a process with general egress is what turns prompt injection into exfiltration.
resource "google_cloud_run_v2_service" "scan_fetch" {
  name                = "bugmine-scan-fetch"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker["scan_fetch"].email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    # ALL_TRAFFIC so the clone leaves through Cloud NAT, where the RFC1918 and link-local deny
    # rules apply. PRIVATE_RANGES_ONLY would send public traffic straight out and bypass them.
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image   = local.image
      command = ["uvicorn"]
      args    = ["--factory", "bugmine.worker:create_worker_app", "--host", "0.0.0.0", "--port", "8080"]

      # Cloning a repository is disk- and network-bound, and the default allowance is not
      # enough for a large monorepo.
      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }

      ports { container_port = 8080 }

      env {
        name  = "BUGMINE_SNAPSHOT_BUCKET"
        value = google_storage_bucket.snapshots.name
      }
      env {
        name  = "BUGMINE_SCAN_ANALYZE_URL"
        value = google_cloud_run_v2_service.scan_analyze.uri
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BUGMINE_REGION"
        value = var.region
      }
      env {
        name  = "BUGMINE_TASK_INVOKER_SA"
        value = google_service_account.worker["dispatcher"].email
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
    }
  }
}

resource "google_cloud_run_v2_service" "scan_analyze" {
  name                = "bugmine-scan-analyze"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker["scan_analyze"].email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    # PRIVATE_RANGES_ONLY: this process reads a tenant's source and talks to a model. It reaches
    # Cloud SQL and Vertex over Google's network and has no general internet egress, so an
    # injected instruction that succeeds has nowhere to send anything.
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image   = local.image
      command = ["uvicorn"]
      args    = ["--factory", "bugmine.worker:create_worker_app", "--host", "0.0.0.0", "--port", "8080"]

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }

      ports { container_port = 8080 }

      env {
        name  = "BUGMINE_SNAPSHOT_BUCKET"
        value = google_storage_bucket.snapshots.name
      }
      env {
        name  = "BUGMINE_MODEL"
        value = var.extraction_model
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BUGMINE_REGION"
        value = var.region
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
    }
  }
}

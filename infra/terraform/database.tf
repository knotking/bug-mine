# Cloud SQL — the catalog, job state, findings, and the token ledger.
#
# Postgres rather than a document store because the model is relational and, more importantly,
# because row-level security is the data-layer half of NFR-19's isolation. Application-level
# filtering is one forgotten predicate away from a cross-tenant read, and that bug returns
# *more* data rather than an error.

resource "random_password" "db_app" {
  length  = 32
  special = true
}

resource "google_sql_database_instance" "main" {
  name             = "bugmine"
  database_version = "POSTGRES_16"
  region           = var.region

  # Guards against `terraform destroy` taking the catalog with it. Version history is not
  # reconstructible by re-crawling (NFR-16) — a re-crawl recovers a source's current state,
  # never its past states.
  deletion_protection = true

  settings {
    # Explicit, because the default is ENTERPRISE_PLUS, which rejects db-custom-* tiers and
    # requires the substantially more expensive db-perf-optimized-* machines. Defaulting here
    # would have quietly multiplied the database bill.
    edition           = "ENTERPRISE"
    tier              = var.db_tier
    availability_type = var.db_ha ? "REGIONAL" : "ZONAL"
    disk_size         = 100
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.main.id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 30
      }
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    # Row-level security is only enforced for non-superusers. The application role must never
    # be an owner or a superuser, or RLS silently does nothing.
    database_flags {
      name  = "log_min_duration_statement"
      value = "1000"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = true
    }

    maintenance_window {
      day  = 7
      hour = 4
    }
  }

  depends_on = [google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "bugmine" {
  name     = "bugmine"
  instance = google_sql_database_instance.main.name
}

# The application role. Deliberately not an owner: RLS is bypassed by table owners and
# superusers, so an over-privileged role turns tenant isolation into a no-op.
resource "google_sql_user" "app" {
  name     = "bugmine_app"
  instance = google_sql_database_instance.main.name
  password = random_password.db_app.result
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "bugmine-db-app-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_app.result
}

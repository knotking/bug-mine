output "project_id" {
  value = var.project_id
}

output "db_connection_name" {
  description = "For the Cloud SQL connector."
  value       = google_sql_database_instance.main.connection_name
}

output "db_private_ip" {
  value = google_sql_database_instance.main.private_ip_address
}

output "artifact_bucket" {
  value = google_storage_bucket.artifacts.name
}

output "snapshot_bucket" {
  value = google_storage_bucket.snapshots.name
}

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "vpc_network" {
  value = google_compute_network.main.id
}

output "worker_service_accounts" {
  value = { for k, v in google_service_account.worker : k => v.email }
}

output "nat_enabled" {
  description = "False means tenant-supplied crawl sources (M8) must not be enabled."
  value       = var.enable_nat
}

variable "project_id" {
  description = "GCP project. Resources for BugMine live only here."
  type        = string
  default     = "bugmine-dev"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "environment" {
  description = "Required by the buildgeek.ai org policy on project tagging."
  type        = string
  default     = "Development"
}

variable "db_tier" {
  description = <<-EOT
    Cloud SQL machine type. db-custom-2-8192 is 2 vCPU / 8 GB, ~$120/month, and matches the
    sizing in docs/plan/mvp.md. db-custom-1-3840 roughly halves that for early development.
  EOT
  type        = string
  default     = "db-custom-2-8192"
}

variable "db_ha" {
  description = "Regional failover. Doubles the database bill; off until it is needed."
  type        = bool
  default     = false
}

variable "enable_nat" {
  description = <<-EOT
    Cloud NAT gives the fetch workers controlled egress and is the network half of the SSRF
    defence in docs/plan/mvp.md §3. ~$45/month. Required before tenant-supplied crawl sources
    (M8) may ship.
  EOT
  type        = bool
  default     = true
}

variable "snapshot_retention_days" {
  description = "Repo snapshots are deleted by bucket policy, not application code (NFR-20)."
  type        = number
  default     = 1
}

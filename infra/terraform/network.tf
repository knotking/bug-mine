# Network — the enforcement layer for the egress split.
#
# docs/plan/mvp.md §6 divides workers by whether they may reach the network at all:
#   crawl / scan_fetch   egress yes, no model    (fetch untrusted content)
#   extract / scan_analyze  egress NO, model yes (feed untrusted content to a model)
#
# That split is a security control, not a convention, which is why it lives here rather than
# in application configuration. A successful prompt injection in crawled content can control
# the model's output and still reach nothing.

resource "google_compute_network" "main" {
  name                    = "bugmine"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "main" {
  name                     = "bugmine-${var.region}"
  ip_cidr_range            = "10.10.0.0/20"
  region                   = var.region
  network                  = google_compute_network.main.id
  private_ip_google_access = true
}

# --- Egress for the fetch workers ------------------------------------------------------

resource "google_compute_router" "nat" {
  count   = var.enable_nat ? 1 : 0
  name    = "bugmine-nat-router"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "nat" {
  count  = var.enable_nat ? 1 : 0
  name   = "bugmine-nat"
  router = google_compute_router.nat[0].name
  region = var.region

  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# --- Firewall ---------------------------------------------------------------------------
#
# Deny-by-default egress, with an explicit allow for public HTTPS. This is what makes the
# SSRF defence hold when the application-layer check is bypassed: even if a tenant-supplied
# URL slips past validation, the metadata endpoint and RFC1918 space are unreachable.

resource "google_compute_firewall" "deny_internal_egress" {
  name      = "bugmine-deny-internal-egress"
  network   = google_compute_network.main.name
  direction = "EGRESS"
  priority  = 900

  deny {
    protocol = "all"
  }

  # Link-local carries the GCP metadata server (169.254.169.254) — the SSRF target that
  # would otherwise hand out a service account token.
  destination_ranges = [
    "169.254.0.0/16",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
  ]

  target_tags = ["fetch-worker"]
}

resource "google_compute_firewall" "allow_https_egress" {
  name      = "bugmine-allow-https-egress"
  network   = google_compute_network.main.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["fetch-worker"]
}

# --- Private services access, so Cloud SQL has no public IP -----------------------------

resource "google_compute_global_address" "private_ip" {
  name          = "bugmine-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
  depends_on              = [google_project_service.enabled]
}

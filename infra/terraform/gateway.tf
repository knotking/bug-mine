# API Gateway.
#
# This is what makes the deployment publicly reachable. The org policy forbids granting
# run.invoker to allUsers, so a browser cannot call Cloud Run directly — it has no way to
# produce a Google identity token. API Gateway is not Cloud Run, so the policy does not apply
# to it: the gateway is public, and it authenticates to the backend as its own service account.
#
#   browser / CLI  ->  API Gateway (public)  ->  Cloud Run (IAM, gateway SA)
#
# It also gives the API a stable hostname independent of Cloud Run revision URLs, and a place
# for rate limiting that currently exists nowhere.

resource "google_service_account" "gateway" {
  account_id   = "bugmine-gateway"
  display_name = "BugMine: API Gateway"
}

resource "google_cloud_run_v2_service_iam_member" "gateway_invokes_api" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway.email}"
}

locals {
  # A catch-all route rather than an enumerated one.
  #
  # Enumerating paths here would mean the gateway spec and the application's routes drift, and
  # the failure is silent: a route added in code is simply unreachable, with a 404 that looks
  # like a bug in the application. Authorisation lives behind the gateway anyway — every route
  # already resolves a principal and RLS still enforces isolation — so the gateway's job is
  # reachability and rate limiting, not access control.
  gateway_spec = yamlencode({
    swagger  = "2.0"
    info     = { title = "bugmine", description = "BugMine API", version = "1.0.0" }
    schemes  = ["https"]
    produces = ["application/json"]

    x-google-management = {
      metrics = [{
        name        = "bugmine-requests"
        displayName = "BugMine requests"
        valueType   = "INT64"
        metricKind  = "DELTA"
      }]
      quota = {
        limits = [{
          name   = "bugmine-request-limit"
          metric = "bugmine-requests"
          unit   = "1/min/{project}"
          values = { STANDARD = var.gateway_requests_per_minute }
        }]
      }
    }
    paths = {
      "/**" = {
        for method in ["get", "post", "put", "patch", "delete", "options"] :
        method => merge(local.gw_op, { operationId = "proxy-${method}" })
      }
    }
  })

  gw_op = {
    operationId = "proxy"
    # No `parameters` block: `/**` is a wildcard, not a named path parameter, and declaring one
    # makes the config fail conversion with a message that names neither the path nor the field.
    # Rate limiting.
    #
    # The gateway is the only place this can live: it is the single entry point, and it sees
    # anonymous traffic that never reaches the application's per-tenant accounting. Without a
    # quota, /v1/public/* is an unauthenticated endpoint anybody can hammer — and it is the one
    # surface reachable without an account at all.
    #
    # Metric-based rather than per-key, because the public path has no key to attribute to.
    # Per-tenant quotas remain the application's job, where the tenant is actually known.
    x-google-quota = {
      metricCosts = {
        "bugmine-requests" = 1
      }
    }
    x-google-backend = {
      address          = google_cloud_run_v2_service.api.uri
      path_translation = "APPEND_PATH_TO_ADDRESS"
      deadline         = 60
    }
    responses = { "200" = { description = "ok" } }
  }
}

resource "google_api_gateway_api" "bugmine" {
  provider = google-beta
  api_id   = "bugmine"
}

resource "google_api_gateway_api_config" "bugmine" {
  provider      = google-beta
  api           = google_api_gateway_api.bugmine.api_id
  api_config_id = "bugmine-${substr(sha256(local.gateway_spec), 0, 8)}"

  openapi_documents {
    document {
      path     = "bugmine.yaml"
      contents = base64encode(local.gateway_spec)
    }
  }

  gateway_config {
    backend_config {
      google_service_account = google_service_account.gateway.email
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "bugmine" {
  provider   = google-beta
  api_config = google_api_gateway_api_config.bugmine.id
  gateway_id = "bugmine"
  region     = var.region
}

output "gateway_url" {
  description = "The public entry point. This is what a browser and the CLI should use."
  value       = "https://${google_api_gateway_gateway.bugmine.default_hostname}"
}


variable "gateway_requests_per_minute" {
  description = <<-EOT
    Requests per minute through the gateway.

    Deliberately generous rather than tight: `check/dependencies` is called once per lockfile
    from an IDE, and a developer opening several projects should not be throttled. The number
    exists to bound abuse of the unauthenticated public surface, not to shape normal use.
  EOT
  type        = number
  default     = 600
}

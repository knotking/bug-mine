# Cloud Tasks — the work queue.
#
# Chosen over Pub/Sub because per-queue dispatch rate limiting is the noisy-neighbour control:
# a tenant merging a dependency bump across forty services generates forty scans in a minute
# (NFR-9's 10x burst), which is within their token quota but would monopolise the queue.
#
# Note that Cloud Tasks cannot start a Cloud Run *job* directly — it delivers HTTP requests.
# Tasks target a small dispatcher service which calls the Jobs Execution API, preserving both
# the rate limiting and the 7-day task budget a job gets.

locals {
  queues = {
    # 3/second, 6 at a time. An authenticated token allows 5,000 requests an hour; the
    # unauthenticated ceiling was 60, which one burst exhausted. The throttle that fixed that
    # was slower than the sweep's own refill rate, so the queue filled faster than it drained.
    crawl        = { rate = 3, concurrent = 6 }
    extract      = { rate = 5, concurrent = 10 }
    scan_fetch   = { rate = 10, concurrent = 20 }
    scan_analyze = { rate = 10, concurrent = 20 }
    package_pull = { rate = 20, concurrent = 40 }
  }
}

resource "google_cloud_tasks_queue" "work" {
  for_each = local.queues

  # Cloud Tasks queue IDs permit only letters, digits and hyphens — an underscore is a 400.
  # Job type names use underscores, so translate rather than renaming the job types.
  name     = "bugmine-${replace(each.key, "_", "-")}"
  location = var.region

  rate_limits {
    max_dispatches_per_second = each.value.rate
    max_concurrent_dispatches = each.value.concurrent
  }

  retry_config {
    max_attempts       = 5
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 4
    max_retry_duration = "3600s"
  }

  depends_on = [google_project_service.enabled]
}

locals {
  scheduler_jobs = {
    ingest   = { schedule = "*/15 * * * *" }
    features = { schedule = "2,17,32,47 * * * *" }
    train    = { schedule = "0 2 * * 0" }
    forecast = { schedule = "0 6 * * *" }
    metrics  = { schedule = "10,25,40,55 * * * *" }
  }
}

resource "google_cloud_scheduler_job" "jobs" {
  for_each = local.scheduler_jobs

  name      = "${each.key}-trigger"
  region    = var.scheduler_region
  project   = var.project_id
  schedule  = each.value.schedule
  time_zone = "Europe/Paris"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${each.key}:run"

    oauth_token {
      service_account_email = google_service_account.jobs_sa.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  # Explicit defaults to avoid perpetual plan diff (GCP always returns these).
  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "3600s"
    max_doublings        = 5
  }
}

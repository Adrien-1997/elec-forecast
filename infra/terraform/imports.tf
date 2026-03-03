# Import blocks for resources already provisioned by setup.ps1 / scheduler/setup.ps1.
# Run `terraform init && terraform apply` — Terraform will import these into state
# on first apply, then manage them normally going forward.
# These blocks are idempotent and safe to keep permanently.

# ── IAM / Service Account ─────────────────────────────────────────────────────

import {
  to = google_service_account.jobs_sa
  id = "projects/elec-forecast/serviceAccounts/elec-forecast-sa@elec-forecast.iam.gserviceaccount.com"
}

# ── GCS ───────────────────────────────────────────────────────────────────────

import {
  to = google_storage_bucket.main
  id = "elec-forecast-931951823998"
}

# ── Artifact Registry ─────────────────────────────────────────────────────────

import {
  to = google_artifact_registry_repository.docker
  id = "projects/elec-forecast/locations/europe-west9/repositories/elec-forecast"
}

# ── BigQuery datasets ─────────────────────────────────────────────────────────

import {
  to = google_bigquery_dataset.raw
  id = "projects/elec-forecast/datasets/elec_raw"
}

import {
  to = google_bigquery_dataset.features
  id = "projects/elec-forecast/datasets/elec_features"
}

import {
  to = google_bigquery_dataset.ml
  id = "projects/elec-forecast/datasets/elec_ml"
}

# ── BigQuery tables ───────────────────────────────────────────────────────────

import {
  to = google_bigquery_table.eco2mix
  id = "elec-forecast/elec_raw/eco2mix"
}

import {
  to = google_bigquery_table.weather
  id = "elec-forecast/elec_raw/weather"
}

import {
  to = google_bigquery_table.features
  id = "elec-forecast/elec_features/features"
}

import {
  to = google_bigquery_table.predictions
  id = "elec-forecast/elec_ml/predictions"
}

import {
  to = google_bigquery_table.metrics
  id = "elec-forecast/elec_ml/metrics"
}

# ── Secret Manager ────────────────────────────────────────────────────────────

import {
  to = google_secret_manager_secret.project_id
  id = "projects/elec-forecast/secrets/GCP_PROJECT_ID"
}

import {
  to = google_secret_manager_secret.gcs_bucket
  id = "projects/elec-forecast/secrets/GCS_BUCKET"
}

# ── Cloud Scheduler ───────────────────────────────────────────────────────────

import {
  to = google_cloud_scheduler_job.jobs["ingest"]
  id = "projects/elec-forecast/locations/europe-west1/jobs/ingest-trigger"
}

import {
  to = google_cloud_scheduler_job.jobs["features"]
  id = "projects/elec-forecast/locations/europe-west1/jobs/features-trigger"
}

import {
  to = google_cloud_scheduler_job.jobs["train"]
  id = "projects/elec-forecast/locations/europe-west1/jobs/train-trigger"
}

import {
  to = google_cloud_scheduler_job.jobs["forecast"]
  id = "projects/elec-forecast/locations/europe-west1/jobs/forecast-trigger"
}

import {
  to = google_cloud_scheduler_job.jobs["metrics"]
  id = "projects/elec-forecast/locations/europe-west1/jobs/metrics-trigger"
}

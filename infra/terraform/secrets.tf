# Secret values are derived from Terraform-known values — no manual input needed.

resource "google_secret_manager_secret" "project_id" {
  secret_id = "GCP_PROJECT_ID"
  project   = var.project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "project_id" {
  secret      = google_secret_manager_secret.project_id.id
  secret_data = var.project_id
}

resource "google_secret_manager_secret" "gcs_bucket" {
  secret_id = "GCS_BUCKET"
  project   = var.project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "gcs_bucket" {
  secret      = google_secret_manager_secret.gcs_bucket.id
  secret_data = google_storage_bucket.main.name
}

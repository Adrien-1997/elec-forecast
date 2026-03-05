resource "google_service_account" "jobs_sa" {
  account_id   = var.sa_name
  display_name = "elec-forecast jobs SA"
  project      = var.project_id
}

locals {
  sa_roles = toset([
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/storage.objectAdmin",
    "roles/run.invoker",
    "roles/secretmanager.secretAccessor",
  ])
}

# google_project_iam_member is additive — safe to apply without importing.
resource "google_project_iam_member" "sa_roles" {
  for_each = local.sa_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.jobs_sa.email}"
}

# ── Cloud Build SA — needed to deploy Cloud Run on push to main ───────────────

locals {
  cloudbuild_sa = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = local.cloudbuild_sa
}

# Allows Cloud Build to deploy Cloud Run services that run as jobs_sa
resource "google_service_account_iam_member" "cloudbuild_act_as_jobs_sa" {
  service_account_id = google_service_account.jobs_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.cloudbuild_sa
}

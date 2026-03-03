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

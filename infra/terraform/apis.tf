locals {
  apis = toset([
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each = local.apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

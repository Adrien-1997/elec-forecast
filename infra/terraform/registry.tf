resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "elec-forecast"
  description   = "elec-forecast Docker images"
  format        = "DOCKER"
  project       = var.project_id
}

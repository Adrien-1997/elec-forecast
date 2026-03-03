resource "google_storage_bucket" "main" {
  name                        = "elec-forecast-${data.google_project.this.number}"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = false
  force_destroy               = false
}

output "sa_email" {
  description = "Service account email used by all jobs"
  value       = google_service_account.jobs_sa.email
}

output "bucket_name" {
  description = "GCS bucket name (models + MLflow + Terraform state)"
  value       = google_storage_bucket.main.name
}

output "registry_url" {
  description = "Artifact Registry base URL for Docker images"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}"
}

output "jobs_image" {
  description = "Full image path for the jobs Docker image"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/elec-jobs:latest"
}

output "mlflow_url" {
  description = "MLflow UI Cloud Run service URL"
  value       = google_cloud_run_v2_service.mlflow.uri
}

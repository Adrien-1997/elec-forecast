# Cloud Run — IAM bindings for public-facing services.
#
# Service *deployments* (image tags, env vars, resources) are managed by
# infra/cloudrun/deploy.ps1 — not Terraform — because they depend on Docker
# image builds (build steps, not infra state).
#
# These IAM resources only grant unauthenticated HTTP access (allUsers invoker).
# terraform apply will fail here if the service does not exist yet; run
# deploy.ps1 first to create it, then apply.

resource "google_cloud_run_v2_service_iam_member" "mlflow_public" {
  project  = var.project_id
  location = var.region
  name     = "elec-mlflow"
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "dashboard_public" {
  project  = var.project_id
  location = var.region
  name     = "elec-dashboard"
  role     = "roles/run.invoker"
  member   = "allUsers"
}

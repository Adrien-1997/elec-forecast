# Cloud Run — MLflow UI service + IAM bindings.
#
# MLflow is authenticated (no allUsers) — accessible only to the owner.
# Access via browser: gcloud run services proxy elec-mlflow --region europe-west9
# Or directly with a Bearer token: gcloud auth print-identity-token
#
# The MLflow service is fully managed here.
# Image updates (after Cloud Build pushes a new :latest) require either:
#   terraform apply -replace=google_cloud_run_v2_service.mlflow
#   or: gcloud run deploy elec-mlflow --image <new-image> --region europe-west9
# template is ignored after initial creation to avoid plan diffs on image digest.
#
# The dashboard service is deployed by deploy.ps1 (depends on image build);
# only its public IAM binding is managed here.

resource "google_cloud_run_v2_service" "mlflow" {
  name     = "elec-mlflow"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.jobs_sa.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/elec-mlflow:latest"

      ports {
        container_port = 8080
      }

      env {
        name = "GCS_BUCKET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gcs_bucket.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          memory = "512Mi"
          cpu    = "1"
        }
      }
    }
  }

  # Terraform creates the service; image rollouts handled by Cloud Build.
  lifecycle {
    ignore_changes = [template]
  }

  depends_on = [
    google_project_iam_member.sa_roles,
    google_secret_manager_secret_version.gcs_bucket,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "mlflow_owner" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.mlflow.name
  role     = "roles/run.invoker"
  member   = "user:adrien.morel@gmail.com"
}

# Allow the jobs SA to call the MLflow service (Cloud Run → Cloud Run auth)
resource "google_cloud_run_v2_service_iam_member" "mlflow_sa_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.mlflow.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.jobs_sa.email}"
}

resource "google_cloud_run_v2_job" "reingest" {
  name     = "reingest"
  location = var.region
  project  = var.project_id

  template {
    template {
      service_account = google_service_account.jobs_sa.email
      max_retries     = 1

      timeout = "600s"

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/elec-jobs:latest"

        env {
          name  = "JOB_MODULE"
          value = "reingest"
        }

        env {
          name = "GCP_PROJECT_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.project_id.secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "GCS_BUCKET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.gcs_bucket.secret_id
              version = "latest"
            }
          }
        }

        resources {
          limits = {
            memory = "512Mi"
            cpu    = "1"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template]
  }

  depends_on = [
    google_project_iam_member.sa_roles,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "dashboard_public" {
  project  = var.project_id
  location = var.region
  name     = "elec-dashboard"
  role     = "roles/run.invoker"
  member   = "allUsers"
}

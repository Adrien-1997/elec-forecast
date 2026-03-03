terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # State stored in the project's own GCS bucket — free, no Cloud SQL needed.
  backend "gcs" {
    bucket = "elec-forecast-931951823998"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Used to resolve the project number (needed for bucket name).
data "google_project" "this" {
  project_id = var.project_id
}

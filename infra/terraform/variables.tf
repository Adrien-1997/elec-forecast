variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "elec-forecast"
}

variable "region" {
  description = "Primary GCP region for all resources"
  type        = string
  default     = "europe-west9"
}

variable "scheduler_region" {
  description = "Cloud Scheduler region (europe-west1 — availability constraint)"
  type        = string
  default     = "europe-west1"
}

variable "sa_name" {
  description = "Service account short name"
  type        = string
  default     = "elec-forecast-sa"
}

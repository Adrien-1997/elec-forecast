# ── Datasets ─────────────────────────────────────────────────────────────────

resource "google_bigquery_dataset" "raw" {
  dataset_id = "elec_raw"
  location   = var.region
  project    = var.project_id
}

resource "google_bigquery_dataset" "features" {
  dataset_id = "elec_features"
  location   = var.region
  project    = var.project_id
}

resource "google_bigquery_dataset" "ml" {
  dataset_id = "elec_ml"
  location   = var.region
  project    = var.project_id
}

# ── Tables ────────────────────────────────────────────────────────────────────

resource "google_bigquery_table" "eco2mix" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "eco2mix"
  project             = var.project_id
  description         = "Raw eco2mix consumption records from ODRE API."
  deletion_protection = false

  schema = file("${path.module}/schemas/eco2mix.json")

  time_partitioning {
    type          = "DAY"
    field         = "date_heure"
    expiration_ms = 63072000000 # 730 days — needed as JOIN target for training
  }

  clustering = ["region"]
}

resource "google_bigquery_table" "weather" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "weather"
  project             = var.project_id
  description         = "Raw weather observations from Open-Meteo per region centroid."
  deletion_protection = false

  schema = file("${path.module}/schemas/weather.json")

  time_partitioning {
    type          = "DAY"
    field         = "date_heure"
    expiration_ms = 63072000000 # 730 days — needed for full feature recomputation
  }

  clustering = ["region"]
}

resource "google_bigquery_table" "features" {
  dataset_id          = google_bigquery_dataset.features.dataset_id
  table_id            = "features"
  project             = var.project_id
  description         = "Feature store — 15-min grain per region, used for training and scoring."
  deletion_protection = false

  schema = file("${path.module}/schemas/features.json")

  time_partitioning {
    type          = "DAY"
    field         = "date_heure"
    expiration_ms = 7776000000 # 90 days — raw (730d) retained for full recomputation if needed
  }

  clustering = ["region"]
}

resource "google_bigquery_table" "predictions" {
  dataset_id          = google_bigquery_dataset.ml.dataset_id
  table_id            = "predictions"
  project             = var.project_id
  description         = "24h-ahead demand forecasts. One row per (forecast_horizon_dt, region) — idempotent UPSERT."
  deletion_protection = false

  schema = file("${path.module}/schemas/predictions.json")

  time_partitioning {
    type          = "DAY"
    field         = "forecast_horizon_dt"
    expiration_ms = 7776000000 # 90 days
  }

  clustering = ["region"]
}

resource "google_bigquery_table" "metrics" {
  dataset_id          = google_bigquery_dataset.ml.dataset_id
  table_id            = "metrics"
  project             = var.project_id
  description         = "Rolling 7-day model performance metrics per region + France total. One row per (computed_date, region) — idempotent UPSERT."
  deletion_protection = false

  schema = file("${path.module}/schemas/metrics.json")

  time_partitioning {
    type  = "DAY"
    field = "computed_date"
  }

  clustering = ["region"]
}

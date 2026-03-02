# elec-forecast

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![GCP](https://img.shields.io/badge/GCP-Cloud_Run-4285F4?logo=googlecloud&logoColor=white)
![BigQuery](https://img.shields.io/badge/BigQuery-elec__raw%20%7C%20features%20%7C%20ml-4285F4?logo=googlebigquery&logoColor=white)
![LightGBM](https://img.shields.io/badge/Model-LightGBM-2980b9)
![MLflow](https://img.shields.io/badge/Tracking-MLflow-0194E2?logo=mlflow&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
[![Live demo](https://img.shields.io/badge/Live_demo-Dashboard-2563EB)](https://elec-dashboard-931951823998.europe-west9.run.app)

End-to-end ML pipeline for **day-ahead electricity demand forecasting** across 12 French metropolitan regions — running live on GCP.

**Live dashboard**: [elec-dashboard-931951823998.europe-west9.run.app](https://elec-dashboard-931951823998.europe-west9.run.app)

---

## Overview

This project forecasts regional electricity consumption in France at 15-minute granularity, 24 hours ahead. The full pipeline runs autonomously on GCP free-tier infrastructure: data is ingested every 15 minutes from two public APIs, features are materialised into BigQuery, a LightGBM model is retrained weekly, and predictions are served through a Streamlit dashboard with live monitoring metrics.

The goal is a production-grade ML system — not just a notebook — with proper data contracts, experiment tracking, scheduled jobs, monitoring, and a deployment pipeline.

---

## Architecture

```
ODRÉ API (eco2mix)  ─┐
                      ├─► [ingest]  ─► BQ elec_raw  ─► [features]  ─► BQ elec_features
Open-Meteo API     ─┘                                                        │
                                                                              ▼
                                                                       [train] ──► GCS model artifact
                                                                              │         │
                                                                         MLflow DB  [score]
                                                                       (SQLite/GCS)    │
                                                                                       ▼
                                                                            BQ elec_ml.predictions
                                                                                       │
                                                                                       ▼
                                                                         Streamlit dashboard (live)
```

**Data flow per cycle (every 15 min):**

```
:00/:15/:30/:45  →  ingest   — fetch new eco2mix + weather records → BQ raw
         +2 min  →  features — materialise feature store from raw
         +5 min  →  score    — load model from GCS, write 24h-ahead predictions → BQ
```

Training runs weekly (Sunday 2am) on the full feature store.

---

## Stack

| Layer | Technology | Why |
|---|---|---|
| Compute | Cloud Run Jobs (batch) + Cloud Run Services (dashboard) | Scale to zero, no idle cost |
| Storage | BigQuery (raw, features, predictions) + GCS (model artifacts) | Serverless, free-tier friendly |
| Orchestration | Cloud Scheduler | Managed cron, no Airflow overhead |
| ML | LightGBM + scikit-learn | Fast training, strong tabular performance |
| Experiment tracking | MLflow self-hosted on Cloud Run | Portable, no vendor lock-in; SQLite on GCS avoids Cloud SQL cost |
| Dashboard | Streamlit on Cloud Run | Rapid iteration, Python-native |
| CI/CD | Cloud Build + Artifact Registry | Native GCP, triggered via `deploy.ps1` |
| Region | europe-west9 (Paris) | Co-located with data source |

---

## Data Sources

### ODRÉ eco2mix (`eco2mix-regional-tr`)
- **Provider**: [Open Data Réseaux Énergies](https://odre.opendatasoft.com/)
- **License**: Licence Ouverte v2.0 (Etalab) — no API key required
- **Granularity**: 15-minute intervals, ~7h publication lag
- **Coverage**: 12 metropolitan French regions, back to 2013 (historical dataset)
- **Fields used**: `date_heure`, `libelle_region`, `consommation` (MW)

### Open-Meteo
- **Provider**: [open-meteo.com](https://open-meteo.com/) — free, no auth
- **Granularity**: Hourly per region centroid (joined to 15-min eco2mix by `TIMESTAMP_TRUNC(date_heure, HOUR)`)
- **Fields**: `temperature_2m` (°C), `wind_speed_10m` (km/h), `direct_radiation` (W/m²)

---

## Feature Engineering

Features are computed in BigQuery SQL (single round-trip) then augmented in Python:

| Feature | Description |
|---|---|
| `consommation_lag_24h` | Consumption same time yesterday |
| `consommation_lag_168h` | Consumption same time last week |
| `consommation_rolling_168h` | 7-day rolling average (RANGE window in BQ) |
| `temperature_celsius` | Regional temperature at nearest hour |
| `wind_speed_kmh` | Regional wind speed at nearest hour |
| `solar_radiation_wm2` | Direct radiation at nearest hour |
| `hour_of_day` | 0–23 (Europe/Paris local time) |
| `day_of_week` | 0=Mon … 6=Sun |
| `month` | 1–12 |
| `is_weekend` | Boolean |
| `is_public_holiday_fr` | Boolean (via `holidays` library) |

Region is encoded as a categorical feature by LightGBM natively.

---

## ML Model

- **Algorithm**: LightGBM regressor (gradient boosted trees)
- **Target**: `consommation` (MW) at each 15-min slot, per region
- **Horizon**: 24 hours ahead (96 slots × 12 regions = 1,152 predictions per scoring run)
- **Experiment tracking**: MLflow — each training run logs parameters, metrics (RMSE, MAE), and the model artifact
- **Artifact storage**: `gs://elec-forecast-931951823998/models/{run_id}/model.lgb`
- **Model registry**: MLflow tracking DB (SQLite) persisted on GCS, downloaded/uploaded at job boundaries

---

## Dashboard

Live Streamlit dashboard showing:

- **KPI row**: France total predicted MW (next slot), data completeness (24h), MAE, p95/p99 error vs actuals
- **Pipeline freshness**: colour-coded badges (green < 20 min, yellow < 60 min, red otherwise) for ingest, features, score
- **Regional map**: folium map of France with circle markers — size and opacity encode predicted demand per region
- **Time series**: realized vs predicted consumption, selectable per region or France total, 48h history + 24h forecast with overlap zone where past predictions meet actuals

---

## Repo Layout

```
elec-forecast/
├── jobs/
│   ├── elec_jobs/
│   │   ├── ingest/run.py          # eco2mix + weather → BQ raw
│   │   ├── features/run.py        # raw → feature store (BQ SQL + Python)
│   │   ├── train/run.py           # features → LightGBM + MLflow + GCS
│   │   ├── score/run.py           # model + features → 24h predictions
│   │   ├── shared/
│   │   │   ├── config.py          # env-based config + region centroids
│   │   │   ├── bq.py              # BQ client + load helpers
│   │   │   └── gcs.py             # GCS upload/download helpers
│   │   └── __main__.py            # Docker entrypoint (JOB_MODULE env var)
│   └── pyproject.toml
├── apps/
│   ├── dashboard/
│   │   ├── app.py                 # Streamlit dashboard
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── mlflow/                    # Self-hosted MLflow server (WIP)
├── infra/
│   ├── cloudrun/
│   │   ├── Dockerfile.jobs        # Single image for all 4 jobs
│   │   ├── cloudbuild.yaml        # Cloud Build — builds jobs + dashboard images
│   │   └── deploy.ps1             # Build + deploy all Cloud Run resources
│   ├── sql/ddl/                   # BigQuery table DDL (data contracts)
│   └── scheduler/setup.ps1        # Cloud Scheduler jobs setup
├── contracts/
│   ├── schemas.md                 # Human-readable table schemas
│   └── features.yaml              # Feature registry
├── .env.example
├── CLAUDE.md                      # Project context for Claude Code
└── TODO.md
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) authenticated
- GCP project with billing enabled (free tier sufficient)

### 1. Clone and create venv

```powershell
git clone <repo>
cd elec-forecast
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e jobs/[dev]
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
# Fill in: GCP_PROJECT_ID, GCS_BUCKET
```

### 3. Bootstrap GCP resources

Creates APIs, IAM service account, GCS bucket, Artifact Registry, BQ datasets + tables, Secret Manager secrets.

```powershell
gcloud auth login
gcloud config set project <PROJECT_ID>
.\infra\setup.ps1
```

### 4. Deploy to Cloud Run

Builds Docker images via Cloud Build and deploys 4 Cloud Run Jobs + dashboard Service.

```powershell
.\infra\cloudrun\deploy.ps1
.\infra\scheduler\setup.ps1
```

### 5. Run jobs locally

```powershell
# Activate venv and set env vars
$env:JOB_MODULE = "ingest"   # or features / train / score
python -m elec_jobs
```

---

## Jobs

| Job | Schedule | Description |
|---|---|---|
| `ingest` | `*/15 * * * *` | Pull new eco2mix records + weather → BQ raw tables |
| `features` | `2,17,32,47 * * * *` | Compute lags, rolling avg, calendar features → BQ feature store |
| `train` | `0 2 * * 0` (Sun 2am) | Train LightGBM on full feature store, log to MLflow, push model to GCS |
| `score` | `5,20,35,50 * * * *` | Load latest model from GCS, generate 96-slot 24h forecast → BQ predictions |

Schedules are staggered so each job waits for upstream data before running.

All jobs share a single Docker image (`Dockerfile.jobs`); the `JOB_MODULE` environment variable selects the entry point.

---

## GCP Resources

| Resource | Value |
|---|---|
| Project | `elec-forecast` (`931951823998`) |
| Region | `europe-west9` (Paris) |
| GCS bucket | `elec-forecast-931951823998` |
| Artifact Registry | `europe-west9-docker.pkg.dev/elec-forecast/elec-forecast` |
| Service account | `elec-forecast-sa@elec-forecast.iam.gserviceaccount.com` |
| BQ datasets | `elec_raw`, `elec_features`, `elec_ml` |
| Scheduler region | `europe-west1` (Cloud Scheduler availability constraint) |

---

## Roadmap

- [ ] MLflow server on Cloud Run (self-hosted experiment tracking UI)
- [ ] GitHub → Cloud Build trigger (CI on push to main)
- [ ] Historical backfill (eco2mix-regional-cons-def, 2013–present) + initial training run
- [ ] Drift monitoring: PSI/KS test on feature distributions, rolling MAE vs baseline
- [ ] Automated retrain policy: trigger when 7-day MAE exceeds threshold
- [ ] Data retention: BQ partition expiry (raw 90d, features 30d) + GCS model rotation (keep last 3)
- [ ] Unit tests (mock ODRÉ API, feature computation)

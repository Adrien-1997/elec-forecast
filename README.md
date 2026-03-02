# elec-forecast

End-to-end ML pipeline for **day-ahead electricity demand forecasting** across 13 French metropolitan regions.

Data source: [ODRÉ API](https://odre.opendatasoft.com/) (eco2mix) + [Open-Meteo](https://open-meteo.com/) weather.
Infrastructure: GCP free tier (Cloud Run, BigQuery, GCS, Cloud Scheduler).

---

## Architecture

```
ODRÉ API (eco2mix)  ─┐
                      ├─► [ingest]  ─► BQ raw  ─► [features]  ─► BQ feature store
Open-Meteo API     ─┘                                               │
                                                                     ▼
                                                              [train] ──► GCS artifact
                                                                     │         │
                                                                MLflow DB   [score]
                                                              (SQLite/GCS)     │
                                                                               ▼
                                                                      BQ predictions
                                                                               │
                                                                               ▼
                                                                    Streamlit dashboard
```

## Repo layout

```
elec-forecast/
├── jobs/
│   ├── elec_jobs/
│   │   ├── ingest/run.py       # eco2mix + weather → BQ raw
│   │   ├── features/run.py     # raw → feature store
│   │   ├── train/run.py        # features → model + MLflow
│   │   ├── score/run.py        # model + features → predictions
│   │   ├── shared/             # bq.py, gcs.py, config.py
│   │   └── __main__.py         # entrypoint (JOB_MODULE env var)
│   └── pyproject.toml
├── apps/
│   ├── dashboard/app.py        # Streamlit
│   └── mlflow/                 # Self-hosted MLflow on Cloud Run
├── infra/
│   ├── cloudrun/
│   │   ├── Dockerfile.jobs
│   │   ├── cloudbuild.yaml
│   │   └── deploy.ps1          # build + deploy all Cloud Run Jobs
│   ├── sql/ddl/                # BQ table DDL
│   └── scheduler/setup.ps1    # Cloud Scheduler setup
├── contracts/
│   ├── schemas.md              # human-readable table schemas
│   └── features.yaml           # feature registry
├── .env.example
└── CLAUDE.md
```

## Getting started

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
# Edit .env with your GCP project ID, bucket name, etc.
```

### 3. Authenticate to GCP

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

### 4. Create BQ datasets and tables

```powershell
bq mk --dataset --location=europe-west9 elec_raw
bq mk --dataset --location=europe-west9 elec_features
bq mk --dataset --location=europe-west9 elec_ml
bq query --use_legacy_sql=false < infra/sql/ddl/elec_raw.sql
bq query --use_legacy_sql=false < infra/sql/ddl/elec_features.sql
bq query --use_legacy_sql=false < infra/sql/ddl/elec_ml.sql
```

### 5. Deploy to Cloud Run

```powershell
.\infra\cloudrun\deploy.ps1
.\infra\scheduler\setup.ps1
```

## Jobs

| Job | Schedule | Description |
|---|---|---|
| `ingest` | Every 30 min | Pull eco2mix + weather → BQ raw |
| `features` | Every 30 min | Materialise feature store |
| `train` | Sunday 2am | Train LightGBM, log to MLflow |
| `score` | Every 30 min | Generate 24h-ahead forecasts |

Run locally:

```powershell
$env:JOB_MODULE = "ingest"
python -m elec_jobs
```

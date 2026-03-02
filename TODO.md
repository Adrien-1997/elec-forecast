# TODO — elec-forecast

## Infra / GCP setup
- [x] Create GCP project (`elec-forecast`)
- [x] Run `.\infra\setup.ps1` — APIs, SA, bucket, Artifact Registry, BQ datasets+tables, secrets
- [x] Update `.env` with `GCS_BUCKET=elec-forecast-931951823998`
- [x] Update CLAUDE.md (GCP section + Current Status)

## Jobs — implementation
- [x] `ingest/run.py` — pull eco2mix (ODRÉ API, paginated) → BQ `elec_raw.eco2mix`
- [x] `ingest/run.py` — pull Open-Meteo weather per region centroid → BQ `elec_raw.weather`
- [x] `features/run.py` — compute lags + rolling avg + calendar features → BQ `elec_features.features`
- [x] `train/run.py` — train LightGBM, log to MLflow, upload model artifact to GCS
- [x] `score/run.py` — load model from GCS, generate 24h-ahead forecasts → BQ `elec_ml.predictions`

## Apps
- [x] `apps/dashboard/app.py` — forecasts vs actuals per region (folium map + Plotly time series)
- [x] `apps/dashboard/app.py` — monitoring metrics (MAE, p95, p99, completeness, freshness badges)
- [ ] `apps/mlflow/` — deploy MLflow server on Cloud Run (SQLite ↔ GCS sync)

## CI/CD + deployment
- [x] `infra/cloudrun/deploy.ps1` — build via Cloud Build + deploy 4 Jobs + dashboard Service
- [x] `infra/cloudrun/cloudbuild.yaml` — build jobs + dashboard images
- [x] `infra/scheduler/setup.ps1` — create 4 Cloud Scheduler jobs (staggered: ingest→features+2min→score+5min)
- [x] Verified Cloud Run job logs — all 4 jobs executing successfully
- [ ] Connect Cloud Build to GitHub repo (trigger on push to main)

## Data pipeline operations
- [ ] **Backfill**: run ingest with `eco2mix-regional-cons-def` (historical dataset, back to 2013) to populate BQ for training
- [ ] **Initial training run**: execute `train` job after backfill, verify MLflow run logged + model on GCS
- [ ] Validate end-to-end pipeline: ingest → features → train → score → dashboard shows predictions

## Monitoring & retraining
- [ ] **Drift detection**: compute feature drift (PSI or KS test) on rolling window vs training distribution
- [ ] **Prediction drift**: track MAE rolling 7d vs baseline MAE from training — alert if > 2× baseline
- [ ] **Retrain trigger policy**: define threshold (e.g. MAE 7d avg > 150 MW) → manual or automated retrain
- [ ] Add drift metrics to dashboard (separate monitoring tab or new KPI row)
- [ ] Document retrain SOP in CLAUDE.md

## Data retention policy
- [ ] **BQ `elec_raw.eco2mix`**: set table expiration or partition expiry — keep 90 days rolling (raw data is cheap but grows fast)
- [ ] **BQ `elec_features.features`**: keep 30 days (can be recomputed from raw)
- [ ] **BQ `elec_ml.predictions`**: keep 90 days (needed for MAE computation and drift tracking)
- [ ] **GCS models/**: keep last 3 model versions (by run_id), delete older artifacts
- [ ] **GCS mlflow/**: SQLite DB stays (lightweight), artifact subdirs follow model retention

## Tests & quality
- [ ] Unit tests `ingest` (mock ODRÉ API)
- [ ] Unit tests `features` (lag/rolling computation)
- [ ] Linter: `ruff check jobs/`

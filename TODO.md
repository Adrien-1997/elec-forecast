# TODO — elec-forecast

## Infra / GCP setup
- [x] Create GCP project (`elec-forecast`)
- [x] Run `.\infra\setup.ps1` — APIs, SA, bucket, Artifact Registry, BQ datasets+tables, secrets
- [x] Update `.env` with `GCS_BUCKET=elec-forecast-931951823998`
- [x] Update CLAUDE.md (GCP section + Current Status)
- [ ] **Migrate infra to Terraform** — replace `infra/setup.ps1` + `infra/scheduler/setup.ps1` with Terraform modules (GCS, BQ datasets+tables, IAM, Secret Manager, Cloud Scheduler); keep `deploy.ps1` for image build + Cloud Run deploy (Cloud Build not easily Terraformable)

## Data quality — ingest bug fixes
- [x] **Overlap window** (`ingest/run.py`): use `since = MAX(date_heure) - 6h` instead of exact max — re-fetches recent slots so late-publishing regions catch up to 12/12 progressively
- [x] **Upsert on ingest** (`ingest/run.py`): replace `_append_to_bq` with `_merge_to_bq` (BQ MERGE on `date_heure, region`) to avoid duplicates from the re-fetch overlap
- [ ] **Deploy + one-time 48h backfill**: rebuild jobs image, redeploy ingest, run manually with `DEFAULT_LOOKBACK_DAYS=2` to retroactively fill incomplete slots already in BQ

## Jobs — implementation
- [x] `ingest/run.py` — pull eco2mix (ODRÉ API, paginated) → BQ `elec_raw.eco2mix`
- [x] `ingest/run.py` — pull Open-Meteo weather per region centroid → BQ `elec_raw.weather`
- [x] `features/run.py` — compute lags + rolling avg + calendar features → BQ `elec_features.features`
- [x] `train/run.py` — train LightGBM (+ region as categorical), log to MLflow, upload model artifact to GCS
- [x] `forecast/run.py` — daily job: lag features from BQ + Open-Meteo forecast weather → 96×12 predictions → UPSERT `elec_ml.predictions`
- [x] `metrics/run.py` — every 15 min: predictions × actuals → MAE/p95/p99 rolling 7d → UPSERT `elec_ml.metrics`
- [x] `shared/bq.py` — `merge_to_bq` utility (used by ingest, forecast, metrics)
- [x] Cloud Run Jobs created for `forecast` + `metrics`; `score` job + scheduler trigger deleted; metrics on `10,25,40,55 * * * *`

## Apps
- [x] `apps/dashboard/app.py` — forecasts vs actuals per region (folium map + Plotly time series)
- [x] `apps/dashboard/app.py` — monitoring metrics (MAE, p95, p99, completeness, freshness badges)
- [ ] `apps/mlflow/` — deploy MLflow server on Cloud Run (SQLite ↔ GCS sync)

## CI/CD + deployment
- [x] `infra/cloudrun/deploy.ps1` — build via Cloud Build + deploy 5 Jobs + dashboard (`:latest` tag)
- [x] `infra/cloudrun/cloudbuild.yaml` — builds images tagged `:{SHA}` + `:latest`
- [x] `infra/scheduler/setup.ps1` — 5 Cloud Scheduler jobs (ingest, features, train, forecast, metrics)
- [x] Verified all 5 Cloud Run jobs execute successfully
- [ ] Connect Cloud Build to GitHub repo (trigger on push to main)

## Data pipeline operations
- [ ] **Backfill**: run ingest with `eco2mix-regional-cons-def` (historical dataset, back to 2013) to populate BQ for training
- [ ] **Initial training run**: execute `train` job after backfill, verify MLflow run logged + model on GCS
- [x] Validate end-to-end pipeline: all 5 jobs run locally + on Cloud Run; dashboard shows predictions + freshness badges

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

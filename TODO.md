# TODO — elec-forecast

## Infra / GCP setup
- [x] Create GCP project (`elec-forecast`)
- [x] Bootstrap GCP resources via `setup.ps1` (now deleted — replaced by Terraform)
- [x] Update `.env` with `GCS_BUCKET=elec-forecast-931951823998`
- [x] Update CLAUDE.md (GCP section + Current Status)
- [x] **Migrate infra to Terraform** — `infra/terraform/` covers GCS, BQ datasets+tables, IAM, Secret Manager, Cloud Scheduler, Artifact Registry, APIs; `deploy.ps1` kept for image build + Cloud Run deploy

## Data quality — ingest bug fixes
- [x] **Overlap window** (`ingest/run.py`): use `since = MAX(date_heure) - 6h` instead of exact max — re-fetches recent slots so late-publishing regions catch up to 12/12 progressively
- [x] **Upsert on ingest** (`ingest/run.py`): replace `_append_to_bq` with `_merge_to_bq` (BQ MERGE on `date_heure, region`) to avoid duplicates from the re-fetch overlap

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
- [x] `apps/mlflow/` — MLflow server on Cloud Run (SQLite↔GCS sync, auth-protected, scales to 0)

## CI/CD + deployment
- [x] `infra/cloudrun/deploy.ps1` — build via Cloud Build + deploy 6 Jobs + dashboard (`:latest` tag)
- [x] `infra/cloudrun/cloudbuild.yaml` — builds images tagged `:{SHA}` + `:latest`
- [x] Cloud Scheduler jobs — features + train changed to daily (01:50 / 02:00 Paris); managed by Terraform
- [x] Verified all 6 Cloud Run jobs execute successfully (incl. backfill)
- [x] Connect Cloud Build to GitHub repo (trigger on push to main)
- [x] Commit + push all current fixes (forecast lag alignment, daily schedule, 90d lookback, dashboard v2)

## Data pipeline operations
- [x] **Backfill**: `backfill/run.py` job — eco2mix-regional-cons-def (2024) + eco2mix-regional-tr (2025+); `scripts/full_pipeline.ps1` orchestrates full reset
- [x] **Initial training run**: model trained on 2024-01-01 → today; MLflow run logged locally; model artifact on GCS
- [x] **Forecast lag alignment fix**: inference features aligned to T=slot-24h (lag_24h=eco[slot-48h], lag_168h=eco[slot-192h]); ECO_LOOKBACK_H bumped to 216h
- [x] **Daily retrain**: features + train jobs changed from weekly to daily; `TRAIN_LOOKBACK_DAYS=730` rolling 2-year window
- [x] **Dashboard v2**: contact header (Adrien Morel + links), system check badges (Ingest·Features / Forecast·Retrain / Eval), model_ver + forecasted_at from latest batch
- [x] **Dashboard v3**: MLflow link in header; Retrain badge reads GCS blob timestamp; timeseries x-axis 00:00–00:00 UTC; fixed colorbar/axis margins; chart card padding
- [x] Validate end-to-end pipeline: all 6 jobs run locally + on Cloud Run; dashboard shows predictions + freshness badges

## Monitoring & retraining
- [x] **Performance monitoring**: MAE trend chart on dashboard — rolling 7d MAE per region + France total over last 30 days (`load_metrics_history` + `build_mae_trend`)
- [x] **MLflow auth**: SA invoker IAM (`mlflow_sa_invoker` in cloudrun.tf) + OIDC token injection in train job (`_fetch_identity_token`); local access via `gcloud run services proxy elec-mlflow --region europe-west9 --port 8080`
- [x] **`cloudbuild.yaml` consolidated**: single file builds all 3 images + redeploys all Cloud Run Jobs + services on push to main
- ~~Drift detection (PSI/KS)~~: not implemented — daily retrain + seasonal variance make statistical tests impractical at this cadence
- ~~Automated retrain trigger~~: not implemented — daily retrain IS the policy; MAE spikes during seasonal transitions would cause runaway retrains

## Data retention policy
- [x] **BQ `elec_raw.eco2mix`**: partition expiry 730 days (needed as JOIN target for training)
- [x] **BQ `elec_raw.weather`**: partition expiry 730 days (needed for full feature recomputation)
- [x] **BQ `elec_features.features`**: partition expiry 90 days (raw kept 730d for recompute if needed)
- [x] **BQ `elec_ml.predictions`**: partition expiry 90 days
- [x] **GCS models/**: keep last 7 model versions — `_prune_old_models()` in train job after each run
- [x] **GCS mlflow/**: SQLite DB stays; matching artifact subdirs pruned alongside models

## Tests & quality
- [ ] Unit tests `ingest` (mock ODRÉ API)
- [ ] Unit tests `features` (lag/rolling computation)
- [ ] Linter: `ruff check jobs/`

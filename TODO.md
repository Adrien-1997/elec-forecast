# TODO — elec-forecast

## Infra / GCP setup
- [x] Créer le projet GCP (`elec-forecast`)
- [x] Lancer `.\infra\setup.ps1` — APIs, SA, bucket, Artifact Registry, BQ datasets+tables, secrets
- [x] Mettre à jour `.env` avec `GCS_BUCKET=elec-forecast-931951823998`
- [x] Mettre à jour CLAUDE.md (GCP section + Current Status)

## Jobs — implémentation
- [x] `ingest/run.py` — pull eco2mix (ODRÉ API, pagination) → BQ `elec_raw.eco2mix`
- [x] `ingest/run.py` — pull Open-Meteo weather par centroïde région → BQ `elec_raw.weather`
- [ ] `features/run.py` — calculer les lags + rolling + features calendaires → BQ `elec_features.features`
- [ ] `train/run.py` — entraîner LightGBM, logguer dans MLflow, uploader modèle sur GCS
- [ ] `score/run.py` — charger modèle depuis GCS, générer prévisions 24h → BQ `elec_ml.predictions`

## Apps
- [ ] `apps/mlflow/entrypoint.sh` — tester le sync SQLite GCS↔local
- [ ] `apps/dashboard/app.py` — afficher prévisions vs actuals par région
- [ ] `apps/dashboard/app.py` — métriques monitoring (drift, MAE rolling)

## CI/CD + déploiement
- [ ] Tester `infra/cloudrun/deploy.ps1` — build + push image + déployer les 4 Cloud Run Jobs
- [ ] Connecter Cloud Build au repo GitHub (trigger sur push main)
- [ ] Tester `infra/scheduler/setup.ps1` — créer les 4 Cloud Scheduler jobs
- [ ] Vérifier les logs Cloud Run pour chaque job

## Tests & qualité
- [ ] Tests unitaires `ingest` (mock API ODRÉ)
- [ ] Tests unitaires `features` (calcul lags)
- [ ] Linter : `ruff check jobs/`

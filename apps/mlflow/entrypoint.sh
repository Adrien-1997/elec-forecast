#!/bin/bash
set -e

# Sync MLflow SQLite DB from GCS (create if absent)
DB_PATH=/tmp/mlflow.db
GCS_DB_PATH="gs://${GCS_BUCKET}/mlflow/mlflow.db"

if gsutil -q stat "${GCS_DB_PATH}"; then
    gsutil cp "${GCS_DB_PATH}" "${DB_PATH}"
else
    echo "No existing MLflow DB on GCS — starting fresh."
fi

mlflow server \
    --backend-store-uri "sqlite:///${DB_PATH}" \
    --default-artifact-root "gs://${GCS_BUCKET}/mlflow/artifacts" \
    --host 0.0.0.0 \
    --port 5000

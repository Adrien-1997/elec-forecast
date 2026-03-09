#!/bin/bash
set -e

DB_PATH=/tmp/mlflow.db

# ── Download existing SQLite DB from GCS (or start fresh) ─────────────────────
python3 - <<'EOF'
import os, sys
from google.cloud import storage

bucket_name = os.environ["GCS_BUCKET"]
client = storage.Client()
blob = client.bucket(bucket_name).blob("mlflow/mlflow.db")
if blob.exists():
    blob.download_to_filename("/tmp/mlflow.db")
    print("entrypoint: downloaded mlflow.db from GCS", flush=True)
else:
    print("entrypoint: no existing DB on GCS — starting fresh", flush=True)
EOF

# ── Periodic sync: push SQLite back to GCS every 30 s ─────────────────────────
_sync_loop() {
    while true; do
        sleep 30
        python3 - <<'EOF' 2>/dev/null || true
import os
from google.cloud import storage
storage.Client().bucket(os.environ["GCS_BUCKET"]) \
    .blob("mlflow/mlflow.db").upload_from_filename("/tmp/mlflow.db")
EOF
    done
}
_sync_loop &
SYNC_PID=$!

# ── Final sync on SIGTERM / SIGINT ─────────────────────────────────────────────
_cleanup() {
    echo "entrypoint: caught signal — final sync to GCS"
    kill "$SYNC_PID" 2>/dev/null || true
    python3 - <<'EOF'
import os
from google.cloud import storage
storage.Client().bucket(os.environ["GCS_BUCKET"]) \
    .blob("mlflow/mlflow.db").upload_from_filename("/tmp/mlflow.db")
print("entrypoint: final sync done", flush=True)
EOF
}
trap _cleanup SIGTERM SIGINT

# ── Start MLflow server ────────────────────────────────────────────────────────
exec mlflow server \
    --backend-store-uri "sqlite:////tmp/mlflow.db" \
    --default-artifact-root "gs://${GCS_BUCKET}/mlflow/artifacts" \
    --host 0.0.0.0 \
    --port "${PORT:-8080}"

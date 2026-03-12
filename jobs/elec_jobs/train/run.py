"""Train job: feature store → LightGBM → MLflow + GCS artifact.

Schedule: daily 02:00 Paris via Cloud Scheduler.

Strategy:
- Pull features at T joined with eco2mix target at T+24h (24h-ahead forecast).
- Drop rows where lag features are NULL (insufficient history).
- Time-based split: last 20% of time range → validation set.
- Log params, metrics, and model artifact to MLflow (best-effort — training
  continues and model is saved to GCS even if MLflow is unavailable).
- Upload .lgb file to GCS at models/{run_id}/model.lgb.

MLflow tracking URI:
- Local testing : set MLFLOW_TRACKING_URI=file:./mlruns in .env
- Production    : set MLFLOW_TRACKING_URI=<Cloud Run MLflow URL> in .env / Secret Manager
"""

import logging
import tempfile
import uuid
from contextlib import contextmanager
from datetime import timezone
from pathlib import Path

import lightgbm as lgb
import mlflow
import pandas as pd
from google.cloud import bigquery
from sklearn.metrics import mean_absolute_error, mean_squared_error

from elec_jobs.shared import config, gcs
from elec_jobs.shared.bq import get_client

LOG = logging.getLogger(__name__)
UTC = timezone.utc

# Sorted list of region names — must match forecast/run.py REGION_CATEGORIES exactly.
# Fixed order guarantees consistent LightGBM categorical label encoding across runs.
REGION_CATEGORIES: list[str] = sorted(v[0] for v in config.REGION_CENTROIDS.values())

FEATURE_COLS = [
    "region",                       # categorical — LightGBM native handling
    "consommation_lag_24h",
    "consommation_lag_48h",
    "consommation_lag_168h",
    "consommation_rolling_168h",
    "temperature_celsius",
    "wind_speed_kmh",
    "solar_radiation_wm2",
    "hour_of_day",
    "day_of_week",
    "is_weekend",
    "is_public_holiday_fr",
    "month",
]
TARGET_COL = "consommation"
VAL_FRACTION = 0.2   # last 20% of time range held out for validation
MIN_ROWS = 200       # safety gate — abort if not enough data after join
TRAIN_LOOKBACK_DAYS = 730  # 2-year window — captures full annual seasonality cycle


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_training_data(client: bigquery.Client) -> pd.DataFrame:
    """Pull (features at T, target consommation at T+24h) from BigQuery."""
    feature_cols_sql = ", ".join(f"f.{c}" for c in FEATURE_COLS)
    sql = f"""
    SELECT
        f.date_heure,
        f.region,
        {feature_cols_sql},
        e.consommation AS {TARGET_COL}
    FROM `{config.GCP_PROJECT_ID}.elec_features.features` AS f
    JOIN `{config.GCP_PROJECT_ID}.elec_raw.eco2mix` AS e
        ON  e.region     = f.region
        AND e.date_heure = TIMESTAMP_ADD(f.date_heure, INTERVAL 24 HOUR)
    WHERE
        f.date_heure >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {TRAIN_LOOKBACK_DAYS} DAY)
        AND f.consommation_lag_24h IS NOT NULL  -- exclude first 24h (no lag history)
        AND e.consommation     IS NOT NULL
        -- lag_168h allowed to be NULL: LightGBM handles missing values natively.
    ORDER BY f.date_heure
    """
    df = client.query(sql).to_dataframe()
    LOG.info("train: loaded %d rows", len(df))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Train / eval
# ─────────────────────────────────────────────────────────────────────────────

def _split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-based split: last VAL_FRACTION of the time range → val."""
    dt_min = df["date_heure"].min()
    dt_max = df["date_heure"].max()
    cutoff = dt_min + (dt_max - dt_min) * (1 - VAL_FRACTION)
    return df[df["date_heure"] <= cutoff], df[df["date_heure"] > cutoff]


def _train(
    train_df: pd.DataFrame, val_df: pd.DataFrame
) -> tuple[lgb.Booster, dict]:
    """Train LightGBM with early stopping; return booster + val metrics."""
    train_df = train_df.copy()
    val_df = val_df.copy()
    for col in ("is_weekend", "is_public_holiday_fr"):
        train_df[col] = train_df[col].astype(int)
        val_df[col] = val_df[col].astype(int)
    # Fixed category list ensures consistent label encoding across train and forecast runs.
    train_df["region"] = pd.Categorical(train_df["region"], categories=REGION_CATEGORIES)
    val_df["region"]   = pd.Categorical(val_df["region"],   categories=REGION_CATEGORIES)

    X_train, y_train = train_df[FEATURE_COLS], train_df[TARGET_COL]
    X_val,   y_val   = val_df[FEATURE_COLS],   val_df[TARGET_COL]

    params = {
        "objective":        "regression",
        "metric":           "mae",
        "num_leaves":       63,
        "learning_rate":    0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq":     5,
        "verbose":          -1,
    }

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval   = lgb.Dataset(X_val,   label=y_val,  reference=dtrain)

    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=500,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)],
    )

    preds = booster.predict(X_val)
    mae   = mean_absolute_error(y_val, preds)
    rmse  = mean_squared_error(y_val, preds) ** 0.5
    metrics = {"val_mae_mw": round(mae, 2), "val_rmse_mw": round(rmse, 2)}
    LOG.info("train: val MAE=%.1f MW  RMSE=%.1f MW", mae, rmse)
    return booster, metrics


def _prune_old_models(current_run_id: str, keep: int = 7) -> None:
    """Delete GCS model artifacts beyond the `keep` most recent run_ids.

    Also removes the matching MLflow artifact subdirectories so GCS doesn't
    accumulate stale blobs (MLflow SQLite DB is unaffected — UI may show
    missing artifacts for pruned runs, which is acceptable).
    """
    from google.cloud import storage
    client = storage.Client(project=config.GCP_PROJECT_ID)
    bucket = client.bucket(config.GCS_BUCKET)

    # Collect run_id → earliest blob time under models/{run_id}/
    run_times: dict[str, object] = {}
    for blob in bucket.list_blobs(prefix="models/"):
        parts = blob.name.split("/")
        if len(parts) < 3:
            continue  # skip models/latest_run_id
        run_id = parts[1]
        if run_id not in run_times or blob.time_created < run_times[run_id]:
            run_times[run_id] = blob.time_created

    if len(run_times) <= keep:
        LOG.info("train: %d model versions on GCS — no pruning needed (keep=%d)", len(run_times), keep)
        return

    sorted_runs = sorted(run_times.items(), key=lambda x: x[1], reverse=True)
    to_delete = {rid for rid, _ in sorted_runs[keep:]}
    LOG.info("train: pruning %d old model version(s) from GCS: %s", len(to_delete), to_delete)

    for rid in to_delete:
        for b in bucket.list_blobs(prefix=f"models/{rid}/"):
            b.delete()
            LOG.info("train: deleted gs://%s/%s", config.GCS_BUCKET, b.name)

    # Also remove matching MLflow artifact subdirs (mlflow/artifacts/{exp_id}/{run_id}/)
    for blob in bucket.list_blobs(prefix="mlflow/artifacts/"):
        parts = blob.name.split("/")
        # mlflow/artifacts/{exp_id}/{run_id}/...
        if len(parts) >= 4 and parts[3] in to_delete:
            blob.delete()
            LOG.info("train: deleted MLflow artifact gs://%s/%s", config.GCS_BUCKET, blob.name)


def _write_latest_run_id(run_id: str) -> None:
    """Overwrite models/latest_run_id in GCS so the score job knows which model to use."""
    from google.cloud import storage
    client = storage.Client(project=config.GCP_PROJECT_ID)
    client.bucket(config.GCS_BUCKET).blob("models/latest_run_id").upload_from_string(run_id)
    LOG.info("train: pointer models/latest_run_id → %s", run_id)


def _fetch_identity_token(audience: str) -> str | None:
    """Fetch an OIDC identity token from the GCP metadata server.

    Used for Cloud Run → Cloud Run authenticated calls. Returns None outside GCP.
    """
    import urllib.request
    url = (
        "http://metadata.google.internal/computeMetadata/v1/instance"
        f"/service-accounts/default/identity?audience={audience}"
    )
    try:
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        return None


@contextmanager
def _mlflow_run():
    """Start an MLflow run, or yield a stub run_id if MLflow is unavailable.

    Yields (run_id, mlflow_active). Training always completes; MLflow is best-effort.
    Short HTTP timeout (10 s, 2 retries) so the fallback is fast on cold-start 503s.
    For Cloud Run → Cloud Run auth: fetches an OIDC identity token from the metadata
    server and sets MLFLOW_TRACKING_TOKEN (Bearer auth expected by Cloud Run IAM).
    """
    import os
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "2")
    tracking_uri = config.MLFLOW_TRACKING_URI
    # Inject identity token when pointing at a remote (non-file) MLflow server.
    if not tracking_uri.startswith("file:") and "MLFLOW_TRACKING_TOKEN" not in os.environ:
        token = _fetch_identity_token(tracking_uri)
        if token:
            os.environ["MLFLOW_TRACKING_TOKEN"] = token
            LOG.info("train: injected GCP identity token for MLflow auth")
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(config.MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run() as run:
            LOG.info("train: MLflow run_id=%s", run.info.run_id)
            yield run.info.run_id, True
    except Exception as exc:
        LOG.warning("train: MLflow unavailable (%s) — training without experiment tracking", exc)
        yield uuid.uuid4().hex, False


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()

    df = _load_training_data(client)
    if len(df) < MIN_ROWS:
        LOG.warning("train: only %d rows after join — need at least %d, aborting", len(df), MIN_ROWS)
        return

    train_df, val_df = _split(df)
    LOG.info("train: %d train rows / %d val rows", len(train_df), len(val_df))

    with _mlflow_run() as (run_id, mlflow_active):
        booster, metrics = _train(train_df, val_df)

        if mlflow_active:
            mlflow.log_params({
                "n_train":        len(train_df),
                "n_val":          len(val_df),
                "val_fraction":   VAL_FRACTION,
                "features":       FEATURE_COLS,
                "n_features":     len(FEATURE_COLS),
                "best_iteration": booster.best_iteration,
            })
            mlflow.log_metrics(metrics)

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.lgb"
            booster.save_model(str(model_path))

            blob_name = f"models/{run_id}/model.lgb"
            gcs.upload(model_path, blob_name)
            LOG.info("train: uploaded gs://%s/%s", config.GCS_BUCKET, blob_name)

            if mlflow_active:
                mlflow.log_artifact(str(model_path), artifact_path="model")

        _write_latest_run_id(run_id)
        if mlflow_active:
            mlflow.set_tag("gcs_model_blob", blob_name)

    _prune_old_models(current_run_id=run_id, keep=7)
    LOG.info("train: done — run_id=%s  val_mae=%.1f MW", run_id, metrics["val_mae_mw"])


if __name__ == "__main__":
    main()

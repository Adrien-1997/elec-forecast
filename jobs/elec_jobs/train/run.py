"""Train job: feature store → LightGBM → MLflow + GCS artifact.

Schedule: weekly (Sunday 2am) via Cloud Scheduler.

Strategy:
- Pull features at T joined with eco2mix target at T+24h (24h-ahead forecast).
- Drop rows where lag features are NULL (insufficient history).
- Time-based split: last 20% of time range → validation set.
- Log params, metrics, and model artifact to MLflow.
- Upload .lgb file to GCS at models/{run_id}/model.lgb.

MLflow tracking URI:
- Local testing : set MLFLOW_TRACKING_URI=file:./mlruns in .env
- Production    : set MLFLOW_TRACKING_URI=<Cloud Run MLflow URL> in .env / Secret Manager
"""

import logging
import tempfile
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

FEATURE_COLS = [
    "consommation_lag_24h",
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
        f.consommation_lag_24h IS NOT NULL  -- exclude first 24h (no lag history)
        AND e.consommation     IS NOT NULL
        -- lag_168h allowed to be NULL: LightGBM handles missing values natively.
        -- Will be non-NULL once we have 14d+ of raw data (backfill or steady state).
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


def _write_latest_run_id(run_id: str) -> None:
    """Overwrite models/latest_run_id in GCS so the score job knows which model to use."""
    from google.cloud import storage
    client = storage.Client(project=config.GCP_PROJECT_ID)
    client.bucket(config.GCS_BUCKET).blob("models/latest_run_id").upload_from_string(run_id)
    LOG.info("train: pointer models/latest_run_id → %s", run_id)


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

    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(config.MLFLOW_EXPERIMENT_NAME)

    with mlflow.start_run() as run:
        run_id = run.info.run_id
        LOG.info("train: MLflow run_id=%s", run_id)

        booster, metrics = _train(train_df, val_df)

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

            mlflow.log_artifact(str(model_path), artifact_path="model")

        # Write pointer so score job always knows which model to load
        _write_latest_run_id(run_id)
        mlflow.set_tag("gcs_model_blob", blob_name)

    LOG.info("train: done — run_id=%s  val_mae=%.1f MW", run_id, metrics["val_mae_mw"])


if __name__ == "__main__":
    main()

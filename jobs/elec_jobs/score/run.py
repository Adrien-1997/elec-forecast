"""Score job: load champion model from GCS → 24h-ahead forecasts → BQ predictions.

Schedule: every 15 min via Cloud Scheduler.

Strategy:
- Read models/latest_run_id from GCS to identify the champion model.
- Download models/{run_id}/model.lgb and load into LightGBM.
- Pull the most recent 24h of feature rows from elec_features.features.
  These rows at T produce forecasts for T+24h (matching train-time target definition).
- Append predictions to elec_ml.predictions.
  Dashboard uses MAX(scored_at) to select the latest forecast per horizon.
"""

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from google.cloud import bigquery, storage

from elec_jobs.shared import config, gcs
from elec_jobs.shared.bq import get_client, load_dataframe

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


# ─────────────────────────────────────────────────────────────────────────────
# Model loading
# ─────────────────────────────────────────────────────────────────────────────

def _get_latest_run_id() -> str:
    client = storage.Client(project=config.GCP_PROJECT_ID)
    return client.bucket(config.GCS_BUCKET).blob("models/latest_run_id").download_as_text().strip()


def _load_model(run_id: str) -> lgb.Booster:
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / "model.lgb"
        gcs.download(f"models/{run_id}/model.lgb", local_path)
        return lgb.Booster(model_file=str(local_path))


# ─────────────────────────────────────────────────────────────────────────────
# Feature loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_score_features(client: bigquery.Client) -> pd.DataFrame:
    """Pull the most recent 24h of feature rows.

    Feature row at T → prediction for T+24h.
    We filter lag_24h IS NOT NULL to exclude rows with no history.
    """
    sql = f"""
    SELECT *
    FROM `{config.GCP_PROJECT_ID}.elec_features.features`
    WHERE
        date_heure >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        AND consommation_lag_24h IS NOT NULL
    ORDER BY date_heure, region
    """
    df = client.query(sql).to_dataframe()
    LOG.info("score: %d feature rows to score", len(df))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    now = datetime.now(UTC)

    run_id = _get_latest_run_id()
    LOG.info("score: loading model run_id=%s", run_id)
    booster = _load_model(run_id)

    df = _load_score_features(client)
    if df.empty:
        LOG.info("score: no feature rows — skipping")
        return

    X = df[FEATURE_COLS].copy()
    for col in ("is_weekend", "is_public_holiday_fr"):
        X[col] = X[col].astype(int)

    preds = booster.predict(X)

    out = pd.DataFrame({
        "forecast_horizon_dt": df["date_heure"] + pd.Timedelta(hours=24),
        "region":              df["region"].values,
        "predicted_mw":        preds,
        "model_version":       run_id,
        "scored_at":           now,
    })

    LOG.info("score: writing %d predictions to BQ", len(out))
    load_dataframe(out, f"{config.GCP_PROJECT_ID}.elec_ml.predictions")
    LOG.info("score: done")


if __name__ == "__main__":
    main()

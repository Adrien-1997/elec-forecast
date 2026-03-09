"""Walk-forward backfill: reproduce the daily pipeline (train 02:00 → forecast 06:00)
for each day in a historical date range with honest time-series evaluation.

Each iteration:
  1. Train a model on features/targets with cutoff at D 02:00 Paris
     (only data that would have been available that morning).
  2. Forecast the 96 slots for D 06:00 Paris → D+1 05:45 Paris.
  3. UPSERT predictions into elec_ml.predictions.

No data leakage: each model sees strictly less data than the next day's model.

Usage:
  # From repo root, with venv activated:
  python scripts/backfill_walk_forward.py

Env vars:
  BACKFILL_WF_START_DATE  YYYY-MM-DD  default: 7 days ago
  BACKFILL_WF_END_DATE    YYYY-MM-DD  default: today
  BACKFILL_WF_CLEAN_GCS   1|0         default: 1  — delete all GCS models before starting
"""

import logging
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import lightgbm as lgb
import pandas as pd
import requests
from google.cloud import bigquery, storage
from sklearn.metrics import mean_absolute_error

from elec_jobs.shared import config, gcs
from elec_jobs.shared.bq import get_client, merge_to_bq

LOG = logging.getLogger(__name__)
UTC   = timezone.utc
PARIS = ZoneInfo("Europe/Paris")

TRAIN_LOOKBACK_DAYS = 730
FEATURE_COLS = [
    "region",
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
TARGET_COL        = "consommation"
VAL_FRACTION      = 0.2
MIN_ROWS          = 200
ECO_LOOKBACK_H    = 216   # 9 days — same as forecast/run.py
HORIZON_STEPS     = 96
REGION_CATEGORIES = sorted(v[0] for v in config.REGION_CENTROIDS.values())


# ─────────────────────────────────────────────────────────────────────────────
# GCS cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _clean_gcs_models() -> None:
    """Delete all blobs under gs://{bucket}/models/."""
    client = storage.Client(project=config.GCP_PROJECT_ID)
    bucket = client.bucket(config.GCS_BUCKET)
    blobs  = list(bucket.list_blobs(prefix="models/"))
    if not blobs:
        LOG.info("clean: no model blobs to delete")
        return
    for blob in blobs:
        blob.delete()
        LOG.info("clean: deleted gs://%s/%s", config.GCS_BUCKET, blob.name)
    LOG.info("clean: removed %d blobs", len(blobs))


# ─────────────────────────────────────────────────────────────────────────────
# Training (cutoff-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _load_training_data(client: bigquery.Client, cutoff_utc: datetime) -> pd.DataFrame:
    """Features JOIN eco2mix targets, strictly before cutoff_utc."""
    feature_cols_sql = ", ".join(f"f.{c}" for c in FEATURE_COLS)
    cutoff_str   = cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    lookback_str = (cutoff_utc - timedelta(days=TRAIN_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = f"""
    SELECT f.date_heure, f.region, {feature_cols_sql}, e.consommation AS {TARGET_COL}
    FROM `{config.GCP_PROJECT_ID}.elec_features.features` AS f
    JOIN `{config.GCP_PROJECT_ID}.elec_raw.eco2mix` AS e
        ON  e.region     = f.region
        AND e.date_heure = TIMESTAMP_ADD(f.date_heure, INTERVAL 24 HOUR)
    WHERE
        f.date_heure >= TIMESTAMP('{lookback_str}')
        AND f.date_heure  < TIMESTAMP('{cutoff_str}')
        AND e.date_heure  < TIMESTAMP('{cutoff_str}')
        AND f.consommation_lag_24h IS NOT NULL
        AND e.consommation         IS NOT NULL
    ORDER BY f.date_heure
    """
    df = client.query(sql).to_dataframe()
    LOG.info("  train: loaded %d rows (cutoff=%s)", len(df), cutoff_str)
    return df


def _split(df: pd.DataFrame):
    dt_min = df["date_heure"].min()
    dt_max = df["date_heure"].max()
    cutoff = dt_min + (dt_max - dt_min) * (1 - VAL_FRACTION)
    return df[df["date_heure"] <= cutoff], df[df["date_heure"] > cutoff]


def _train(train_df: pd.DataFrame, val_df: pd.DataFrame) -> tuple[lgb.Booster, float]:
    train_df, val_df = train_df.copy(), val_df.copy()
    for col in ("is_weekend", "is_public_holiday_fr"):
        train_df[col] = train_df[col].astype(int)
        val_df[col]   = val_df[col].astype(int)
    train_df["region"] = pd.Categorical(train_df["region"], categories=REGION_CATEGORIES)
    val_df["region"]   = pd.Categorical(val_df["region"],   categories=REGION_CATEGORIES)

    X_train, y_train = train_df[FEATURE_COLS], train_df[TARGET_COL]
    X_val,   y_val   = val_df[FEATURE_COLS],   val_df[TARGET_COL]

    params = {
        "objective": "regression", "metric": "mae",
        "num_leaves": 63, "learning_rate": 0.05,
        "feature_fraction": 0.8, "bagging_fraction": 0.8,
        "bagging_freq": 5, "verbose": -1,
    }
    dtrain  = lgb.Dataset(X_train, label=y_train)
    dval    = lgb.Dataset(X_val,   label=y_val, reference=dtrain)
    booster = lgb.train(
        params, dtrain, num_boost_round=500,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)],
    )
    mae = mean_absolute_error(y_val, booster.predict(X_val))
    LOG.info("  train: val MAE=%.1f MW", mae)
    return booster, mae


def _save_model(booster: lgb.Booster) -> str:
    """Save model to GCS and update models/latest_run_id pointer. Returns run_id."""
    run_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = Path(tmpdir) / "model.lgb"
        booster.save_model(str(model_path))
        gcs.upload(model_path, f"models/{run_id}/model.lgb")
    gcs_client = storage.Client(project=config.GCP_PROJECT_ID)
    gcs_client.bucket(config.GCS_BUCKET).blob("models/latest_run_id").upload_from_string(run_id)
    LOG.info("  train: saved model → gs://%s/models/%s/model.lgb", config.GCS_BUCKET, run_id)
    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# Forecast (anchor-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _slots_for_day(d: date) -> list[pd.Timestamp]:
    """96 × 15-min slots anchored at 06:00 Paris on d."""
    start = datetime(d.year, d.month, d.day, 6, 0, tzinfo=PARIS)
    return list(pd.date_range(start, periods=HORIZON_STEPS, freq="15min"))


def _load_eco_at(client: bigquery.Client, anchor_utc: datetime) -> pd.DataFrame:
    """Eco2mix deduped, covering ECO_LOOKBACK_H before anchor (simulates data at forecast time)."""
    anchor_str = anchor_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = f"""
    SELECT date_heure, region, consommation
    FROM (
        SELECT date_heure, region, consommation,
               ROW_NUMBER() OVER (PARTITION BY region, date_heure ORDER BY _ingested_at DESC) AS rn
        FROM `{config.GCP_PROJECT_ID}.{config.BQ_DATASET_RAW}.eco2mix`
        WHERE date_heure >= TIMESTAMP_SUB(TIMESTAMP('{anchor_str}'), INTERVAL {ECO_LOOKBACK_H} HOUR)
          AND date_heure  < TIMESTAMP('{anchor_str}')
          AND consommation IS NOT NULL
    )
    WHERE rn = 1
    """
    return client.query(sql).to_dataframe()


def _fetch_weather(d: date, now: datetime) -> pd.DataFrame:
    """Fetch hourly weather for day d.

    Uses Open-Meteo archive API for dates older than 5 days (observed values),
    and the forecast API with past_days for recent dates.
    """
    frames = []
    days_ago = (now.date() - d).days

    for _code, (region, lat, lon) in config.REGION_CENTROIDS.items():
        if days_ago >= 5:
            resp = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": lat, "longitude": lon,
                    "start_date": d.isoformat(),
                    "end_date":   (d + timedelta(days=1)).isoformat(),
                    "hourly": "temperature_2m,wind_speed_10m,direct_radiation",
                    "timezone": "UTC",
                },
                timeout=60,
            )
        else:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "hourly": "temperature_2m,wind_speed_10m,direct_radiation",
                    "timezone": "UTC",
                    "past_days": days_ago + 1,
                    "forecast_days": 2,
                },
                timeout=30,
            )
        resp.raise_for_status()
        h = resp.json()["hourly"]
        frames.append(pd.DataFrame({
            "hour_dt":             pd.to_datetime(h["time"], utc=True),
            "region":              region,
            "temperature_celsius": h["temperature_2m"],
            "wind_speed_kmh":      h["wind_speed_10m"],
            "solar_radiation_wm2": h["direct_radiation"],
        }))
    return pd.concat(frames, ignore_index=True)


def _build_lag_features(eco: pd.DataFrame, slots: list, regions: list) -> pd.DataFrame:
    eco_by_region = {}
    for region in regions:
        r = (
            eco[eco["region"] == region]
            .set_index("date_heure")["consommation"]
            .sort_index()
        )
        eco_by_region[region] = r

    rows = []
    n_fallback = 0
    for slot in slots:
        T         = slot - pd.Timedelta(hours=24)
        lag24_ts  = T - pd.Timedelta(hours=24)
        lag48_ts  = T - pd.Timedelta(hours=48)
        lag168_ts = T - pd.Timedelta(hours=168)
        roll_end  = T - pd.Timedelta(minutes=15)

        for region in regions:
            r = eco_by_region.get(region, pd.Series(dtype=float))
            lag24  = float(r.loc[lag24_ts])  if lag24_ts  in r.index else None
            lag48  = float(r.loc[lag48_ts])  if lag48_ts  in r.index else None
            lag168 = float(r.loc[lag168_ts]) if lag168_ts in r.index else None
            if lag24 is None and lag48 is not None:
                lag24 = lag48
                n_fallback += 1
            rolling = None
            if not r.empty:
                window  = r.loc[lag168_ts:roll_end]
                rolling = float(window.mean()) if len(window) > 0 else None
            rows.append({
                "forecast_horizon_dt":       slot,
                "region":                    region,
                "consommation_lag_24h":      lag24,
                "consommation_lag_48h":      lag48,
                "consommation_lag_168h":     lag168,
                "consommation_rolling_168h": rolling,
            })
    if n_fallback:
        LOG.warning("  forecast: lag_24h missing for %d rows — used lag_48h fallback", n_fallback)
    return pd.DataFrame(rows)


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    paris = df["forecast_horizon_dt"].dt.tz_convert("Europe/Paris")
    df = df.copy()
    df["hour_of_day"] = paris.dt.hour
    df["day_of_week"] = paris.dt.dayofweek
    df["month"]       = paris.dt.month
    df["is_weekend"]  = df["day_of_week"] >= 5
    years   = paris.dt.year.unique().tolist()
    fr_hols = holidays.France(years=years)
    df["is_public_holiday_fr"] = paris.dt.date.map(lambda d: d in fr_hols)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now   = datetime.now(UTC)
    today = now.astimezone(PARIS).date()

    start = date.fromisoformat(os.getenv("BACKFILL_WF_START_DATE", (today - timedelta(days=7)).isoformat()))
    end   = date.fromisoformat(os.getenv("BACKFILL_WF_END_DATE",   today.isoformat()))
    clean = os.getenv("BACKFILL_WF_CLEAN_GCS", "1") == "1"

    LOG.info("walk-forward backfill: %s → %s  clean_gcs=%s", start, end, clean)

    if clean:
        LOG.info("==> Cleaning GCS models/")
        _clean_gcs_models()

    client = get_client()

    d = start
    while d <= end:
        LOG.info("=== Day %s ===", d)

        # Simulate train at 02:00 Paris on day d
        train_cutoff = datetime(d.year, d.month, d.day, 2, 0, tzinfo=PARIS).astimezone(UTC)
        df = _load_training_data(client, train_cutoff)
        if len(df) < MIN_ROWS:
            LOG.warning("  train: only %d rows — skipping day %s", len(df), d)
            d += timedelta(days=1)
            continue

        train_df, val_df = _split(df)
        LOG.info("  train: %d train / %d val rows", len(train_df), len(val_df))
        booster, mae = _train(train_df, val_df)
        run_id = _save_model(booster)

        # Simulate forecast at 06:00 Paris on day d
        forecast_anchor = datetime(d.year, d.month, d.day, 6, 0, tzinfo=PARIS).astimezone(UTC)
        slots   = _slots_for_day(d)
        eco     = _load_eco_at(client, forecast_anchor)
        weather = _fetch_weather(d, now)

        df_feat = _build_lag_features(eco, slots, REGION_CATEGORIES)
        df_feat["hour_dt"] = df_feat["forecast_horizon_dt"].dt.floor("h")
        df_feat = df_feat.merge(weather, on=["hour_dt", "region"], how="left").drop(columns=["hour_dt"])
        df_feat = _add_calendar_features(df_feat)

        X = df_feat[FEATURE_COLS].copy()
        X["region"]               = pd.Categorical(X["region"], categories=REGION_CATEGORIES)
        X["is_weekend"]           = X["is_weekend"].astype(int)
        X["is_public_holiday_fr"] = X["is_public_holiday_fr"].astype(int)

        preds = booster.predict(X)
        out = pd.DataFrame({
            "forecast_horizon_dt": df_feat["forecast_horizon_dt"],
            "region":              df_feat["region"],
            "predicted_mw":        preds,
            "model_version":       run_id,
            "forecast_date":       d,
            "forecasted_at":       forecast_anchor,
        })

        merge_to_bq(
            client, out, config.GCP_PROJECT_ID,
            f"{config.BQ_DATASET_ML}.predictions",
            key_cols=("forecast_horizon_dt", "region"),
        )
        LOG.info("  forecast: upserted %d predictions  val_mae=%.1f MW  run_id=%s", len(out), mae, run_id)

        d += timedelta(days=1)

    LOG.info("walk-forward backfill: done — run metrics job next")


if __name__ == "__main__":
    main()

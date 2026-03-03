"""Forecast job: build next-24h feature rows → score with champion model → BQ predictions.

Schedule: daily at 06:00 Europe/Paris (04:00 UTC) via Cloud Scheduler.

Strategy:
- Generate 96 future 15-min slots (next 24h from the nearest future 15-min boundary).
- Pull last 8 days of eco2mix from BQ → compute lag_24h, lag_168h, rolling_168h in Python.
- Fetch Open-Meteo weather forecast (forecast_days=2) per region centroid — no BQ round-trip.
- Add calendar features (Paris timezone) + French public holiday flag.
- Score 1,152 rows (96 slots × 12 regions) with the champion LightGBM model.
- UPSERT predictions into elec_ml.predictions on (forecast_horizon_dt, region)
  so re-running the daily job is idempotent.

Region is encoded as a pandas Categorical and passed to LightGBM as a categorical feature.
Training (train/run.py) uses the same FEATURE_COLS and the same REGION_CATEGORIES order —
always use pd.Categorical with fixed categories to ensure consistent label encoding.
"""

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import holidays
import lightgbm as lgb
import pandas as pd
import requests
from google.cloud import bigquery, storage

from elec_jobs.shared import config, gcs
from elec_jobs.shared.bq import get_client, merge_to_bq

LOG = logging.getLogger(__name__)
UTC = timezone.utc

# Sorted list of region names — must match train/run.py exactly.
REGION_CATEGORIES: list[str] = sorted(v[0] for v in config.REGION_CENTROIDS.values())

FEATURE_COLS = [
    "region",                       # categorical — LightGBM native handling
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

HORIZON_STEPS   = 96    # 15-min slots in 24 h
ECO_LOOKBACK_H  = 192   # 8 days: covers 168 h lag + buffer


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
# Feature assembly
# ─────────────────────────────────────────────────────────────────────────────

def _generate_slots(now: datetime) -> list[pd.Timestamp]:
    """96 future 15-min slots starting from the next 15-min boundary."""
    start = pd.Timestamp(now).ceil("15min")
    return list(pd.date_range(start, periods=HORIZON_STEPS, freq="15min"))


def _load_eco_history(client: bigquery.Client) -> pd.DataFrame:
    """Last 8 days of eco2mix, deduped by latest ingestion."""
    sql = f"""
    SELECT date_heure, region, consommation
    FROM (
        SELECT date_heure, region, consommation,
               ROW_NUMBER() OVER (
                   PARTITION BY region, date_heure
                   ORDER BY _ingested_at DESC
               ) AS rn
        FROM `{config.GCP_PROJECT_ID}.{config.BQ_DATASET_RAW}.eco2mix`
        WHERE date_heure >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {ECO_LOOKBACK_H} HOUR)
          AND consommation IS NOT NULL
    )
    WHERE rn = 1
    """
    return client.query(sql).to_dataframe()


def _build_lag_features(
    eco: pd.DataFrame,
    slots: list[pd.Timestamp],
    regions: list[str],
) -> pd.DataFrame:
    """Compute lag_24h, lag_168h, rolling_168h for every (slot, region) pair.

    Uses pandas index lookups — O(steps × regions × log N_eco).
    rolling_168h = mean of eco[slot-168h : slot] for the region.
    """
    eco_by_region: dict[str, pd.Series] = {}
    for region in regions:
        r = (
            eco[eco["region"] == region]
            .set_index("date_heure")["consommation"]
            .sort_index()
        )
        eco_by_region[region] = r

    rows = []
    for slot in slots:
        lag24_ts  = slot - pd.Timedelta(hours=24)
        lag168_ts = slot - pd.Timedelta(hours=168)

        for region in regions:
            r = eco_by_region.get(region, pd.Series(dtype=float))

            lag24  = float(r.loc[lag24_ts])  if lag24_ts  in r.index else None
            lag168 = float(r.loc[lag168_ts]) if lag168_ts in r.index else None

            if r.empty:
                rolling = None
            else:
                window  = r.loc[lag168_ts:slot]
                rolling = float(window.mean()) if len(window) > 0 else None

            rows.append({
                "forecast_horizon_dt":    slot,
                "region":                 region,
                "consommation_lag_24h":   lag24,
                "consommation_lag_168h":  lag168,
                "consommation_rolling_168h": rolling,
            })

    return pd.DataFrame(rows)


def _fetch_weather_forecast() -> pd.DataFrame:
    """Fetch Open-Meteo hourly weather forecast (next 2 days) for all 12 region centroids."""
    frames = []
    for _code, (region, lat, lon) in config.REGION_CENTROIDS.items():
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":       lat,
                "longitude":      lon,
                "hourly":         "temperature_2m,wind_speed_10m,direct_radiation",
                "timezone":       "UTC",
                "past_days":      0,
                "forecast_days":  2,
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


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    paris = df["forecast_horizon_dt"].dt.tz_convert("Europe/Paris")
    df = df.copy()
    df["hour_of_day"] = paris.dt.hour
    df["day_of_week"] = paris.dt.dayofweek   # 0=Mon … 6=Sun
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
    client = get_client()
    now    = datetime.now(UTC)

    run_id = _get_latest_run_id()
    LOG.info("forecast: loading model run_id=%s", run_id)
    booster = _load_model(run_id)

    slots   = _generate_slots(now)
    regions = REGION_CATEGORIES
    LOG.info("forecast: %d slots × %d regions = %d rows", len(slots), len(regions), len(slots) * len(regions))

    # ── Lag features from BQ eco2mix history ─────────────────────────────────
    eco = _load_eco_history(client)
    LOG.info("forecast: eco history — %d rows covering %d regions", len(eco), eco["region"].nunique())
    df = _build_lag_features(eco, slots, regions)

    # ── Weather forecast from Open-Meteo ─────────────────────────────────────
    LOG.info("forecast: fetching weather forecast from Open-Meteo")
    weather = _fetch_weather_forecast()
    df["hour_dt"] = df["forecast_horizon_dt"].dt.floor("h")
    df = df.merge(weather, on=["hour_dt", "region"], how="left").drop(columns=["hour_dt"])

    # ── Calendar features ─────────────────────────────────────────────────────
    df = _add_calendar_features(df)

    # ── Score ─────────────────────────────────────────────────────────────────
    X = df[FEATURE_COLS].copy()
    X["region"]                = pd.Categorical(X["region"], categories=REGION_CATEGORIES)
    X["is_weekend"]            = X["is_weekend"].astype(int)
    X["is_public_holiday_fr"]  = X["is_public_holiday_fr"].astype(int)

    preds = booster.predict(X)
    LOG.info("forecast: scored %d predictions", len(preds))

    # ── Write predictions (UPSERT on forecast_horizon_dt + region) ────────────
    out = pd.DataFrame({
        "forecast_horizon_dt": df["forecast_horizon_dt"],
        "region":              df["region"],
        "predicted_mw":        preds,
        "model_version":       run_id,
        "forecast_date":       now.date(),
        "forecasted_at":       now,
    })

    merge_to_bq(
        client, out, config.GCP_PROJECT_ID,
        f"{config.BQ_DATASET_ML}.predictions",
        key_cols=("forecast_horizon_dt", "region"),
    )
    LOG.info("forecast: done — %d predictions written", len(out))


if __name__ == "__main__":
    main()

"""Reingest job: refresh last 7 days of eco2mix + weather → BQ raw tables.

Schedule: Weekly (Sunday 01:00 Paris) — before features (01:50) and train (02:00).

Why this exists:
- eco2mix: ODRÉ regions publish with variable lag; slots initially ingested with
  partial region coverage get corrected values as late publishers catch up.
- Weather: replaces forecast-based values (from /v1/forecast) with accurate
  observed values from Open-Meteo archive API.

Both UPSERTs are idempotent (MERGE on (date_heure, region)).
"""

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client, merge_to_bq
from elec_jobs.ingest.run import fetch_eco2mix

LOG = logging.getLogger(__name__)
UTC = timezone.utc
LOOKBACK_HOURS = 168  # 7 days


def _fetch_weather_archive(start: date, end: date) -> pd.DataFrame:
    """Fetch hourly weather from Open-Meteo archive API for all 12 region centroids.

    Uses observed values (more accurate than the forecast-based values stored by
    the ingest job, which uses /v1/forecast with past_days).
    """
    frames = []
    now = datetime.now(UTC)

    for _code, (region, lat, lon) in config.REGION_CENTROIDS.items():
        resp = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":   lat,
                "longitude":  lon,
                "start_date": start.isoformat(),
                "end_date":   end.isoformat(),
                "hourly":     "temperature_2m,wind_speed_10m,direct_radiation",
                "timezone":   "UTC",
            },
            timeout=60,
        )
        resp.raise_for_status()
        h = resp.json()["hourly"]

        frames.append(pd.DataFrame({
            "date_heure":           pd.to_datetime(h["time"], utc=True),
            "region":               region,
            "temperature_celsius":  h["temperature_2m"],
            "wind_speed_kmh":       h["wind_speed_10m"],
            "solar_radiation_wm2":  h["direct_radiation"],
            "_ingested_at":         now,
        }))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    now = datetime.now(UTC)
    since = now - timedelta(hours=LOOKBACK_HOURS)

    eco2mix_table = f"{config.BQ_DATASET_RAW}.eco2mix"
    weather_table = f"{config.BQ_DATASET_RAW}.weather"

    # ── eco2mix ───────────────────────────────────────────────────────────────
    LOG.info("reingest: eco2mix since %s", since.isoformat())
    df_eco2mix = fetch_eco2mix(since)
    if df_eco2mix.empty:
        LOG.info("eco2mix: no records")
    else:
        merge_to_bq(client, df_eco2mix, config.GCP_PROJECT_ID, eco2mix_table)
        LOG.info("eco2mix: upserted %d rows", len(df_eco2mix))

    # ── weather (archive — observed values) ───────────────────────────────────
    start_date = since.date()
    end_date = now.date()
    LOG.info("reingest: weather %s → %s (archive API)", start_date, end_date)
    df_weather = _fetch_weather_archive(start_date, end_date)
    if df_weather.empty:
        LOG.info("weather: no records")
    else:
        merge_to_bq(client, df_weather, config.GCP_PROJECT_ID, weather_table)
        LOG.info("weather: upserted %d rows", len(df_weather))

    LOG.info("reingest: done")


if __name__ == "__main__":
    main()

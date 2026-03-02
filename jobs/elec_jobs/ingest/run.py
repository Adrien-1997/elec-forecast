"""Ingest job: pull eco2mix (ODRÉ) + Open-Meteo weather → BQ raw tables.

Schedule: every 30 min via Cloud Scheduler.

Strategy:
- Query max(date_heure) already in BQ → fetch only new records.
- First run (empty table): falls back to DEFAULT_LOOKBACK_DAYS.
- Weather is stored at hourly granularity; the features job joins by
  TIMESTAMP_TRUNC(date_heure, HOUR).
- Dedup: simple append; downstream features job uses MAX(_ingested_at)
  if duplicates appear due to retries.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from google.cloud import bigquery

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client

LOG = logging.getLogger(__name__)
UTC = timezone.utc
DEFAULT_LOOKBACK_DAYS = 7  # used on first run or after a gap


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bq_max_date_heure(client: bigquery.Client, table: str) -> datetime | None:
    """Return max(date_heure) from a BQ table, or None if the table is empty."""
    sql = f"SELECT MAX(date_heure) AS max_dt FROM `{config.GCP_PROJECT_ID}.{table}`"
    rows = list(client.query(sql).result())
    val = rows[0]["max_dt"]
    if val is None:
        return None
    return val if val.tzinfo else val.replace(tzinfo=UTC)


# ─────────────────────────────────────────────────────────────────────────────
# eco2mix
# ─────────────────────────────────────────────────────────────────────────────

def fetch_eco2mix(since: datetime) -> pd.DataFrame:
    """Pull eco2mix records from ODRÉ API (paginated) for all regions since `since`.

    Returns a DataFrame with columns:
        date_heure, region, consommation, _ingested_at
    """
    url = f"{config.ODRE_BASE_URL}/catalog/datasets/{config.ODRE_REALTIME_DATASET}/records"
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "where": f"consommation is not null AND date_heure > '{since_str}'",
        "order_by": "date_heure asc",
        "limit": 100,
        "offset": 0,
    }

    rows = []
    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("results", [])
        rows.extend(batch)
        LOG.debug("eco2mix: fetched %d records (offset=%d)", len(batch), params["offset"])
        if len(batch) < 100:
            break
        params["offset"] += 100

    if not rows:
        return pd.DataFrame()

    now = datetime.now(UTC)
    return pd.DataFrame([
        {
            "date_heure":    pd.Timestamp(r["date_heure"]).tz_convert("UTC"),
            "region":        r["libelle_region"],
            "consommation":  float(r["consommation"]) if r.get("consommation") is not None else None,
            "_ingested_at":  now,
        }
        for r in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Weather
# ─────────────────────────────────────────────────────────────────────────────

def fetch_weather(since: datetime) -> pd.DataFrame:
    """Fetch hourly weather from Open-Meteo for all 12 region centroids.

    Returns a DataFrame with columns:
        date_heure, region, temperature_celsius, wind_speed_kmh,
        solar_radiation_wm2, _ingested_at
    """
    past_days = max(1, (datetime.now(UTC) - since).days + 1)
    past_days = min(past_days, 92)  # Open-Meteo free tier cap

    frames = []
    now = datetime.now(UTC)

    for _code, (region, lat, lon) in config.REGION_CENTROIDS.items():
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":     lat,
                "longitude":    lon,
                "hourly":       "temperature_2m,wind_speed_10m,direct_radiation",
                "timezone":     "UTC",
                "past_days":    past_days,
                "forecast_days": 1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        h = resp.json()["hourly"]

        df = pd.DataFrame({
            "date_heure":           pd.to_datetime(h["time"], utc=True),
            "region":               region,
            "temperature_celsius":  h["temperature_2m"],
            "wind_speed_kmh":       h["wind_speed_10m"],
            "solar_radiation_wm2":  h["direct_radiation"],
            "_ingested_at":         now,
        })
        df = df[df["date_heure"] > since]
        frames.append(df)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# BQ writes
# ─────────────────────────────────────────────────────────────────────────────

def _append_to_bq(client: bigquery.Client, df: pd.DataFrame, table: str) -> None:
    """Append a DataFrame to a fully-qualified BQ table."""
    job = client.load_table_from_dataframe(
        df,
        f"{config.GCP_PROJECT_ID}.{table}",
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
    )
    job.result()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    now = datetime.now(UTC)

    # ── eco2mix ──────────────────────────────────────────────────────────────
    eco2mix_table = f"{config.BQ_DATASET_RAW}.eco2mix"
    max_dt = _bq_max_date_heure(client, eco2mix_table)
    since_eco2mix = max_dt if max_dt else now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    LOG.info("eco2mix: fetching since %s", since_eco2mix.isoformat())

    df_eco2mix = fetch_eco2mix(since_eco2mix)
    if df_eco2mix.empty:
        LOG.info("eco2mix: no new records")
    else:
        LOG.info("eco2mix: inserting %d rows", len(df_eco2mix))
        _append_to_bq(client, df_eco2mix, eco2mix_table)
        LOG.info("eco2mix: done")

    # ── weather ──────────────────────────────────────────────────────────────
    weather_table = f"{config.BQ_DATASET_RAW}.weather"
    max_dt_w = _bq_max_date_heure(client, weather_table)
    since_weather = max_dt_w if max_dt_w else now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    LOG.info("weather: fetching since %s", since_weather.isoformat())

    df_weather = fetch_weather(since_weather)
    if df_weather.empty:
        LOG.info("weather: no new records")
    else:
        LOG.info("weather: inserting %d rows", len(df_weather))
        _append_to_bq(client, df_weather, weather_table)
        LOG.info("weather: done")


if __name__ == "__main__":
    main()

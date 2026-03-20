"""Ingest job: pull eco2mix (ODRÉ) + Open-Meteo weather → BQ raw tables.

Schedule: every 30 min via Cloud Scheduler.

Strategy:
- Query max(date_heure) already in BQ, then subtract OVERLAP_HOURS to re-fetch
  recent slots — ODRÉ regions publish with variable lag (up to ~2h), so without
  the overlap window late regions are never backfilled and slots stay at 5-6/12.
- First run (empty table): falls back to DEFAULT_LOOKBACK_DAYS.
- Upsert via BQ MERGE on (date_heure, region) to avoid duplicates from re-fetch.
- Weather is stored at hourly granularity; the features job joins by
  TIMESTAMP_TRUNC(date_heure, HOUR).
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from google.cloud import bigquery

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client, merge_to_bq
from elec_jobs.shared.models import Eco2mixApiPage, OpenMeteoResponse

LOG = logging.getLogger(__name__)
UTC = timezone.utc
DEFAULT_LOOKBACK_DAYS = 7  # used on first run or after a gap
OVERLAP_HOURS = 6           # re-fetch last 6h so late-publishing regions fill in


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

    records = []
    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        page = Eco2mixApiPage.model_validate(resp.json())
        records.extend(page.results)
        LOG.debug("eco2mix: fetched %d records (offset=%d)", len(page.results), params["offset"])
        if len(page.results) < 100:
            break
        params["offset"] += 100

    if not records:
        return pd.DataFrame()

    now = datetime.now(UTC)
    return pd.DataFrame([
        {
            "date_heure":    pd.Timestamp(r.date_heure).tz_convert("UTC"),
            "region":        r.libelle_region,
            "consommation":  r.consommation,
            "_ingested_at":  now,
        }
        for r in records
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
        h = OpenMeteoResponse.model_validate(resp.json()).hourly

        df = pd.DataFrame({
            "date_heure":           pd.to_datetime(h.time, utc=True),
            "region":               region,
            "temperature_celsius":  h.temperature_2m,
            "wind_speed_kmh":       h.wind_speed_10m,
            "solar_radiation_wm2":  h.direct_radiation,
            "_ingested_at":         now,
        })
        df = df[df["date_heure"] > since]
        frames.append(df)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
    since_eco2mix = (
        max_dt - timedelta(hours=OVERLAP_HOURS)
        if max_dt else
        now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )
    LOG.info("eco2mix: fetching since %s (overlap=%dh)", since_eco2mix.isoformat(), OVERLAP_HOURS)

    df_eco2mix = fetch_eco2mix(since_eco2mix)
    if df_eco2mix.empty:
        LOG.info("eco2mix: no new records")
    else:
        LOG.info("eco2mix: upserting %d rows", len(df_eco2mix))
        merge_to_bq(client, df_eco2mix, config.GCP_PROJECT_ID, eco2mix_table)
        LOG.info("eco2mix: done")

    # ── weather ──────────────────────────────────────────────────────────────
    weather_table = f"{config.BQ_DATASET_RAW}.weather"
    max_dt_w = _bq_max_date_heure(client, weather_table)
    since_weather = (
        max_dt_w - timedelta(hours=OVERLAP_HOURS)
        if max_dt_w else
        now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )
    LOG.info("weather: fetching since %s (overlap=%dh)", since_weather.isoformat(), OVERLAP_HOURS)

    df_weather = fetch_weather(since_weather)
    if df_weather.empty:
        LOG.info("weather: no new records")
    else:
        LOG.info("weather: upserting %d rows", len(df_weather))
        merge_to_bq(client, df_weather, config.GCP_PROJECT_ID, weather_table)
        LOG.info("weather: done")


if __name__ == "__main__":
    main()

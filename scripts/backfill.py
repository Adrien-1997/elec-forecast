"""Backfill script: historical eco2mix + weather → BQ raw tables.

One-shot utility to populate BQ with historical data before initial training.
Not deployed as a Cloud Run Job — run locally with the venv activated.

Strategy:
- eco2mix: paginate the chosen ODRÉ dataset in monthly per-region batches
  (avoids the API's 10 000-row offset limit) → UPSERT elec_raw.eco2mix.
- weather: Open-Meteo archive API (start_date/end_date) per region, batched yearly
  → UPSERT elec_raw.weather.
- Both are idempotent (MERGE on primary key) — safe to re-run or overlap with ingest.

Dataset choice:
  eco2mix-regional-cons-def  consolidated/definitive — covers up to ~end of previous year
  eco2mix-regional-tr        real-time — covers recent months (2025+)

Usage:
  # From repo root, with venv activated:
  python scripts/backfill.py

Env vars:
  BACKFILL_START_DATE  YYYY-MM-DD  default: 2 years ago
  BACKFILL_END_DATE    YYYY-MM-DD  default: today
  BACKFILL_DATASET     dataset ID  default: eco2mix-regional-cons-def
                                   use eco2mix-regional-tr for 2025+ data
"""

import logging
import os
from datetime import date, datetime, timezone

import pandas as pd
import requests
from google.cloud import bigquery

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client, merge_to_bq

LOG = logging.getLogger(__name__)
UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# eco2mix (historical/definitive dataset)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_eco2mix_region_month(region_label: str, year: int, month: int, now: datetime, dataset: str) -> list[dict]:
    """Paginate one ODRÉ eco2mix dataset for one region and one calendar month.

    Queries per region to stay under the ODRÉ API offset hard limit (10 000).
    One region × one month ≈ 2 880 rows — well within the limit.
    """
    url = f"{config.ODRE_BASE_URL}/catalog/datasets/{dataset}/records"

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    start_str = f"{year:04d}-{month:02d}-01T00:00:00Z"
    end_str   = f"{next_year:04d}-{next_month:02d}-01T00:00:00Z"

    # Use double-quoted string literals (ODS API v2.1 / ODSQL supports both quote styles).
    # This avoids escaping issues with region names containing single quotes
    # (e.g. "Provence-Alpes-Côte d'Azur").
    params = {
        "where":    (
            f'libelle_region = "{region_label}"'
            f" AND date_heure >= '{start_str}'"
            f" AND date_heure < '{end_str}'"
            f" AND consommation is not null"
        ),
        "order_by": "date_heure asc",
        "limit":    100,
        "offset":   0,
    }

    rows = []
    while True:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        batch = resp.json().get("results", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        params["offset"] += 100

    return [
        {
            "date_heure":   pd.Timestamp(r["date_heure"]).tz_convert("UTC"),
            "region":       r["libelle_region"],
            "consommation": float(r["consommation"]),
            "_ingested_at": now,
        }
        for r in rows
        if r.get("consommation") is not None
    ]


def _fetch_eco2mix_month(year: int, month: int, dataset: str) -> pd.DataFrame:
    """Fetch eco2mix for all 12 regions for one calendar month."""
    now  = datetime.now(UTC)
    rows = []
    for _code, (region_label, _lat, _lon) in config.REGION_CENTROIDS.items():
        rows.extend(_fetch_eco2mix_region_month(region_label, year, month, now, dataset))
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# Weather (Open-Meteo archive API)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_weather_period(start: date, end: date) -> pd.DataFrame:
    """Fetch hourly weather from Open-Meteo archive for all 12 regions.

    Uses archive-api.open-meteo.com/v1/archive (explicit start/end dates),
    which covers any historical period — unlike /v1/forecast that caps at 92 days.
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


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    today  = date.today()

    default_start = date(today.year - 2, today.month, today.day)
    start   = date.fromisoformat(os.getenv("BACKFILL_START_DATE", default_start.isoformat()))
    end     = date.fromisoformat(os.getenv("BACKFILL_END_DATE",   today.isoformat()))
    dataset = os.getenv("BACKFILL_DATASET", config.ODRE_HISTORICAL_DATASET)
    LOG.info("backfill: %s → %s  dataset=%s", start, end, dataset)

    eco2mix_table = f"{config.BQ_DATASET_RAW}.eco2mix"
    weather_table = f"{config.BQ_DATASET_RAW}.weather"

    # ── eco2mix: monthly batches ──────────────────────────────────────────────
    LOG.info("backfill: starting eco2mix from %s", dataset)
    cur_year, cur_month = start.year, start.month
    end_year, end_month = end.year, end.month

    while (cur_year, cur_month) <= (end_year, end_month):
        LOG.info("eco2mix: %04d-%02d …", cur_year, cur_month)
        df = _fetch_eco2mix_month(cur_year, cur_month, dataset)
        if not df.empty:
            merge_to_bq(client, df, config.GCP_PROJECT_ID, eco2mix_table)
            LOG.info("eco2mix: upserted %d rows", len(df))
        else:
            LOG.info("eco2mix: no records")
        if cur_month == 12:
            cur_year, cur_month = cur_year + 1, 1
        else:
            cur_month += 1

    # ── weather: yearly batches ───────────────────────────────────────────────
    LOG.info("backfill: starting weather from Open-Meteo archive")
    for yr in range(start.year, end.year + 1):
        batch_start = max(start, date(yr, 1, 1))
        batch_end   = min(end,   date(yr, 12, 31))
        LOG.info("weather: %s → %s …", batch_start, batch_end)
        df = _fetch_weather_period(batch_start, batch_end)
        if not df.empty:
            merge_to_bq(client, df, config.GCP_PROJECT_ID, weather_table)
            LOG.info("weather: upserted %d rows", len(df))
        else:
            LOG.info("weather: no records")

    LOG.info("backfill: done — run features job next, then train")


if __name__ == "__main__":
    main()

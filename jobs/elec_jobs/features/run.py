"""Features job: materialise feature store from BQ raw tables → elec_features.features.

Schedule: every 15 min via Cloud Scheduler.

Strategy:
- Query max(date_heure) from elec_features.features → compute only new rows.
- First run (empty table): falls back to LOOKBACK_DAYS so lags and rolling are populated.
- Lags and rolling avg computed via BigQuery SQL (single round-trip, handles gaps correctly).
- Eco2mix deduped by MAX(_ingested_at) to handle retry duplicates.
- Weather joined at hourly granularity via TIMESTAMP_TRUNC(date_heure, HOUR).
- Calendar features (hour, dow, month) computed in SQL at Europe/Paris timezone.
- is_weekend + is_public_holiday_fr added in Python (holidays library).
"""

import logging
from datetime import datetime, timedelta, timezone

import holidays
import pandas as pd
from google.cloud import bigquery

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client, load_dataframe

LOG = logging.getLogger(__name__)
UTC = timezone.utc
LOOKBACK_DAYS = 7  # covers max lag (168 h) and rolling window (7 d)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bq_max_date_heure(client: bigquery.Client, table: str) -> datetime | None:
    sql = f"SELECT MAX(date_heure) AS max_dt FROM `{config.GCP_PROJECT_ID}.{table}`"
    rows = list(client.query(sql).result())
    val = rows[0]["max_dt"]
    if val is None:
        return None
    return val if val.tzinfo else val.replace(tzinfo=UTC)


def _build_features_sql(project: str, since: datetime) -> str:
    """Return a BigQuery SQL query that computes all numeric/calendar features.

    Returns rows with date_heure > since, using data from (since - 7d) as history
    so that lag_168h and rolling_7d are fully populated on the first batch.
    """
    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    lookback_str = (since - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""
WITH
eco AS (
    -- Deduplicate: keep latest ingested value for each (region, date_heure).
    SELECT date_heure, region, consommation
    FROM (
        SELECT
            date_heure, region, consommation,
            ROW_NUMBER() OVER (
                PARTITION BY region, date_heure
                ORDER BY _ingested_at DESC
            ) AS rn
        FROM `{project}.elec_raw.eco2mix`
        WHERE date_heure >= TIMESTAMP('{lookback_str}')
    )
    WHERE rn = 1
),
weather_h AS (
    -- Aggregate to hourly so we can join to 15-min eco2mix via TIMESTAMP_TRUNC.
    SELECT
        TIMESTAMP_TRUNC(date_heure, HOUR) AS hour_dt,
        region,
        AVG(temperature_celsius)          AS temperature_celsius,
        AVG(wind_speed_kmh)               AS wind_speed_kmh,
        AVG(solar_radiation_wm2)          AS solar_radiation_wm2
    FROM `{project}.elec_raw.weather`
    WHERE date_heure >= TIMESTAMP('{lookback_str}')
    GROUP BY 1, 2
),
windowed AS (
    SELECT
        e.date_heure,
        e.region,

        -- Point-in-time lags via self-join (handles gaps in raw data correctly).
        lag24.consommation                                                            AS consommation_lag_24h,
        lag168.consommation                                                           AS consommation_lag_168h,

        -- 7-day rolling average.  BQ RANGE windows require a numeric ORDER BY key,
        -- so we order by UNIX_SECONDS and use 604800 (= 7 * 86400 s) as the boundary.
        -- Window spans the full `eco` CTE (history rows included) before the outer
        -- WHERE filters to date_heure > since, so the first output batch is fully populated.
        AVG(e.consommation) OVER (
            PARTITION BY e.region
            ORDER BY UNIX_SECONDS(e.date_heure)
            RANGE BETWEEN 604800 PRECEDING AND CURRENT ROW
        )                                                                             AS consommation_rolling_168h,

        -- Weather (may be NULL if weather ingest is behind eco2mix).
        w.temperature_celsius,
        w.wind_speed_kmh,
        w.solar_radiation_wm2,

        -- Calendar features in Paris local time.
        EXTRACT(HOUR     FROM e.date_heure AT TIME ZONE 'Europe/Paris')               AS hour_of_day,
        -- DAYOFWEEK: 1=Sun … 7=Sat → shift to 0=Mon … 6=Sun
        MOD(EXTRACT(DAYOFWEEK FROM e.date_heure AT TIME ZONE 'Europe/Paris') + 5, 7) AS day_of_week,
        EXTRACT(MONTH    FROM e.date_heure AT TIME ZONE 'Europe/Paris')               AS month

    FROM eco AS e
    LEFT JOIN eco AS lag24
        ON  lag24.region     = e.region
        AND lag24.date_heure = TIMESTAMP_SUB(e.date_heure, INTERVAL 24 HOUR)
    LEFT JOIN eco AS lag168
        ON  lag168.region     = e.region
        AND lag168.date_heure = TIMESTAMP_SUB(e.date_heure, INTERVAL 168 HOUR)
    LEFT JOIN weather_h AS w
        ON  w.region   = e.region
        AND w.hour_dt  = TIMESTAMP_TRUNC(e.date_heure, HOUR)
)
SELECT *
FROM windowed
WHERE date_heure > TIMESTAMP('{since_str}')
ORDER BY date_heure, region
"""


def _add_holiday_flag(date_heure: pd.Series) -> pd.Series:
    years = date_heure.dt.year.unique().tolist()
    fr_hols = holidays.France(years=years)
    dates_paris = date_heure.dt.tz_convert("Europe/Paris").dt.date
    return dates_paris.map(lambda d: d in fr_hols)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    now = datetime.now(UTC)

    features_table = f"{config.BQ_DATASET_FEATURES}.features"
    max_dt = _bq_max_date_heure(client, features_table)
    since = max_dt if max_dt else now - timedelta(days=LOOKBACK_DAYS)
    LOG.info("features: computing since %s", since.isoformat())

    sql = _build_features_sql(config.GCP_PROJECT_ID, since)
    df = client.query(sql).to_dataframe()

    if df.empty:
        LOG.info("features: no new records")
        return

    LOG.info("features: %d rows computed", len(df))

    # Python-side columns
    df["is_weekend"] = (df["day_of_week"] >= 5)
    df["is_public_holiday_fr"] = _add_holiday_flag(df["date_heure"])
    df["_materialized_at"] = now

    load_dataframe(df, f"{config.GCP_PROJECT_ID}.{features_table}")
    LOG.info("features: done")


if __name__ == "__main__":
    main()

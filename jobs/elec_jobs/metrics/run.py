"""Metrics job: compute rolling-7d model performance → elec_ml.metrics.

Schedule: daily at 08:00 Europe/Paris (06:00 UTC) — after ingest has had time to
publish actuals for the previous day's forecasts.

Strategy:
- Join elec_ml.predictions × elec_raw.eco2mix for all horizons that:
    (a) are within the last 7 days (rolling evaluation window), and
    (b) are at least 2h in the past (allows ODRÉ publication lag).
- Compute per-region metrics: MAE, p95_error, p99_error, n_samples.
- Compute France aggregate: sum predictions and actuals by slot first
  (only on slots where all 12 regions have actuals), then compute errors.
- UPSERT rows into elec_ml.metrics on (computed_date, region) so re-running
  is idempotent and updates the day's snapshot.
"""

import logging
from datetime import date, datetime, timezone

import pandas as pd
from google.cloud import bigquery

from elec_jobs.shared import config
from elec_jobs.shared.bq import get_client, merge_to_bq

LOG = logging.getLogger(__name__)
UTC = timezone.utc

N_REGIONS = 12    # expected regions per slot for France aggregate
EVAL_DAYS = 7     # rolling evaluation window
ODRE_LAG_H = 2   # minimum hours before an actual is considered reliable


# ─────────────────────────────────────────────────────────────────────────────
# BQ query
# ─────────────────────────────────────────────────────────────────────────────

def _load_matched(client: bigquery.Client) -> pd.DataFrame:
    """Return prediction/actual pairs for completed horizons within the last 7 days."""
    sql = f"""
    SELECT
        p.forecast_horizon_dt,
        p.region,
        p.predicted_mw,
        e.consommation
    FROM `{config.GCP_PROJECT_ID}.{config.BQ_DATASET_ML}.predictions` AS p
    JOIN `{config.GCP_PROJECT_ID}.{config.BQ_DATASET_RAW}.eco2mix`    AS e
        ON  e.region     = p.region
        AND e.date_heure = p.forecast_horizon_dt
        AND e.consommation IS NOT NULL
    WHERE
        p.forecast_horizon_dt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {EVAL_DAYS} DAY)
        AND p.forecast_horizon_dt <= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {ODRE_LAG_H} HOUR)
    """
    return client.query(sql).to_dataframe()


# ─────────────────────────────────────────────────────────────────────────────
# Metric computation
# ─────────────────────────────────────────────────────────────────────────────

def _region_metrics(matched: pd.DataFrame) -> pd.DataFrame:
    """Per-region MAE, p95_error, p99_error, n_samples."""
    matched = matched.copy()
    matched["abs_error"] = (matched["predicted_mw"] - matched["consommation"]).abs()

    rows = []
    for region, grp in matched.groupby("region"):
        rows.append({
            "region":        region,
            "mae_mw":        grp["abs_error"].mean(),
            "p95_error_mw":  grp["abs_error"].quantile(0.95),
            "p99_error_mw":  grp["abs_error"].quantile(0.99),
            "n_samples":     len(grp),
        })
    return pd.DataFrame(rows)


def _france_metrics(matched: pd.DataFrame) -> pd.DataFrame:
    """France aggregate: sum by slot (complete slots only), then compute errors."""
    # Only slots where all 12 regions have actuals
    slot_counts = matched.groupby("forecast_horizon_dt")["region"].count()
    complete_slots = slot_counts[slot_counts == N_REGIONS].index

    if len(complete_slots) == 0:
        LOG.warning("metrics: no complete slots (12/12 regions) for France aggregate")
        return pd.DataFrame()

    complete = matched[matched["forecast_horizon_dt"].isin(complete_slots)]
    france = (
        complete.groupby("forecast_horizon_dt")
        .agg(predicted_mw=("predicted_mw", "sum"), consommation=("consommation", "sum"))
        .reset_index()
    )
    france["abs_error"] = (france["predicted_mw"] - france["consommation"]).abs()

    return pd.DataFrame([{
        "region":        "France",
        "mae_mw":        france["abs_error"].mean(),
        "p95_error_mw":  france["abs_error"].quantile(0.95),
        "p99_error_mw":  france["abs_error"].quantile(0.99),
        "n_samples":     len(france),
    }])


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    now    = datetime.now(UTC)
    today  = date.today()

    matched = _load_matched(client)
    if matched.empty:
        LOG.info("metrics: no matched prediction/actual pairs — skipping")
        return
    LOG.info("metrics: %d matched pairs across %d regions", len(matched), matched["region"].nunique())

    per_region = _region_metrics(matched)
    france     = _france_metrics(matched)

    df = pd.concat([per_region, france], ignore_index=True)
    df["computed_date"] = today
    df["_computed_at"]  = now

    LOG.info("metrics: upserting %d rows (regions + France)", len(df))
    merge_to_bq(
        client, df, config.GCP_PROJECT_ID,
        f"{config.BQ_DATASET_ML}.metrics",
        key_cols=("computed_date", "region"),
    )
    LOG.info("metrics: done")

    for _, row in df.iterrows():
        LOG.info(
            "  %-30s  MAE=%6.0f MW  p95=%6.0f MW  p99=%6.0f MW  n=%d",
            row["region"], row["mae_mw"], row["p95_error_mw"], row["p99_error_mw"], row["n_samples"],
        )


if __name__ == "__main__":
    main()

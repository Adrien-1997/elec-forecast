-- BQ DDL: elec_ml dataset

CREATE TABLE IF NOT EXISTS `elec_ml.predictions` (
    forecast_horizon_dt TIMESTAMP   NOT NULL,   -- the datetime being forecast
    region              STRING      NOT NULL,   -- one row per (slot, region)
    predicted_mw        FLOAT64     NOT NULL,
    model_version       STRING,                 -- MLflow run ID
    forecast_date       DATE        NOT NULL,   -- date the daily forecast was generated
    forecasted_at       TIMESTAMP   NOT NULL    -- exact timestamp of the forecast run
)
PARTITION BY DATE(forecast_horizon_dt)
CLUSTER BY region
OPTIONS (
    description = "24h-ahead demand forecasts produced by the daily forecast job. One row per (forecast_horizon_dt, region) — idempotent UPSERT."
);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `elec_ml.metrics` (
    computed_date   DATE        NOT NULL,   -- date these metrics were computed
    region          STRING      NOT NULL,   -- region name OR 'France' for national aggregate
    mae_mw          FLOAT64,                -- mean absolute error over rolling 7-day window
    p95_error_mw    FLOAT64,                -- 95th percentile of absolute error
    p99_error_mw    FLOAT64,                -- 99th percentile of absolute error
    n_samples       INT64,                  -- number of matched prediction/actual pairs
    _computed_at    TIMESTAMP   NOT NULL
)
PARTITION BY computed_date
CLUSTER BY region
OPTIONS (
    description = "Rolling 7-day model performance metrics per region + France total. One row per (computed_date, region) — idempotent UPSERT."
);

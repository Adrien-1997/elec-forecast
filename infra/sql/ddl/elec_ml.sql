-- BQ DDL: elec_ml dataset

CREATE TABLE IF NOT EXISTS `elec_ml.predictions` (
    forecast_horizon_dt TIMESTAMP   NOT NULL,   -- the datetime being forecast
    region              STRING      NOT NULL,
    predicted_mw        FLOAT64     NOT NULL,
    model_version       STRING,                 -- MLflow run ID
    scored_at           TIMESTAMP   NOT NULL
)
PARTITION BY DATE(forecast_horizon_dt)
CLUSTER BY region
OPTIONS (
    description = "24h-ahead demand forecasts produced by the score job."
);

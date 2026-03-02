-- BQ DDL: elec_features dataset

CREATE TABLE IF NOT EXISTS `elec_features.features` (
    date_heure                  TIMESTAMP   NOT NULL,
    region                      STRING      NOT NULL,
    consommation_lag_24h        FLOAT64,
    consommation_lag_168h       FLOAT64,
    consommation_rolling_7d     FLOAT64,
    temperature_celsius         FLOAT64,
    wind_speed_kmh              FLOAT64,
    solar_radiation_wm2         FLOAT64,
    hour_of_day                 INT64,
    day_of_week                 INT64,      -- 0=Mon … 6=Sun
    is_weekend                  BOOL,
    is_public_holiday_fr        BOOL,
    month                       INT64,
    _materialized_at            TIMESTAMP   NOT NULL
)
PARTITION BY DATE(date_heure)
CLUSTER BY region
OPTIONS (
    description = "Feature store — 30-min grain per region, used for training and scoring."
);

-- BQ DDL: elec_raw dataset
-- Run via: bq query --use_legacy_sql=false < infra/sql/ddl/elec_raw.sql

CREATE TABLE IF NOT EXISTS `elec_raw.eco2mix` (
    date_heure          TIMESTAMP   NOT NULL,
    region              STRING      NOT NULL,
    consommation        FLOAT64,             -- MW
    _ingested_at        TIMESTAMP   NOT NULL
)
PARTITION BY DATE(date_heure)
CLUSTER BY region
OPTIONS (
    description = "Raw eco2mix consumption records from ODRE API.",
    require_partition_filter = false
);

CREATE TABLE IF NOT EXISTS `elec_raw.weather` (
    date_heure          TIMESTAMP   NOT NULL,
    region              STRING      NOT NULL,
    temperature_celsius FLOAT64,
    wind_speed_kmh      FLOAT64,
    solar_radiation_wm2 FLOAT64,
    _ingested_at        TIMESTAMP   NOT NULL
)
PARTITION BY DATE(date_heure)
CLUSTER BY region
OPTIONS (
    description = "Raw weather observations from Open-Meteo per region centroid."
);

# Data Contracts — Table Schemas

## `elec_raw.eco2mix`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | 15-min slot (UTC) |
| `region` | STRING | French metro region name |
| `consommation` | FLOAT64 | Consumption in MW |
| `_ingested_at` | TIMESTAMP | Row insertion time |

**Source**: ODRÉ API (`eco2mix-regional-tr` / `eco2mix-regional-cons-def`)
**Partition**: DATE(date_heure) · **Cluster**: region
**UPSERT key**: (date_heure, region)

---

## `elec_raw.weather`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | Hourly slot (UTC) |
| `region` | STRING | French metro region name |
| `temperature_celsius` | FLOAT64 | °C |
| `wind_speed_kmh` | FLOAT64 | km/h |
| `solar_radiation_wm2` | FLOAT64 | W/m² |
| `_ingested_at` | TIMESTAMP | Row insertion time |

**Source**: Open-Meteo API (region centroids) — historical weather only; forecast weather fetched at run time by the forecast job.

---

## `elec_features.features`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | 15-min slot (UTC) |
| `region` | STRING | French metro region name |
| `consommation_lag_24h` | FLOAT64 | Same slot 24h ago |
| `consommation_lag_168h` | FLOAT64 | Same slot 7 days ago |
| `consommation_rolling_168h` | FLOAT64 | 7-day rolling mean |
| `temperature_celsius` | FLOAT64 | °C |
| `wind_speed_kmh` | FLOAT64 | km/h |
| `solar_radiation_wm2` | FLOAT64 | W/m² |
| `hour_of_day` | INT64 | 0–23 |
| `day_of_week` | INT64 | 0=Mon … 6=Sun |
| `is_weekend` | BOOL | Sat or Sun |
| `is_public_holiday_fr` | BOOL | French public holiday |
| `month` | INT64 | 1–12 |
| `_materialized_at` | TIMESTAMP | Row insertion time |

---

## `elec_ml.predictions`
| Column | Type | Description |
|---|---|---|
| `forecast_horizon_dt` | TIMESTAMP | Datetime being forecast |
| `region` | STRING | French metro region name |
| `predicted_mw` | FLOAT64 | Predicted consumption in MW |
| `model_version` | STRING | MLflow run ID |
| `forecast_date` | DATE | Calendar date the forecast was generated |
| `forecasted_at` | TIMESTAMP | Exact timestamp of the forecast run |

**Written by**: `forecast` job (daily 06:00 Paris)
**Partition**: DATE(forecast_horizon_dt) · **Cluster**: region
**UPSERT key**: (forecast_horizon_dt, region)

---

## `elec_ml.metrics`
| Column | Type | Description |
|---|---|---|
| `computed_date` | DATE | Date these metrics were computed |
| `region` | STRING | Region name or `'France'` for national aggregate |
| `mae_mw` | FLOAT64 | Mean absolute error over rolling 7-day window |
| `p95_error_mw` | FLOAT64 | 95th percentile of absolute error |
| `p99_error_mw` | FLOAT64 | 99th percentile of absolute error |
| `n_samples` | INT64 | Number of matched prediction/actual pairs |
| `_computed_at` | TIMESTAMP | Exact timestamp of computation |

**Written by**: `metrics` job (every 15 min, +10 min after ingest)
**Partition**: computed_date · **Cluster**: region
**UPSERT key**: (computed_date, region) — intraday runs overwrite today's snapshot
**France row**: aggregated over complete slots only (all 12 regions present)

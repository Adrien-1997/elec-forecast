# Data Contracts — Table Schemas

## `elec_raw.eco2mix`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | 30-min slot (UTC) |
| `region` | STRING | French metro region name |
| `consommation` | FLOAT64 | Consumption in MW |
| `_ingested_at` | TIMESTAMP | Row insertion time |

**Source**: ODRÉ API (`eco2mix-regional-tr` / `eco2mix-regional-cons-def`)
**Partition**: DATE(date_heure) · **Cluster**: region

---

## `elec_raw.weather`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | 30-min slot (UTC) |
| `region` | STRING | French metro region name |
| `temperature_celsius` | FLOAT64 | °C |
| `wind_speed_kmh` | FLOAT64 | km/h |
| `solar_radiation_wm2` | FLOAT64 | W/m² |
| `_ingested_at` | TIMESTAMP | Row insertion time |

**Source**: Open-Meteo API (region centroids)

---

## `elec_features.features`
| Column | Type | Description |
|---|---|---|
| `date_heure` | TIMESTAMP | 30-min slot (UTC) |
| `region` | STRING | French metro region name |
| `consommation_lag_24h` | FLOAT64 | Same slot 24h ago |
| `consommation_lag_168h` | FLOAT64 | Same slot 7 days ago |
| `consommation_rolling_7d` | FLOAT64 | 7-day rolling mean |
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
| `scored_at` | TIMESTAMP | When forecast was produced |

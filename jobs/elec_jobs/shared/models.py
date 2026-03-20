"""Pydantic models for API response parsing and output row validation."""

import math
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from elec_jobs.shared.config import REGION_CATEGORIES

# ── ODRÉ eco2mix API ──────────────────────────────────────────────────────────

class Eco2mixRecord(BaseModel):
    """One record from the ODRÉ eco2mix API results list."""
    date_heure: str
    libelle_region: str
    consommation: float | None

    @field_validator("date_heure")
    @classmethod
    def must_be_tz_aware(cls, v: str) -> str:
        import pandas as pd
        ts = pd.Timestamp(v)
        if ts.tz is None:
            raise ValueError(f"date_heure must be tz-aware, got: {v!r}")
        return v


class Eco2mixApiPage(BaseModel):
    """One page of results from the ODRÉ eco2mix API."""
    results: list[Eco2mixRecord]


# ── Open-Meteo hourly arrays ──────────────────────────────────────────────────

class OpenMeteoHourly(BaseModel):
    """Hourly arrays from an Open-Meteo API response (forecast or archive)."""
    time: list[str]
    temperature_2m: list[float | None]
    wind_speed_10m: list[float | None]
    direct_radiation: list[float | None]

    @model_validator(mode="after")
    def arrays_same_length(self) -> "OpenMeteoHourly":
        n = len(self.time)
        for field in ("temperature_2m", "wind_speed_10m", "direct_radiation"):
            arr = getattr(self, field)
            if len(arr) != n:
                raise ValueError(f"{field}: expected {n} values, got {len(arr)}")
        return self


class OpenMeteoResponse(BaseModel):
    """Top-level Open-Meteo API response."""
    hourly: OpenMeteoHourly


# ── Forecast output rows ──────────────────────────────────────────────────────

class ForecastRecord(BaseModel):
    """One prediction row written to elec_ml.predictions."""
    forecast_horizon_dt: datetime
    region: str
    predicted_mw: float

    @field_validator("region")
    @classmethod
    def must_be_known_region(cls, v: str) -> str:
        if v not in REGION_CATEGORIES:
            raise ValueError(f"Unknown region: {v!r}")
        return v

    @field_validator("predicted_mw")
    @classmethod
    def must_be_positive_and_finite(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"predicted_mw must be positive and finite, got {v}")
        return v


# ── Metrics output rows ───────────────────────────────────────────────────────

class MetricsRecord(BaseModel):
    """One metrics row written to elec_ml.metrics."""
    region: str
    mae_mw: float
    p95_error_mw: float
    p99_error_mw: float
    n_samples: int

    @field_validator("mae_mw", "p95_error_mw", "p99_error_mw")
    @classmethod
    def must_be_finite(cls, v: float, info) -> float:
        if not math.isfinite(v):
            raise ValueError(f"{info.field_name} must be finite, got {v}")
        return v

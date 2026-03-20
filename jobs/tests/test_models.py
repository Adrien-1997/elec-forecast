"""Tests for shared/models.py Pydantic validation."""

import pytest
from pydantic import ValidationError

from elec_jobs.shared.models import (
    Eco2mixApiPage,
    Eco2mixRecord,
    ForecastRecord,
    MetricsRecord,
    OpenMeteoHourly,
    OpenMeteoResponse,
)

# ── Eco2mixRecord ─────────────────────────────────────────────────────────────

class TestEco2mixRecord:
    def test_valid(self):
        r = Eco2mixRecord(
            date_heure="2024-06-01T10:00:00+00:00",
            libelle_region="Île-de-France",
            consommation=5000.0,
        )
        assert r.libelle_region == "Île-de-France"
        assert r.consommation == 5000.0

    def test_consommation_none_allowed(self):
        r = Eco2mixRecord(
            date_heure="2024-06-01T10:00:00+00:00",
            libelle_region="Normandie",
            consommation=None,
        )
        assert r.consommation is None

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError, match="tz-aware"):
            Eco2mixRecord(
                date_heure="2024-06-01T10:00:00",
                libelle_region="Normandie",
                consommation=1000.0,
            )


class TestEco2mixApiPage:
    def test_valid_page(self):
        data = {
            "results": [
                {"date_heure": "2024-06-01T10:00:00+00:00", "libelle_region": "Normandie", "consommation": 1500.0},
                {"date_heure": "2024-06-01T10:15:00+00:00", "libelle_region": "Bretagne", "consommation": None},
            ]
        }
        page = Eco2mixApiPage.model_validate(data)
        assert len(page.results) == 2

    def test_missing_results_key_raises(self):
        with pytest.raises(ValidationError):
            Eco2mixApiPage.model_validate({"total_count": 0})

    def test_invalid_record_in_page_raises(self):
        with pytest.raises(ValidationError):
            Eco2mixApiPage.model_validate({
                "results": [{"date_heure": "2024-06-01T10:00:00", "libelle_region": "X", "consommation": 1.0}]
            })


# ── OpenMeteoHourly ───────────────────────────────────────────────────────────

class TestOpenMeteoHourly:
    def test_valid(self):
        h = OpenMeteoHourly(
            time=["2024-06-01T00:00", "2024-06-01T01:00"],
            temperature_2m=[15.0, 14.5],
            wind_speed_10m=[10.0, 12.0],
            direct_radiation=[0.0, 0.0],
        )
        assert len(h.time) == 2

    def test_length_mismatch_raises(self):
        with pytest.raises(ValidationError, match="temperature_2m"):
            OpenMeteoHourly(
                time=["2024-06-01T00:00", "2024-06-01T01:00"],
                temperature_2m=[15.0],  # wrong length
                wind_speed_10m=[10.0, 12.0],
                direct_radiation=[0.0, 0.0],
            )

    def test_none_values_allowed(self):
        h = OpenMeteoHourly(
            time=["2024-06-01T00:00"],
            temperature_2m=[None],
            wind_speed_10m=[None],
            direct_radiation=[None],
        )
        assert h.temperature_2m[0] is None

    def test_full_response_parsed(self):
        data = {
            "hourly": {
                "time": ["2024-06-01T00:00"],
                "temperature_2m": [12.0],
                "wind_speed_10m": [5.0],
                "direct_radiation": [0.0],
            }
        }
        resp = OpenMeteoResponse.model_validate(data)
        assert resp.hourly.time == ["2024-06-01T00:00"]

    def test_missing_hourly_key_raises(self):
        with pytest.raises(ValidationError):
            OpenMeteoResponse.model_validate({"latitude": 48.8})


# ── ForecastRecord ────────────────────────────────────────────────────────────

class TestForecastRecord:
    def _valid(self, **overrides):
        kwargs = dict(
            forecast_horizon_dt="2024-06-01T10:00:00+00:00",
            region="Île-de-France",
            predicted_mw=5000.0,
        )
        kwargs.update(overrides)
        return ForecastRecord(**kwargs)

    def test_valid(self):
        r = self._valid()
        assert r.predicted_mw == 5000.0

    def test_unknown_region_raises(self):
        with pytest.raises(ValidationError, match="Unknown region"):
            self._valid(region="Atlantide")

    def test_zero_mw_raises(self):
        with pytest.raises(ValidationError, match="positive and finite"):
            self._valid(predicted_mw=0.0)

    def test_negative_mw_raises(self):
        with pytest.raises(ValidationError, match="positive and finite"):
            self._valid(predicted_mw=-100.0)

    def test_nan_mw_raises(self):
        with pytest.raises(ValidationError, match="positive and finite"):
            self._valid(predicted_mw=float("nan"))

    def test_inf_mw_raises(self):
        with pytest.raises(ValidationError, match="positive and finite"):
            self._valid(predicted_mw=float("inf"))


# ── MetricsRecord ─────────────────────────────────────────────────────────────

class TestMetricsRecord:
    def _valid(self, **overrides):
        kwargs = dict(region="Bretagne", mae_mw=120.5, p95_error_mw=280.0, p99_error_mw=350.0, n_samples=500)
        kwargs.update(overrides)
        return MetricsRecord(**kwargs)

    def test_valid(self):
        r = self._valid()
        assert r.mae_mw == 120.5

    def test_nan_mae_raises(self):
        with pytest.raises(ValidationError, match="finite"):
            self._valid(mae_mw=float("nan"))

    def test_nan_p95_raises(self):
        with pytest.raises(ValidationError, match="finite"):
            self._valid(p95_error_mw=float("nan"))

    def test_model_dump_round_trips(self):
        r = self._valid()
        d = r.model_dump()
        assert d["region"] == "Bretagne"
        assert d["n_samples"] == 500

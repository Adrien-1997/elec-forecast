"""Unit tests for elec_jobs.ingest.run — pure logic, no BQ/GCS."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from elec_jobs.ingest.run import fetch_eco2mix

UTC = timezone.utc


def _resp(records: list) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"results": records}
    return m


def _record(
    date_heure: str = "2024-06-01T10:00:00Z",
    region: str = "Île-de-France",
    consommation: float = 5000.0,
) -> dict:
    return {"date_heure": date_heure, "libelle_region": region, "consommation": consommation}


# ── fetch_eco2mix ─────────────────────────────────────────────────────────────

class TestFetchEco2mix:
    def test_empty_response_returns_empty_dataframe(self):
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.return_value = _resp([])
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert df.empty

    def test_single_page_stops_after_one_request(self):
        records = [_record() for _ in range(50)]
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.return_value = _resp(records)
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert mock_get.call_count == 1
        assert len(df) == 50

    def test_full_page_triggers_second_request(self):
        page1 = [_record() for _ in range(100)]
        page2 = [_record() for _ in range(30)]
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.side_effect = [_resp(page1), _resp(page2)]
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert mock_get.call_count == 2
        assert len(df) == 130

    def test_output_columns(self):
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.return_value = _resp([_record()])
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert set(df.columns) == {"date_heure", "region", "consommation", "_ingested_at"}

    def test_date_heure_converted_to_utc(self):
        # Input is UTC+2 → should be normalised to 08:00 UTC
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.return_value = _resp([_record(date_heure="2024-06-01T10:00:00+02:00")])
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert df["date_heure"].dt.tz is not None
        assert df["date_heure"].iloc[0] == pd.Timestamp("2024-06-01T08:00:00Z")

    def test_region_mapped_from_libelle_region(self):
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.return_value = _resp([_record(region="Bretagne")])
            df = fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        assert df["region"].iloc[0] == "Bretagne"

    def test_second_page_uses_incremented_offset(self):
        page1 = [_record() for _ in range(100)]
        page2 = [_record() for _ in range(1)]
        with patch("elec_jobs.ingest.run.requests.get") as mock_get:
            mock_get.side_effect = [_resp(page1), _resp(page2)]
            fetch_eco2mix(datetime(2024, 1, 1, tzinfo=UTC))
        _, kwargs2 = mock_get.call_args_list[1]
        assert mock_get.call_args_list[1][1]["params"]["offset"] == 100

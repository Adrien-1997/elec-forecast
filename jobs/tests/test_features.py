"""Unit tests for elec_jobs.features.run — pure logic, no BQ/GCS."""

from datetime import datetime, timezone

import pandas as pd

from elec_jobs.features.run import _add_holiday_flag, _build_features_sql

UTC = timezone.utc


# ── _add_holiday_flag ─────────────────────────────────────────────────────────

class TestAddHolidayFlag:
    def test_french_public_holiday(self):
        # 2024-11-01: Toussaint
        s = pd.Series([pd.Timestamp("2024-11-01 10:00:00", tz="UTC")])
        assert _add_holiday_flag(s).iloc[0]

    def test_bastille_day(self):
        s = pd.Series([pd.Timestamp("2024-07-14 08:00:00", tz="UTC")])
        assert _add_holiday_flag(s).iloc[0]

    def test_regular_weekday(self):
        # 2024-11-04: regular Monday
        s = pd.Series([pd.Timestamp("2024-11-04 10:00:00", tz="UTC")])
        assert not _add_holiday_flag(s).iloc[0]

    def test_mixed_series(self):
        dates = pd.Series([
            pd.Timestamp("2024-07-14 12:00:00", tz="UTC"),  # Bastille Day → holiday
            pd.Timestamp("2024-07-15 12:00:00", tz="UTC"),  # regular Monday
        ])
        result = _add_holiday_flag(dates)
        assert result.iloc[0]
        assert not result.iloc[1]

    def test_new_years_day(self):
        s = pd.Series([pd.Timestamp("2024-01-01 00:00:00", tz="UTC")])
        assert _add_holiday_flag(s).iloc[0]


# ── _build_features_sql ───────────────────────────────────────────────────────

class TestBuildFeaturesSql:
    def test_since_timestamp_in_sql(self):
        since = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        sql = _build_features_sql("my-project", since)
        assert "2024-06-15T12:00:00" in sql

    def test_lookback_is_7_days_before_since(self):
        since = datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)
        sql = _build_features_sql("my-project", since)
        assert "2024-06-08T00:00:00" in sql  # since - 7 days

    def test_project_id_in_table_refs(self):
        sql = _build_features_sql("elec-forecast", datetime(2024, 1, 1, tzinfo=UTC))
        assert "`elec-forecast.elec_raw.eco2mix`" in sql
        assert "`elec-forecast.elec_raw.weather`" in sql

    def test_rolling_window_excludes_current_row(self):
        # 900 PRECEDING = 15 min: prevents target leakage into its own feature
        sql = _build_features_sql("p", datetime(2024, 1, 1, tzinfo=UTC))
        assert "900 PRECEDING" in sql

    def test_where_filters_to_since(self):
        since = datetime(2024, 3, 10, 6, 0, 0, tzinfo=UTC)
        sql = _build_features_sql("p", since)
        # The outer WHERE should use the since timestamp, not the lookback
        assert sql.count("2024-03-10T06:00:00") >= 1

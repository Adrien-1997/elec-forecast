"""Streamlit dashboard — forecasts vs actuals per region + monitoring metrics."""

import os

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from google.cloud import bigquery
from streamlit_folium import st_folium

load_dotenv()

PROJECT = os.environ["GCP_PROJECT_ID"].strip()

REGION_CENTROIDS: dict[str, tuple[float, float]] = {
    "Île-de-France":               (48.8566,  2.3522),
    "Centre-Val de Loire":         (47.7516,  1.6751),
    "Bourgogne-Franche-Comté":    (47.2805,  4.9994),
    "Normandie":                   (49.1829,  0.3707),
    "Hauts-de-France":             (50.4902,  2.7857),
    "Grand Est":                   (48.6994,  6.1867),
    "Pays de la Loire":            (47.7624, -0.3296),
    "Bretagne":                    (48.2020, -2.9326),
    "Nouvelle-Aquitaine":          (44.8378,  0.5792),
    "Occitanie":                   (43.8485,  3.2503),
    "Auvergne-Rhône-Alpes":       (45.7597,  4.8422),
    "Provence-Alpes-Côte d'Azur": (43.9352,  6.0679),
}

EXPECTED_SLOTS_24H = 96 * 12   # 15-min slots × 12 regions
ALL_REGIONS        = "All regions"
MAP_COLOR          = "#2563EB"


# ─────────────────────────────────────────────────────────────────────────────
# BQ queries
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _bq() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


@st.cache_data(ttl=300)
def load_latest_forecasts() -> pd.DataFrame:
    sql = f"""
    SELECT forecast_horizon_dt, region, predicted_mw, model_version, forecasted_at
    FROM `{PROJECT}.elec_ml.predictions`
    WHERE forecast_horizon_dt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 48 HOUR)
      AND forecast_horizon_dt <= TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    ORDER BY forecast_horizon_dt, region
    """
    return _bq().query(sql).to_dataframe()


@st.cache_data(ttl=300)
def load_actuals(hours: int = 48) -> pd.DataFrame:
    sql = f"""
    SELECT date_heure, region, consommation
    FROM `{PROJECT}.elec_raw.eco2mix`
    WHERE date_heure >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {hours} HOUR)
      AND consommation IS NOT NULL
    ORDER BY date_heure, region
    """
    return _bq().query(sql).to_dataframe()


@st.cache_data(ttl=300)
def load_system_status() -> dict:
    sql = f"""
    SELECT
        (SELECT MAX(_ingested_at)     FROM `{PROJECT}.elec_raw.eco2mix`)       AS last_ingest,
        (SELECT MAX(_materialized_at) FROM `{PROJECT}.elec_features.features`) AS last_features,
        (SELECT MAX(forecasted_at)    FROM `{PROJECT}.elec_ml.predictions`)    AS last_forecast
    """
    return _bq().query(sql).to_dataframe().iloc[0].to_dict()


@st.cache_data(ttl=300)
def load_metrics() -> pd.DataFrame:
    sql = f"""
    SELECT region, mae_mw, p95_error_mw, p99_error_mw, n_samples, computed_date
    FROM `{PROJECT}.elec_ml.metrics`
    WHERE computed_date = (SELECT MAX(computed_date) FROM `{PROJECT}.elec_ml.metrics`)
    ORDER BY region
    """
    return _bq().query(sql).to_dataframe()


@st.cache_data(ttl=300)
def load_completeness() -> int:
    sql = f"""
    SELECT COUNT(*) AS n
    FROM (
        SELECT DISTINCT date_heure, region
        FROM `{PROJECT}.elec_raw.eco2mix`
        WHERE date_heure >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
          AND consommation IS NOT NULL
    )
    """
    return int(_bq().query(sql).to_dataframe().iloc[0]["n"])


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts(ts) -> pd.Timestamp | None:
    if ts is None:
        return None
    try:
        t = pd.Timestamp(ts)
        return t.tz_localize("UTC") if t.tzinfo is None else t
    except Exception:
        return None


def _ago(ts) -> str:
    t = _ts(ts)
    if t is None:
        return "—"
    mins = int((pd.Timestamp.now(tz="UTC") - t).total_seconds() / 60)
    if mins < 1:    return "just now"
    if mins < 60:   return f"{mins} min ago"
    if mins < 1440: return f"{mins // 60} h {mins % 60} min ago"
    return f"{mins // 1440} d ago"


def _freshness_cls(ts) -> str:
    t = _ts(ts)
    if t is None:
        return "badge-dead"
    mins = (pd.Timestamp.now(tz="UTC") - t).total_seconds() / 60
    if mins < 20:  return "badge-ok"
    if mins < 60:  return "badge-warn"
    return "badge-dead"


def _freshness_cls_daily(ts) -> str:
    t = _ts(ts)
    if t is None:
        return "badge-dead"
    hours = (pd.Timestamp.now(tz="UTC") - t).total_seconds() / 3600
    if hours < 26: return "badge-ok"
    if hours < 30: return "badge-warn"
    return "badge-dead"


def _badge(label: str, ts, daily: bool = False) -> str:
    cls = _freshness_cls_daily(ts) if daily else _freshness_cls(ts)
    return f'<span class="badge {cls}">{label}</span>'


def _fmt_mw(v, suffix=" MW") -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.0f}{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Map
# ─────────────────────────────────────────────────────────────────────────────

def build_map(forecasts: pd.DataFrame) -> folium.Map:
    avg_mw = forecasts.groupby("region")["predicted_mw"].mean()
    min_mw = avg_mw.min()
    rng    = (avg_mw.max() - min_mw) or 1.0

    m = folium.Map(tiles="CartoDB positron", zoom_start=6)
    m.fit_bounds([[42.3, -5.1], [51.1, 9.6]])

    for region, (lat, lon) in REGION_CENTROIDS.items():
        if region not in avg_mw.index:
            continue
        mw = avg_mw[region]
        t  = (mw - min_mw) / rng
        folium.CircleMarker(
            location=[lat, lon],
            radius=10 + t * 20,
            color=MAP_COLOR,
            fill=True,
            fill_color=MAP_COLOR,
            fill_opacity=0.30 + t * 0.55,
            weight=1.5,
            tooltip=folium.Tooltip(
                f"<b style='font-size:13px'>{region}</b>"
                f"<br>Avg predicted: <b>{mw:,.0f} MW</b>",
                sticky=True,
            ),
        ).add_to(m)

    return m


# ─────────────────────────────────────────────────────────────────────────────
# Time-series chart
# ─────────────────────────────────────────────────────────────────────────────

def build_timeseries(forecasts: pd.DataFrame, actuals: pd.DataFrame, region: str) -> go.Figure:
    now_utc = pd.Timestamp.now(tz="UTC")

    today_paris   = now_utc.tz_convert("Europe/Paris").normalize()
    day_start_utc = today_paris.tz_convert("UTC")
    day_end_utc   = (today_paris + pd.Timedelta(days=1)).tz_convert("UTC")

    if region == ALL_REGIONS:
        pred_g = (
            forecasts.groupby("forecast_horizon_dt")["predicted_mw"]
            .sum().reset_index().sort_values("forecast_horizon_dt")
        )
        act_g = (
            actuals.groupby("date_heure")["consommation"]
            .sum().reset_index().sort_values("date_heure")
        )
        px, py  = pred_g["forecast_horizon_dt"], pred_g["predicted_mw"]
        ax, ay  = act_g["date_heure"],           act_g["consommation"]
        title   = "France total — Realized vs Predicted"
        y_label = "Total consumption (MW)"
    else:
        pred_r = forecasts[forecasts["region"] == region].sort_values("forecast_horizon_dt")
        act_r  = actuals[actuals["region"] == region].sort_values("date_heure")
        px, py  = pred_r["forecast_horizon_dt"], pred_r["predicted_mw"]
        ax, ay  = act_r["date_heure"],           act_r["consommation"]
        title   = f"{region} — Realized vs Predicted"
        y_label = "Consumption (MW)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ax, y=ay, name="Actual",
        line=dict(color="#1E3A5F", width=2), mode="lines",
    ))
    fig.add_trace(go.Scatter(
        x=px, y=py, name="Predicted",
        line=dict(color=MAP_COLOR, width=2, dash="dash"), mode="lines",
    ))
    fig.update_layout(
        shapes=[{
            "type": "line", "xref": "x", "yref": "paper",
            "x0": now_utc, "x1": now_utc, "y0": 0, "y1": 1,
            "line": {"dash": "dot", "color": "#94A3B8", "width": 1},
        }],
        annotations=[{
            "x": now_utc, "y": 1, "xref": "x", "yref": "paper",
            "text": "now", "showarrow": False,
            "font": {"color": "#94A3B8", "size": 11},
            "xanchor": "left", "yanchor": "top",
        }],
        title=dict(text=title, font=dict(size=14, color="#0F172A")),
        yaxis_title=y_label,
        xaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(
            range=[day_start_utc, day_end_utc],
            showgrid=True, gridcolor="#E2E8F0", zeroline=False,
            showline=True, linewidth=1, linecolor="#CBD5E1",
            tickfont=dict(color="#475569", size=11),
        ),
        yaxis=dict(
            showgrid=True, gridcolor="#E2E8F0", zeroline=False,
            showline=True, linewidth=1, linecolor="#CBD5E1",
            tickfont=dict(color="#475569", size=11),
            title_font=dict(color="#475569"),
        ),
        legend=dict(
            x=0.01, y=0.99,
            xanchor="left", yanchor="top",
            bgcolor="#FFFFFF",
            bordercolor="#94A3B8", borderwidth=1,
            font=dict(size=13, color="#0F172A"),
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        height=360,
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Page config + CSS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Elec Forecast", layout="wide", page_icon="⚡")

st.markdown("""
<style>
/* ── Metric cards ─────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 14px 18px;
}
[data-testid="stMetricLabel"] > div {
    font-size: 11px !important;
    color: #64748B !important;
    text-transform: uppercase;
    letter-spacing: .06em;
    font-weight: 600;
}
[data-testid="stMetricValue"] > div {
    font-size: 22px !important;
    font-weight: 700 !important;
    color: #0F172A !important;
}
/* ── Freshness badges ─────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .04em;
    vertical-align: middle;
}
.badge-ok   { background: #DCFCE7; color: #166534; }
.badge-warn { background: #FEF9C3; color: #854D0E; }
.badge-dead { background: #FEE2E2; color: #991B1B; }
/* ── Pipeline status bar ──────────────────────────────────────────────── */
.status-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 13px;
    color: #64748B;
}
.status-sep { color: #CBD5E1; }
/* ── Section labels ───────────────────────────────────────────────────── */
.section-label {
    font-size: 11px;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: .08em;
    font-weight: 700;
    margin: 0 0 10px 0;
}
/* ── Chart card ───────────────────────────────────────────────────────── */
[data-testid="stPlotlyChart"] {
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 12px 12px 2px;
    background: #fff;
    box-shadow: 0 1px 4px rgba(15,23,42,.05);
    overflow: hidden;
}
/* ── Chart+map row: equal height columns ──────────────────────────────── */
[data-testid="stHorizontalBlock"]:has([data-testid="stVerticalBlockBorderWrapper"]) {
    align-items: stretch;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stVerticalBlockBorderWrapper"])
    > [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
    height: 100%;
}
/* ── Map card (st.container border=True) ──────────────────────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #E2E8F0 !important;
    border-radius: 12px !important;
    box-shadow: 0 1px 4px rgba(15,23,42,.05) !important;
    background: #fff !important;
    padding: 14px 14px 10px !important;
    height: 100% !important;
    box-sizing: border-box !important;
}
/* ── Region selector — compact pill above chart ───────────────────────── */
.region-selector [data-testid="stSelectbox"] > div > div {
    border-radius: 20px;
    border-color: #E2E8F0;
    font-size: 13px;
    min-height: 34px;
    padding: 0 12px;
    background: #F8FAFC;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Load data
# ─────────────────────────────────────────────────────────────────────────────

with st.spinner("Loading…"):
    forecasts  = load_latest_forecasts()
    actuals    = load_actuals(hours=48)
    status     = load_system_status()
    metrics_df = load_metrics()
    n_complete = load_completeness()

# Keep only timestamps where all 12 regions have reported.
# Partial timestamps (API lag) produce artificially low sums — drop them.
_N_REGIONS = len(REGION_CENTROIDS)
_complete_ts = actuals.groupby("date_heure")["region"].nunique()
actuals = actuals[actuals["date_heure"].isin(_complete_ts[_complete_ts == _N_REGIONS].index)]

if forecasts.empty:
    st.warning("No forecast data yet — run the forecast job first.")
    st.stop()

now_utc       = pd.Timestamp.now(tz="UTC")
forecasted_at = forecasts["forecasted_at"].iloc[0]
model_ver     = (forecasts["model_version"].iloc[0] or "")[:8]

fut = forecasts[forecasts["forecast_horizon_dt"] > now_utc]
france_total = (
    fut[fut["forecast_horizon_dt"] == fut["forecast_horizon_dt"].min()]["predicted_mw"].sum()
    if not fut.empty else None
)

completeness_pct = round(100 * n_complete / EXPECTED_SLOTS_24H, 1)

france_row = metrics_df[metrics_df["region"] == "France"]
mae_mw    = france_row["mae_mw"].iloc[0]       if not france_row.empty else None
p95_mw    = france_row["p95_error_mw"].iloc[0] if not france_row.empty else None
n_matched = int(france_row["n_samples"].iloc[0]) if not france_row.empty else 0


# ─────────────────────────────────────────────────────────────────────────────
# Header — title + pipeline status
# ─────────────────────────────────────────────────────────────────────────────

col_title, col_status = st.columns([3, 2])

with col_title:
    st.markdown("## ⚡ Electricity Demand Forecast France")
    st.caption(f"Model `{model_ver}…` · Forecasted {_ago(forecasted_at)}")

with col_status:
    ingest_ts   = status.get("last_ingest")
    features_ts = status.get("last_features")
    forecast_ts = status.get("last_forecast")
    st.markdown(
        f'<div class="status-bar" style="justify-content:flex-end; padding-top:14px">'
        f'{_badge("Ingest", ingest_ts)} <span>{_ago(ingest_ts)}</span>'
        f' <span class="status-sep">·</span>'
        f' {_badge("Features", features_ts)} <span>{_ago(features_ts)}</span>'
        f' <span class="status-sep">·</span>'
        f' {_badge("Forecast", forecast_ts, daily=True)} <span>{_ago(forecast_ts)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# KPI strip
# ─────────────────────────────────────────────────────────────────────────────

k1, k2, k3, k4 = st.columns(4)
k1.metric("France total · next 15 min (predicted)", _fmt_mw(france_total))
k2.metric(
    "MAE — 7d rolling", _fmt_mw(mae_mw),
    help=f"{n_matched:,} complete forecast slots evaluated",
)
k3.metric("p95 error", _fmt_mw(p95_mw))
k4.metric(
    "Data completeness (24 h)", f"{completeness_pct} %",
    help=f"{n_complete:,} / {EXPECTED_SLOTS_24H:,} expected 15-min slots",
)

st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Chart | Map
# ─────────────────────────────────────────────────────────────────────────────

col_chart, col_map = st.columns([1.6, 1])

with col_chart:
    # Label + region selector inline
    lbl_col, sel_col = st.columns([2, 1.2])
    with lbl_col:
        st.markdown('<p class="section-label">Realized vs Predicted</p>', unsafe_allow_html=True)
    with sel_col:
        regions    = [ALL_REGIONS] + sorted(forecasts["region"].unique().tolist())
        st.markdown('<div class="region-selector">', unsafe_allow_html=True)
        sel_region = st.selectbox("Region", options=regions, index=0, label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)
    st.plotly_chart(build_timeseries(forecasts, actuals, sel_region), use_container_width=True)

with col_map:
    with st.container(border=True):
        st.markdown('<p class="section-label">Predicted demand · avg next 24 h</p>', unsafe_allow_html=True)
        st_folium(build_map(forecasts), width=None, height=306, returned_objects=[])
        st.caption("Circle size and opacity → relative predicted demand.")

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
MAP_COLOR          = "#2563EB"  # single indigo-blue


# ─────────────────────────────────────────────────────────────────────────────
# BQ queries
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _bq() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


@st.cache_data(ttl=300)
def load_latest_forecasts() -> pd.DataFrame:
    sql = f"""
    WITH ranked AS (
        SELECT forecast_horizon_dt, region, predicted_mw, model_version, scored_at,
               ROW_NUMBER() OVER (
                   PARTITION BY forecast_horizon_dt, region
                   ORDER BY scored_at DESC
               ) AS rn
        FROM `{PROJECT}.elec_ml.predictions`
        WHERE forecast_horizon_dt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 48 HOUR)
          AND forecast_horizon_dt <= TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    )
    SELECT forecast_horizon_dt, region, predicted_mw, model_version, scored_at
    FROM ranked
    WHERE rn = 1
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
        (SELECT MAX(scored_at)        FROM `{PROJECT}.elec_ml.predictions`)    AS last_score
    """
    return _bq().query(sql).to_dataframe().iloc[0].to_dict()


@st.cache_data(ttl=300)
def load_error_metrics() -> dict:
    sql = f"""
    WITH preds AS (
        SELECT forecast_horizon_dt, region, predicted_mw
        FROM `{PROJECT}.elec_ml.predictions`
        WHERE scored_at = (SELECT MAX(scored_at) FROM `{PROJECT}.elec_ml.predictions`)
    ),
    matched AS (
        SELECT ABS(p.predicted_mw - e.consommation) AS abs_error
        FROM preds AS p
        JOIN `{PROJECT}.elec_raw.eco2mix` AS e
            ON  e.region     = p.region
            AND e.date_heure = p.forecast_horizon_dt
            AND e.consommation IS NOT NULL
    )
    SELECT
        COUNT(*)                                     AS n_matched,
        AVG(abs_error)                               AS mae_mw,
        APPROX_QUANTILES(abs_error, 100)[OFFSET(95)] AS p95_mw,
        APPROX_QUANTILES(abs_error, 100)[OFFSET(99)] AS p99_mw
    FROM matched
    """
    return _bq().query(sql).to_dataframe().iloc[0].to_dict()


@st.cache_data(ttl=300)
def load_completeness() -> int:
    """Count of distinct (date_heure, region) slots in eco2mix for last 24 h."""
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
    if mins < 1:   return "just now"
    if mins < 60:  return f"{mins} min ago"
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


def _badge(label: str, ts) -> str:
    cls = _freshness_cls(ts)
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
            radius=10 + t * 20,                 # 10 … 30 px
            color=MAP_COLOR,
            fill=True,
            fill_color=MAP_COLOR,
            fill_opacity=0.30 + t * 0.55,       # 0.30 … 0.85 — opacity encodes level
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

    if region == ALL_REGIONS:
        pred_g = (
            forecasts.groupby("forecast_horizon_dt")["predicted_mw"]
            .sum().reset_index().sort_values("forecast_horizon_dt")
        )
        act_g = (
            actuals.groupby("date_heure")["consommation"]
            .sum().reset_index().sort_values("date_heure")
        )
        px, py = pred_g["forecast_horizon_dt"], pred_g["predicted_mw"]
        ax, ay = act_g["date_heure"],           act_g["consommation"]
        title  = "France total — Realized vs Predicted"
        y_label = "Total consumption (MW)"
    else:
        pred_r = forecasts[forecasts["region"] == region].sort_values("forecast_horizon_dt")
        act_r  = actuals[actuals["region"] == region].sort_values("date_heure")
        px, py = pred_r["forecast_horizon_dt"], pred_r["predicted_mw"]
        ax, ay = act_r["date_heure"],           act_r["consommation"]
        title  = f"{region} — Realized vs Predicted"
        y_label = "Consumption (MW)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ax, y=ay,
        name="Actual",
        line=dict(color="#1E3A5F", width=2),
        mode="lines",
    ))
    fig.add_trace(go.Scatter(
        x=px, y=py,
        name="Predicted",
        line=dict(color=MAP_COLOR, width=2, dash="dash"),
        mode="lines",
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
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color="#0F172A")),
        yaxis_title=y_label,
        xaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(
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
# Page
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Elec Forecast", layout="wide")

st.markdown("""
<style>
/* metric cards */
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
/* freshness badges */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .04em;
    vertical-align: middle;
}
.badge-ok   { background: #DCFCE7; color: #166534; }
.badge-warn { background: #FEF9C3; color: #854D0E; }
.badge-dead { background: #FEE2E2; color: #991B1B; }
/* section labels */
.label {
    font-size: 11px;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: .08em;
    font-weight: 700;
    margin: 0 0 6px 0;
}
/* freshness rows */
.fr-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    font-size: 14px;
    color: #334155;
}
</style>
""", unsafe_allow_html=True)

st.title("Electricity Demand Forecast — France")

with st.spinner("Loading…"):
    forecasts  = load_latest_forecasts()
    actuals    = load_actuals(hours=48)
    status     = load_system_status()
    metrics    = load_error_metrics()
    n_complete = load_completeness()

if forecasts.empty:
    st.warning("No forecast data yet — run the score job first.")
    st.stop()

now_utc   = pd.Timestamp.now(tz="UTC")
scored_at = forecasts["scored_at"].iloc[0]
model_ver = (forecasts["model_version"].iloc[0] or "")[:8]
n_matched = int(metrics.get("n_matched", 0) or 0)

# France total for the next upcoming 15-min slot
fut = forecasts[forecasts["forecast_horizon_dt"] > now_utc]
if not fut.empty:
    next_dt        = fut["forecast_horizon_dt"].min()
    france_total   = fut[fut["forecast_horizon_dt"] == next_dt]["predicted_mw"].sum()
else:
    france_total   = None

completeness_pct = round(100 * n_complete / EXPECTED_SLOTS_24H, 1)

st.caption(f"Last score: {_ago(scored_at)} · model `{model_ver}…`")

# ── KPI row ───────────────────────────────────────────────────────────────────

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("France total (next slot)", _fmt_mw(france_total))
k2.metric(
    "Completeness (24 h)",
    f"{completeness_pct} %",
    help=f"{n_complete:,} / {EXPECTED_SLOTS_24H:,} expected 15-min slots",
)
k3.metric("MAE",      _fmt_mw(metrics.get("mae_mw")), help=f"Matched pairs: {n_matched:,}")
k4.metric("p95 error", _fmt_mw(metrics.get("p95_mw")))
k5.metric("p99 error", _fmt_mw(metrics.get("p99_mw")))

st.divider()

# ── Freshness | Map ───────────────────────────────────────────────────────────

col_l, col_r = st.columns([1, 2.6])

with col_l:
    st.markdown('<p class="label">Pipeline freshness</p>', unsafe_allow_html=True)
    for name, key in [("Ingest", "last_ingest"), ("Features", "last_features"), ("Score", "last_score")]:
        ts = status.get(key)
        st.markdown(
            f'<div class="fr-row">'
            f'{_badge(name, ts)}'
            f'<span>{_ago(ts)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<p class="label">Model performance</p>', unsafe_allow_html=True)
    if n_matched == 0:
        st.caption("No matched actuals yet — horizons haven't elapsed (24 h).")
    else:
        st.caption(f"{n_matched:,} matched prediction/actual pairs")

with col_r:
    st.markdown('<p class="label">Predicted demand — next 24 h avg per region</p>', unsafe_allow_html=True)
    st_folium(build_map(forecasts), width=None, height=220, returned_objects=[])
    st.caption("Circle size and opacity scale with predicted demand.")

st.divider()

# ── Time series ───────────────────────────────────────────────────────────────

st.markdown('<p class="label">Realized vs Predicted</p>', unsafe_allow_html=True)

regions    = [ALL_REGIONS] + sorted(forecasts["region"].unique().tolist())
sel_region = st.selectbox("Region", options=regions, index=0, label_visibility="collapsed")
st.plotly_chart(build_timeseries(forecasts, actuals, sel_region), use_container_width=True)

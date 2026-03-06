"""Streamlit dashboard — France electricity demand forecast."""

import os

import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

PROJECT = os.environ["GCP_PROJECT_ID"].strip()
BUCKET  = os.environ.get("GCS_BUCKET", "").strip()

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

FRANCE_GEOJSON_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master"
    "/regions-version-simplifiee.geojson"
)

EXPECTED_SLOTS_24H = 96 * 12
FRANCE_TOTAL       = "France (total)"

# Blue palette used throughout all charts for visual coherence
_C0 = "#EFF6FF"   # lightest
_C1 = "#BFDBFE"
_C2 = "#3B82F6"
_C3 = "#2563EB"
_C4 = "#1D4ED8"
_C5 = "#1E3A8A"   # darkest
BLUE_SCALE = [[0.0, _C0], [0.35, _C1], [0.65, _C3], [1.0, _C5]]


# ─────────────────────────────────────────────────────────────────────────────
# BQ queries
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _bq() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


@st.cache_data(ttl=86400)
def load_france_geojson() -> dict | None:
    try:
        resp = requests.get(FRANCE_GEOJSON_URL, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


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
        (SELECT MAX(_ingested_at)     FROM `{PROJECT}.elec_raw.eco2mix`)        AS last_ingest,
        (SELECT MAX(_materialized_at) FROM `{PROJECT}.elec_features.features`)  AS last_features,
        (SELECT MAX(forecasted_at)    FROM `{PROJECT}.elec_ml.predictions`)     AS last_forecast,
        (SELECT MAX(_computed_at)     FROM `{PROJECT}.elec_ml.metrics`)         AS last_eval
    """
    return _bq().query(sql).to_dataframe().iloc[0].to_dict()


@st.cache_data(ttl=300)
def load_train_status():
    """Return the last-updated timestamp of models/latest_run_id in GCS, or None."""
    if not BUCKET:
        return None
    try:
        from google.cloud import storage as _gcs
        blob = _gcs.Client(project=PROJECT).bucket(BUCKET).blob("models/latest_run_id")
        blob.reload()
        return blob.updated   # tz-aware datetime
    except Exception:
        return None


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
# Chart builders
# ─────────────────────────────────────────────────────────────────────────────

_AXIS = dict(
    showgrid=True, gridcolor="#E2E8F0", zeroline=False,
    showline=True, linecolor="#CBD5E1",
    tickfont=dict(color="#475569", size=11),
)
_CANVAS = dict(plot_bgcolor="white", paper_bgcolor="white")


def build_choropleth(forecasts: pd.DataFrame, geojson: dict) -> go.Figure:
    avg_mw = (
        forecasts.groupby("region")["predicted_mw"]
        .mean().reset_index()
        .rename(columns={"predicted_mw": "avg_mw"})
    )
    fig = go.Figure(go.Choroplethmapbox(
        geojson=geojson,
        locations=avg_mw["region"],
        z=avg_mw["avg_mw"],
        featureidkey="properties.nom",
        colorscale=BLUE_SCALE,
        zmin=avg_mw["avg_mw"].min() * 0.85,
        zmax=avg_mw["avg_mw"].max() * 1.05,
        marker_opacity=0.82,
        marker_line_width=1.5,
        marker_line_color="white",
        colorbar=dict(
            title=dict(text="MW", font=dict(size=12, color="#64748B")),
            thickness=12, len=0.65, x=1.0,
            tickfont=dict(size=10, color="#475569"),
        ),
        hovertemplate="<b>%{location}</b><br>Avg predicted: <b>%{z:,.0f} MW</b><extra></extra>",
    ))
    fig.update_layout(
        **_CANVAS,
        title=dict(text="Predicted demand — avg next 24 h", font=dict(size=14, color="#0F172A")),
        mapbox_style="carto-positron",
        mapbox_zoom=4.5,
        mapbox_center={"lat": 46.5, "lon": 2.5},
        margin=dict(l=0, r=0, t=44, b=0),
        height=490,
    )
    return fig


def build_timeseries(forecasts: pd.DataFrame, actuals: pd.DataFrame, region: str) -> go.Figure:
    now_utc       = pd.Timestamp.now(tz="UTC")
    today_paris   = now_utc.tz_convert("Europe/Paris").normalize()
    day_start_utc = today_paris.tz_convert("UTC")
    day_end_utc   = (today_paris + pd.Timedelta(days=1)).tz_convert("UTC")

    if region == FRANCE_TOTAL:
        pred_g = (
            forecasts.groupby("forecast_horizon_dt")["predicted_mw"]
            .sum().reset_index().sort_values("forecast_horizon_dt")
        )
        act_g = (
            actuals.groupby("date_heure")["consommation"]
            .sum().reset_index().sort_values("date_heure")
        )
        px_, py = pred_g["forecast_horizon_dt"], pred_g["predicted_mw"]
        ax, ay  = act_g["date_heure"],           act_g["consommation"]
        title   = "France — realized vs predicted"
        y_label = "Total consumption (MW)"
    else:
        pred_r = forecasts[forecasts["region"] == region].sort_values("forecast_horizon_dt")
        act_r  = actuals[actuals["region"] == region].sort_values("date_heure")
        px_, py = pred_r["forecast_horizon_dt"], pred_r["predicted_mw"]
        ax, ay  = act_r["date_heure"],           act_r["consommation"]
        title   = f"{region} — realized vs predicted"
        y_label = "Consumption (MW)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ax, y=ay, name="Actual",
        line=dict(color="#1E3A5F", width=2), mode="lines",
    ))
    fig.add_trace(go.Scatter(
        x=px_, y=py, name="Predicted",
        line=dict(color=_C3, width=2, dash="dash"), mode="lines",
    ))
    fig.update_layout(
        **_CANVAS,
        title=dict(text=title, font=dict(size=14, color="#0F172A")),
        xaxis=dict(**_AXIS, range=[day_start_utc, day_end_utc], title=None),
        yaxis=dict(**_AXIS, title=y_label, title_font=dict(color="#475569")),
        shapes=[{
            "type": "line", "xref": "x", "yref": "paper",
            "x0": now_utc, "x1": now_utc, "y0": 0, "y1": 1,
            "line": {"dash": "dot", "color": "#F97316", "width": 1.5},
        }],
        annotations=[{
            "x": now_utc, "y": 1, "xref": "x", "yref": "paper",
            "text": "now", "showarrow": False,
            "font": {"color": "#F97316", "size": 11},
            "xanchor": "left", "yanchor": "top",
        }],
        legend=dict(
            x=0.01, y=0.99, xanchor="left", yanchor="top",
            bgcolor="white", bordercolor="#E2E8F0", borderwidth=1,
            font=dict(size=12, color="#0F172A"),
        ),
        margin=dict(l=0, r=0, t=44, b=0),
        height=420,
    )
    return fig


def build_mae_bars(metrics_df: pd.DataFrame) -> go.Figure:
    df = (
        metrics_df[metrics_df["region"] != "France"]
        .dropna(subset=["mae_mw"])
        .sort_values("mae_mw", ascending=True)
    )
    france     = metrics_df[metrics_df["region"] == "France"]
    france_mae = france["mae_mw"].iloc[0] if not france.empty else None

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["mae_mw"],
        y=df["region"],
        orientation="h",
        marker=dict(
            color=df["mae_mw"],
            colorscale=[[0, _C1], [1, _C4]],
            showscale=False,
        ),
        hovertemplate="%{y}: <b>%{x:,.0f} MW</b><extra></extra>",
    ))
    if france_mae is not None:
        fig.add_vline(
            x=france_mae,
            line_dash="dot", line_color="#F97316", line_width=2,
            annotation_text=f"France avg {france_mae:,.0f} MW",
            annotation_position="top right",
            annotation_font=dict(color="#F97316", size=11),
        )
    fig.update_layout(
        **_CANVAS,
        title=dict(text="MAE by region — 7d rolling", font=dict(size=14, color="#0F172A")),
        xaxis=dict(**_AXIS, title="MAE (MW)"),
        yaxis={**_AXIS, "showgrid": False},
        margin=dict(l=0, r=20, t=44, b=0),
        height=400,
    )
    return fig


def build_heatmap(forecasts: pd.DataFrame) -> go.Figure:
    df = forecasts.copy()
    df["hour_paris"] = df["forecast_horizon_dt"].dt.tz_convert("Europe/Paris").dt.hour

    pivot = (
        df.groupby(["region", "hour_paris"])["predicted_mw"]
        .mean()
        .unstack("hour_paris")
    )
    # Most demand-intensive regions on top
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

    short_name = {
        "Provence-Alpes-Côte d'Azur": "PACA",
        "Auvergne-Rhône-Alpes":       "Auvergne-RA",
        "Bourgogne-Franche-Comté":    "Bourgogne-FC",
        "Centre-Val de Loire":         "Centre-VdL",
    }
    y_labels = [short_name.get(r, r) for r in pivot.index]
    x_labels = [f"{h:02d}:00" for h in range(24)]

    z_values = [
        [pivot.loc[region, h] if h in pivot.columns else None for h in range(24)]
        for region in pivot.index
    ]

    fig = go.Figure(go.Heatmap(
        z=z_values,
        x=x_labels,
        y=y_labels,
        colorscale=BLUE_SCALE,
        hovertemplate="<b>%{y}</b>  %{x}<br>Avg predicted: <b>%{z:,.0f} MW</b><extra></extra>",
        colorbar=dict(
            title=dict(text="MW", font=dict(size=12, color="#64748B")),
            thickness=12,
            tickfont=dict(size=10, color="#475569"),
        ),
        xgap=2,
        ygap=2,
    ))
    fig.update_layout(
        **_CANVAS,
        title=dict(text="Demand heatmap — region × hour (Paris time)", font=dict(size=14, color="#0F172A")),
        xaxis=dict(tickfont=dict(color="#475569", size=10), showline=True, linecolor="#CBD5E1", tickangle=0),
        yaxis=dict(tickfont=dict(color="#475569", size=11), showline=True, linecolor="#CBD5E1", autorange="reversed"),
        margin=dict(l=0, r=0, t=44, b=40),
        height=400,
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Page config + CSS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Elec Forecast", layout="wide", page_icon="⚡")

st.markdown("""
<style>
/* ── Hide default Streamlit top padding ──────────────────────────────── */
[data-testid="stAppViewContainer"] > section > div:first-child { padding-top: 1rem; }

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

/* ── Chart cards — uniform border + shadow for all Plotly charts ─────── */
[data-testid="stPlotlyChart"] {
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 12px 12px 4px;
    background: #fff;
    box-shadow: 0 1px 6px rgba(15,23,42,.06);
    overflow: hidden;
}

/* ── Region selector — compact, pill-shaped ───────────────────────────── */
.region-selector [data-testid="stSelectbox"] > div > div {
    border-radius: 20px;
    border-color: #E2E8F0;
    font-size: 13px;
    min-height: 32px;
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
    geojson    = load_france_geojson()
    train_ts   = load_train_status()

# Drop partial timestamps (API lag: not all 12 regions have reported yet)
_N_REGIONS   = len(REGION_CENTROIDS)
_complete_ts = actuals.groupby("date_heure")["region"].nunique()
actuals      = actuals[actuals["date_heure"].isin(_complete_ts[_complete_ts == _N_REGIONS].index)]

if forecasts.empty:
    st.warning("No forecast data yet — run the forecast job first.")
    st.stop()

now_utc       = pd.Timestamp.now(tz="UTC")
forecasted_at = forecasts["forecasted_at"].max()
model_ver     = (forecasts.loc[forecasts["forecasted_at"].idxmax(), "model_version"] or "")[:8]

fut          = forecasts[forecasts["forecast_horizon_dt"] > now_utc]
france_total = (
    fut[fut["forecast_horizon_dt"] == fut["forecast_horizon_dt"].min()]["predicted_mw"].sum()
    if not fut.empty else None
)
completeness_pct = round(100 * n_complete / EXPECTED_SLOTS_24H, 1)

france_row = metrics_df[metrics_df["region"] == "France"]
mae_mw     = france_row["mae_mw"].iloc[0]       if not france_row.empty else None
p95_mw     = france_row["p95_error_mw"].iloc[0] if not france_row.empty else None
n_matched  = int(france_row["n_samples"].iloc[0]) if not france_row.empty else 0


# ─────────────────────────────────────────────────────────────────────────────
# Contact icons (used in header + footer)
# ─────────────────────────────────────────────────────────────────────────────

_ICON_LI = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="#0A66C2">'
    '<path d="M20.45 20.45h-3.56v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.34V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29zM5.34 7.43a2.07 2.07 0 110-4.14 2.07 2.07 0 010 4.14zM3.56 20.45h3.57V9H3.56v11.45zM22.23 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.46c.98 0 1.77-.77 1.77-1.72V1.72C24 .77 23.21 0 22.23 0z"/>'
    '</svg>'
)
_ICON_GH = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="#0F172A">'
    '<path d="M12 .3a12 12 0 00-3.79 23.4c.6.1.82-.26.82-.58v-2.03c-3.34.73-4.04-1.6-4.04-1.6-.54-1.38-1.33-1.75-1.33-1.75-1.09-.75.08-.73.08-.73 1.2.08 1.84 1.24 1.84 1.24 1.07 1.83 2.8 1.3 3.49.99.1-.78.42-1.3.76-1.6-2.66-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.17 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.28-1.55 3.29-1.23 3.29-1.23.66 1.65.24 2.87.12 3.17.77.84 1.23 1.91 1.23 3.22 0 4.61-2.8 5.63-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.82.58A12 12 0 0012 .3z"/>'
    '</svg>'
)
_ICON_MAIL = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#64748B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="4" width="20" height="16" rx="2"/>'
    '<path d="M2 7l10 7 10-7"/>'
    '</svg>'
)
_ICON_PIN = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z"/>'
    '<circle cx="12" cy="9" r="2.5"/>'
    '</svg>'
)


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────

col_title, col_status = st.columns([3, 2])

with col_title:
    st.markdown("## ⚡ Electricity Demand Forecast France")
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:14px; margin-top:2px; flex-wrap:wrap;">'
        f'<span style="font-size:13px; color:#64748B;">Model <code>{model_ver}…</code> · Forecasted {_ago(forecasted_at)}</span>'
        f'<span style="color:#CBD5E1;">·</span>'
        f'<span style="font-size:13px; font-weight:600; color:#475569;">Adrien Morel</span>'
        f'<a href="https://www.linkedin.com/in/adrien-morel/" target="_blank"'
        f'   style="display:inline-flex; align-items:center; gap:4px; color:#0A66C2; text-decoration:none; font-size:13px;">'
        f'  {_ICON_LI}&nbsp;LinkedIn</a>'
        f'<a href="mailto:adrien.morel@gmail.com"'
        f'   style="display:inline-flex; align-items:center; gap:4px; color:#475569; text-decoration:none; font-size:13px;">'
        f'  {_ICON_MAIL}&nbsp;adrien.morel@gmail.com</a>'
        f'<a href="https://github.com/Adrien-1997/elec-forecast" target="_blank"'
        f'   style="display:inline-flex; align-items:center; gap:4px; color:#0F172A; text-decoration:none; font-size:13px; font-weight:500;">'
        f'  {_ICON_GH}&nbsp;GitHub</a>'
        f'</div>',
        unsafe_allow_html=True,
    )

with col_status:
    ingest_ts   = status.get("last_ingest")
    features_ts = status.get("last_features")
    forecast_ts = status.get("last_forecast")
    eval_ts     = status.get("last_eval")
    st.markdown(
        f'<div style="padding-top:10px; text-align:right;">'
        f'  <div style="font-size:11px; color:#94A3B8; text-transform:uppercase;'
        f'       letter-spacing:.06em; font-weight:600; margin-bottom:5px;">System check</div>'
        f'  <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">'
        f'    <div class="status-bar" style="justify-content:flex-end;">'
        f'      <span style="white-space:nowrap">{_badge("Ingest", ingest_ts)} {_ago(ingest_ts)}</span>'
        f'      <span class="status-sep">·</span>'
        f'      <span style="white-space:nowrap">{_badge("Features", features_ts, daily=True)} {_ago(features_ts)}</span>'
        f'    </div>'
        f'    <div class="status-bar" style="justify-content:flex-end;">'
        f'      <span style="white-space:nowrap">{_badge("Forecast", forecast_ts, daily=True)} {_ago(forecast_ts)}</span>'
        f'      <span class="status-sep">·</span>'
        f'      <span style="white-space:nowrap">{_badge("Retrain", train_ts, daily=True)} {_ago(train_ts)}</span>'
        f'    </div>'
        f'    <div class="status-bar" style="justify-content:flex-end;">'
        f'      <span style="white-space:nowrap">{_badge("Eval", eval_ts)} {_ago(eval_ts)}</span>'
        f'    </div>'
        f'  </div>'
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
    "MAE · France total · 7d rolling", _fmt_mw(mae_mw),
    help=f"Mean absolute error on France-total predictions (sum of all 12 regions). {n_matched:,} complete forecast slots evaluated over the last 7 days.",
)
k3.metric(
    "p95 error · France total · 7d", _fmt_mw(p95_mw),
    help="95th-percentile absolute error on France-total predictions over the last 7 days. 95% of forecasts are within this value.",
)
k4.metric(
    "Data completeness (24 h)", f"{completeness_pct} %",
    help=f"{n_complete:,} / {EXPECTED_SLOTS_24H:,} expected 15-min slots",
)

st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Row 1: Choropleth map | Time series
# ─────────────────────────────────────────────────────────────────────────────

col_map, col_ts = st.columns([1, 1.4])

with col_map:
    if geojson:
        st.plotly_chart(build_choropleth(forecasts, geojson), use_container_width=True)
    else:
        st.info("Map unavailable — GeoJSON could not be fetched.")

with col_ts:
    regions    = [FRANCE_TOTAL] + sorted(forecasts["region"].unique().tolist())
    st.plotly_chart(build_timeseries(forecasts, actuals, st.session_state.get("sel_region", FRANCE_TOTAL)), use_container_width=True)
    st.markdown('<div class="region-selector">', unsafe_allow_html=True)
    sel_region = st.selectbox("Region", options=regions, index=0, label_visibility="collapsed", key="sel_region")
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Row 2: MAE bar chart | Demand heatmap
# ─────────────────────────────────────────────────────────────────────────────

col_mae, col_heat = st.columns([1, 1.6])

with col_mae:
    if not metrics_df.empty:
        st.plotly_chart(build_mae_bars(metrics_df), use_container_width=True)
    else:
        st.info("No metrics yet — run the metrics job first.")

with col_heat:
    st.plotly_chart(build_heatmap(forecasts), use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="text-align:right; color:#CBD5E1; font-size:12px; padding:8px 0 4px;">'
    f'{_ICON_PIN}&nbsp;Paris · 2026</div>',
    unsafe_allow_html=True,
)

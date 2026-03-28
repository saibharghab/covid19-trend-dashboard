"""
COVID-19 Global Trend Analysis & Interactive Dashboard
========================================================
Fetches live WHO/Our World in Data COVID-19 data, processes it,
and builds an interactive Plotly Dash dashboard with:
  - Rolling average smoothing
  - Log-scale toggling
  - Country comparison overlays
  - Automated daily data refresh

Requirements:
    pip install pandas numpy requests plotly dash dash-bootstrap-components

Data Source:
    Our World in Data COVID-19 dataset (public domain)
    https://raw.githubusercontent.com/owid/covid-19-data/master/public/data/owid-covid-data.csv
"""

import os
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

import dash
from dash import dcc, html, Input, Output, callback
import dash_bootstrap_components as dbc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DATA_URL = (
    "https://raw.githubusercontent.com/owid/covid-19-data/master/"
    "public/data/owid-covid-data.csv"
)
CACHE_PATH = "cache/covid_data.csv"
CACHE_TTL_HOURS = 6

METRICS = {
    "new_cases_smoothed":       "Daily New Cases (7-day avg)",
    "new_deaths_smoothed":      "Daily Deaths (7-day avg)",
    "new_cases_smoothed_per_million":  "Cases per Million",
    "new_deaths_smoothed_per_million": "Deaths per Million",
    "people_vaccinated_per_hundred":   "Vaccination Rate (%)",
    "hosp_patients_per_million":       "Hospitalisations per Million",
}

DEFAULT_COUNTRIES = ["United States", "United Kingdom", "India", "Brazil", "Germany", "South Africa"]
EXCLUDE_LOCATIONS = {"World", "Europe", "Asia", "Africa", "North America",
                     "South America", "Oceania", "European Union", "High income",
                     "Low income", "Upper middle income", "Lower middle income"}


# ── Data Ingestion ────────────────────────────────────────────────────────────

def fetch_data(force_refresh: bool = False) -> pd.DataFrame:
    """
    Download OWID COVID-19 dataset with caching.
    Falls back to cache if network is unavailable.
    """
    os.makedirs("cache", exist_ok=True)
    cache_valid = (
        os.path.exists(CACHE_PATH)
        and not force_refresh
        and (datetime.now() - datetime.fromtimestamp(os.path.getmtime(CACHE_PATH)))
        < timedelta(hours=CACHE_TTL_HOURS)
    )

    if cache_valid:
        logger.info("Loading from cache...")
        return pd.read_csv(CACHE_PATH, parse_dates=["date"])

    logger.info("Downloading COVID-19 dataset from Our World in Data...")
    try:
        response = requests.get(DATA_URL, timeout=30)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text), parse_dates=["date"])
        df.to_csv(CACHE_PATH, index=False)
        logger.info(f"Downloaded {len(df):,} rows | Saved to cache.")
        return df
    except Exception as e:
        logger.warning(f"Download failed: {e}")
        if os.path.exists(CACHE_PATH):
            logger.info("Using stale cache.")
            return pd.read_csv(CACHE_PATH, parse_dates=["date"])
        raise RuntimeError("No data available. Check network or provide a local CSV.") from e


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and enrich the dataset."""
    # Filter out aggregated regions
    df = df[~df["location"].isin(EXCLUDE_LOCATIONS)].copy()

    # Ensure numeric columns
    numeric_cols = list(METRICS.keys()) + ["total_cases", "total_deaths", "population"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Derived metrics
    if "total_cases" in df.columns and "population" in df.columns:
        df["case_fatality_rate"] = (df["total_deaths"] / df["total_cases"] * 100).round(3)
        df["cases_per_100k"] = (df["total_cases"] / df["population"] * 100_000).round(2)

    # Rolling 7-day smoothing where API data is absent
    for col in ["new_cases", "new_deaths"]:
        smooth_col = f"{col}_smoothed"
        if smooth_col not in df.columns and col in df.columns:
            df[smooth_col] = (
                df.groupby("location")[col]
                .transform(lambda x: x.rolling(7, min_periods=1).mean())
            )

    df = df.sort_values(["location", "date"]).reset_index(drop=True)
    logger.info(f"Preprocessed: {df['location'].nunique()} countries | "
                f"{df['date'].min().date()} to {df['date'].max().date()}")
    return df


# ── Static Exports ────────────────────────────────────────────────────────────

def export_static_charts(df: pd.DataFrame, output_dir: str = "output") -> None:
    """
    Save static PNG summary charts for reporting / GitHub README.
    """
    os.makedirs(output_dir, exist_ok=True)
    df_filt = df[df["location"].isin(DEFAULT_COUNTRIES)].copy()
    latest = df.groupby("location").last().reset_index()
    latest = latest[~latest["location"].isin(EXCLUDE_LOCATIONS)]

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=[
            "Daily New Cases (7-day avg) – Selected Countries",
            "Daily Deaths (7-day avg) – Selected Countries",
            "Top 20 Countries by Total Cases",
            "Vaccination Rate (%) – Selected Countries",
        ],
    )

    colors = px.colors.qualitative.Set2

    for i, country in enumerate(DEFAULT_COUNTRIES):
        country_df = df_filt[df_filt["location"] == country]
        color = colors[i % len(colors)]

        fig.add_trace(go.Scatter(
            x=country_df["date"], y=country_df["new_cases_smoothed"],
            name=country, line=dict(color=color), legendgroup=country,
            showlegend=True
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=country_df["date"], y=country_df["new_deaths_smoothed"],
            name=country, line=dict(color=color), legendgroup=country,
            showlegend=False
        ), row=1, col=2)

        vax = country_df.dropna(subset=["people_vaccinated_per_hundred"])
        fig.add_trace(go.Scatter(
            x=vax["date"], y=vax["people_vaccinated_per_hundred"],
            name=country, line=dict(color=color), legendgroup=country,
            showlegend=False
        ), row=2, col=2)

    top20 = latest.nlargest(20, "total_cases")[["location", "total_cases"]].dropna()
    fig.add_trace(go.Bar(
        x=top20["total_cases"], y=top20["location"],
        orientation="h", marker_color="#3498db", showlegend=False
    ), row=2, col=1)

    fig.update_layout(
        height=900, title_text="COVID-19 Global Trend Analysis",
        title_font_size=18, template="plotly_white", hovermode="x unified"
    )
    fig.write_image(os.path.join(output_dir, "covid_dashboard.png"), scale=2)
    logger.info(f"Static chart saved to {output_dir}/covid_dashboard.png")


# ── Dash App ──────────────────────────────────────────────────────────────────

def build_dash_app(df: pd.DataFrame) -> dash.Dash:
    """Build and return the Plotly Dash interactive dashboard."""

    all_countries = sorted(df["location"].unique())
    date_min = df["date"].min()
    date_max = df["date"].max()

    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.FLATLY],
        title="COVID-19 Dashboard"
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    app.layout = dbc.Container([
        dbc.Row([
            dbc.Col(html.H2("🌍 COVID-19 Global Trend Dashboard",
                            className="text-primary my-3"), width=8),
            dbc.Col(html.P(f"Data updated: {date_max.strftime('%d %b %Y')}",
                           className="text-muted mt-4"), width=4),
        ]),

        dbc.Row([
            dbc.Col([
                html.Label("Select Countries", className="fw-bold"),
                dcc.Dropdown(
                    id="country-selector",
                    options=[{"label": c, "value": c} for c in all_countries],
                    value=DEFAULT_COUNTRIES[:4],
                    multi=True,
                    placeholder="Select countries...",
                ),
            ], width=5),

            dbc.Col([
                html.Label("Metric", className="fw-bold"),
                dcc.Dropdown(
                    id="metric-selector",
                    options=[{"label": v, "value": k} for k, v in METRICS.items()],
                    value="new_cases_smoothed",
                    clearable=False,
                ),
            ], width=4),

            dbc.Col([
                html.Label("Options", className="fw-bold"),
                dbc.Checklist(
                    id="chart-options",
                    options=[
                        {"label": " Log Scale", "value": "log"},
                        {"label": " Show 30-day Avg", "value": "ma30"},
                    ],
                    value=[],
                    switch=True,
                ),
            ], width=3),
        ], className="mb-3"),

        dbc.Row([
            dbc.Col([
                html.Label("Date Range", className="fw-bold"),
                dcc.DatePickerRange(
                    id="date-range",
                    min_date_allowed=date_min,
                    max_date_allowed=date_max,
                    start_date=date_max - timedelta(days=365),
                    end_date=date_max,
                    display_format="DD MMM YYYY",
                ),
            ], width=12),
        ], className="mb-3"),

        dbc.Row([
            dbc.Col(dcc.Graph(id="trend-chart", style={"height": "420px"}), width=8),
            dbc.Col(dcc.Graph(id="bar-chart", style={"height": "420px"}), width=4),
        ]),

        dbc.Row([
            dbc.Col(dcc.Graph(id="vaccination-chart", style={"height": "320px"}), width=6),
            dbc.Col(dcc.Graph(id="fatality-chart", style={"height": "320px"}), width=6),
        ], className="mt-2"),

        html.Hr(),
        html.P("Data source: Our World in Data (OWID) · Johns Hopkins University · WHO",
               className="text-muted text-center small"),
    ], fluid=True)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    @app.callback(
        Output("trend-chart", "figure"),
        Output("bar-chart", "figure"),
        Output("vaccination-chart", "figure"),
        Output("fatality-chart", "figure"),
        Input("country-selector", "value"),
        Input("metric-selector", "value"),
        Input("chart-options", "value"),
        Input("date-range", "start_date"),
        Input("date-range", "end_date"),
    )
    def update_charts(countries, metric, options, start_date, end_date):
        if not countries:
            empty = go.Figure()
            empty.update_layout(title="Select at least one country")
            return empty, empty, empty, empty

        options = options or []
        mask = (
            df["location"].isin(countries)
            & (df["date"] >= pd.Timestamp(start_date))
            & (df["date"] <= pd.Timestamp(end_date))
        )
        dff = df[mask].copy()

        # ── Trend chart ───────────────────────────────────────────────────────
        trend_fig = go.Figure()
        metric_label = METRICS.get(metric, metric)
        colors = px.colors.qualitative.Set2

        for i, country in enumerate(countries):
            cdf = dff[dff["location"] == country].dropna(subset=[metric])
            if cdf.empty:
                continue
            color = colors[i % len(colors)]
            trend_fig.add_trace(go.Scatter(
                x=cdf["date"], y=cdf[metric],
                name=country, line=dict(color=color, width=1.5),
                hovertemplate=f"<b>{country}</b><br>%{{x|%d %b %Y}}<br>{metric_label}: %{{y:,.0f}}<extra></extra>"
            ))
            if "ma30" in options:
                ma = cdf[metric].rolling(30, min_periods=7).mean()
                trend_fig.add_trace(go.Scatter(
                    x=cdf["date"], y=ma, name=f"{country} (30d avg)",
                    line=dict(color=color, dash="dot", width=2),
                    showlegend=False
                ))

        trend_fig.update_layout(
            title=metric_label,
            yaxis_type="log" if "log" in options else "linear",
            template="plotly_white", hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=40, r=20, t=60, b=40),
        )

        # ── Bar chart: latest cumulative totals ───────────────────────────────
        latest_data = []
        total_col = "total_cases" if "case" in metric else "total_deaths"
        if total_col not in df.columns:
            total_col = "total_cases"

        for country in countries:
            row = dff[dff["location"] == country].dropna(subset=[total_col]).tail(1)
            if not row.empty:
                latest_data.append({"country": country, "value": row[total_col].values[0]})

        bar_df = pd.DataFrame(latest_data).sort_values("value", ascending=True)
        bar_fig = go.Figure(go.Bar(
            x=bar_df["value"], y=bar_df["country"],
            orientation="h",
            marker_color=[colors[i % len(colors)] for i in range(len(bar_df))],
            text=bar_df["value"].apply(lambda x: f"{x:,.0f}"),
            textposition="outside",
        ))
        bar_fig.update_layout(
            title=f"Cumulative {total_col.replace('_', ' ').title()}",
            template="plotly_white", xaxis_tickformat=",",
            margin=dict(l=100, r=60, t=60, b=40),
        )

        # ── Vaccination chart ─────────────────────────────────────────────────
        vax_fig = go.Figure()
        for i, country in enumerate(countries):
            cdf = dff[dff["location"] == country].dropna(subset=["people_vaccinated_per_hundred"])
            if cdf.empty:
                continue
            vax_fig.add_trace(go.Scatter(
                x=cdf["date"], y=cdf["people_vaccinated_per_hundred"],
                name=country, line=dict(color=colors[i % len(colors)]),
                fill="tozeroy", fillcolor=f"rgba(0,0,0,0.02)"
            ))
        vax_fig.update_layout(
            title="Vaccination Rate (%)", template="plotly_white",
            yaxis=dict(range=[0, 100], ticksuffix="%"),
            hovermode="x unified", margin=dict(l=40, r=20, t=60, b=40),
        )

        # ── Case fatality rate chart ──────────────────────────────────────────
        cfr_fig = go.Figure()
        if "case_fatality_rate" in dff.columns:
            for i, country in enumerate(countries):
                cdf = dff[dff["location"] == country].dropna(subset=["case_fatality_rate"])
                cdf = cdf[cdf["case_fatality_rate"] > 0]
                if cdf.empty:
                    continue
                cfr_fig.add_trace(go.Scatter(
                    x=cdf["date"], y=cdf["case_fatality_rate"],
                    name=country, line=dict(color=colors[i % len(colors)])
                ))
        cfr_fig.update_layout(
            title="Case Fatality Rate (%)", template="plotly_white",
            yaxis=dict(ticksuffix="%"),
            hovermode="x unified", margin=dict(l=40, r=20, t=60, b=40),
        )

        return trend_fig, bar_fig, vax_fig, cfr_fig

    return app


# ── Main ──────────────────────────────────────────────────────────────────────

def main(export_only: bool = False):
    df_raw = fetch_data()
    df = preprocess(df_raw)

    os.makedirs("output", exist_ok=True)

    if export_only:
        try:
            export_static_charts(df)
        except Exception as e:
            logger.warning(f"Static export requires kaleido: pip install kaleido. Error: {e}")
        print("\n✅ Static charts exported to output/")
        return

    # Summary stats
    latest = df.groupby("location").last().reset_index()
    latest_filtered = latest[~latest["location"].isin(EXCLUDE_LOCATIONS)]

    print("\n" + "=" * 55)
    print("COVID-19 GLOBAL SUMMARY")
    print("=" * 55)
    print(f"Countries tracked : {df['location'].nunique()}")
    print(f"Date range        : {df['date'].min().date()} → {df['date'].max().date()}")
    if "total_cases" in latest_filtered.columns:
        global_cases = latest_filtered["total_cases"].sum()
        global_deaths = latest_filtered["total_deaths"].sum()
        print(f"Total cases       : {global_cases:,.0f}")
        print(f"Total deaths      : {global_deaths:,.0f}")
        print(f"Global CFR        : {global_deaths/global_cases*100:.2f}%")
    print("=" * 55)
    print("\nStarting interactive dashboard at http://localhost:8050")
    print("Press Ctrl+C to stop.\n")

    app = build_dash_app(df)
    app.run(debug=False, port=8050)


if __name__ == "__main__":
    import sys
    main(export_only="--export" in sys.argv)

"""
Flexkaart — GOPACS Marktberichten Dashboard
Analyseert congestiemanagement in het Nederlandse elektriciteitsnet,
verrijkt met KNMI-weerdata en CBS/PDOK-gebiedsinformatie.
"""

import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from pathlib import Path

from data_import import import_excel_to_db, create_database, parse_mw_profile
from api_clients import fetch_weather, cache_weather_to_db

# ──────────────────────────────────────────────
# Configuratie
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="Flexkaart — GOPACS Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = "flexkaart.db"
EXCEL_PATH = "GOPACS_Marktberichten.xlsx"

# Kleurenschema
COLORS = {
    "primary": "#1B4F72",
    "secondary": "#2E86C1",
    "accent": "#F39C12",
    "success": "#27AE60",
    "danger": "#E74C3C",
    "enexis": "#F39C12",
    "liander": "#2E86C1",
    "stedin": "#27AE60",
    "tennet": "#8E44AD",
    "alliander": "#E74C3C",
}

NETBEHEERDER_KLEUREN = {
    "Enexis": COLORS["enexis"],
    "Liander": COLORS["liander"],
    "Stedin": COLORS["stedin"],
    "TenneT": COLORS["tennet"],
    "Alliander": COLORS["alliander"],
}


# ──────────────────────────────────────────────
# Data laden & caching
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_marktberichten() -> pd.DataFrame:
    """Laad marktberichten uit de SQLite database."""
    if not Path(DB_PATH).exists():
        with st.spinner("Database wordt aangemaakt vanuit Excel..."):
            import_excel_to_db(EXCEL_PATH, DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM marktberichten", conn)
    conn.close()

    # Converteer datum kolommen
    date_cols = [
        "datum_aangemaakt", "datum_laatste_update", "dag",
        "periode_start", "periode_einde", "bieding_start", "bieding_einde"
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    df["datum"] = pd.to_datetime(df["datum"], errors="coerce")
    return df


@st.cache_data(ttl=3600)
def load_weerdata(start_date: str, end_date: str) -> pd.DataFrame:
    """Laad weerdata — uit cache of via API."""
    conn = sqlite3.connect(DB_PATH)

    # Check of we al data hebben
    try:
        cached = pd.read_sql("SELECT * FROM weerdata", conn)
        if not cached.empty:
            cached["datum"] = pd.to_datetime(cached["datum"], errors="coerce")
            conn.close()
            return cached
    except Exception:
        pass
    conn.close()

    # Haal op via Open-Meteo (gratis, geen key nodig)
    cache_weather_to_db(DB_PATH, start_date, end_date)

    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM weerdata", conn)
        df["datum"] = pd.to_datetime(df["datum"], errors="coerce")
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# ──────────────────────────────────────────────
# Sidebar & Filters
# ──────────────────────────────────────────────

def render_sidebar(df: pd.DataFrame) -> dict:
    """Render de sidebar met filters en geef filter-dict terug."""
    st.sidebar.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/"
        "Coat_of_arms_of_the_Netherlands.svg/100px-Coat_of_arms_of_the_Netherlands.svg.png",
        width=60,
    )
    st.sidebar.title("Flexkaart")
    st.sidebar.caption("GOPACS Marktberichten Analyse")

    st.sidebar.divider()
    st.sidebar.subheader("Filters")

    # Datumbereik
    min_date = df["datum"].min()
    max_date = df["datum"].max()

    if pd.isna(min_date):
        min_date = datetime(2020, 1, 1)
    if pd.isna(max_date):
        max_date = datetime.now()

    date_range = st.sidebar.date_input(
        "Periode",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    # Netbeheerder
    netbeheerders = ["Alle"] + sorted(df["netbeheerder"].dropna().unique().tolist())
    sel_netbeheerder = st.sidebar.multiselect(
        "Netbeheerder", netbeheerders, default=["Alle"]
    )

    # Type
    types = ["Alle"] + sorted(df["type"].dropna().unique().tolist())
    sel_type = st.sidebar.selectbox("Type bericht", types)

    # Status
    statussen = ["Alle"] + sorted(df["status"].dropna().unique().tolist())
    sel_status = st.sidebar.selectbox("Status", statussen)

    # Provincie
    provincies = ["Alle"] + sorted(df["provincie"].dropna().unique().tolist())
    sel_provincie = st.sidebar.selectbox("Provincie", provincies)

    st.sidebar.divider()
    st.sidebar.markdown(
        "**Data:** GOPACS Marktberichten  \n"
        "**Weer:** Open-Meteo (gratis)  \n"
        "**Geo:** PDOK / CBS Open Data"
    )

    return {
        "date_range": date_range,
        "netbeheerder": sel_netbeheerder,
        "type": sel_type,
        "status": sel_status,
        "provincie": sel_provincie,
    }


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Pas de sidebar-filters toe op het DataFrame."""
    filtered = df.copy()

    # Datum filter
    dr = filters["date_range"]
    if isinstance(dr, (list, tuple)) and len(dr) == 2:
        start, end = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
        filtered = filtered[
            (filtered["datum"] >= start) & (filtered["datum"] <= end)
        ]

    # Netbeheerder
    if "Alle" not in filters["netbeheerder"] and filters["netbeheerder"]:
        filtered = filtered[filtered["netbeheerder"].isin(filters["netbeheerder"])]

    # Type
    if filters["type"] != "Alle":
        filtered = filtered[filtered["type"] == filters["type"]]

    # Status
    if filters["status"] != "Alle":
        filtered = filtered[filtered["status"] == filters["status"]]

    # Provincie
    if filters["provincie"] != "Alle":
        filtered = filtered[filtered["provincie"] == filters["provincie"]]

    return filtered


# ──────────────────────────────────────────────
# KPI's bovenaan
# ──────────────────────────────────────────────

def render_kpis(df: pd.DataFrame):
    """Toon de belangrijkste KPI's."""
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Totaal berichten", f"{len(df):,}")

    with col2:
        n_open = len(df[df["status"] == "Open"])
        st.metric("Open", f"{n_open:,}")

    with col3:
        avg_mw = df["gem_vereist_mw"].mean()
        st.metric("Gem. profiel (MW)", f"{avg_mw:.2f}" if pd.notna(avg_mw) else "—")

    with col4:
        n_gebieden = df["buy_orders_gebied"].nunique()
        st.metric("Unieke gebieden", f"{n_gebieden:,}")

    with col5:
        gem_duur = df["duur_uur"].mean()
        st.metric("Gem. duur (uur)", f"{gem_duur:.1f}" if pd.notna(gem_duur) else "—")


# ──────────────────────────────────────────────
# Tab 1: Interactieve Kaart
# ──────────────────────────────────────────────

def render_kaart(df: pd.DataFrame):
    """Toon een interactieve kaart van Nederland met congestiegebieden."""
    st.subheader("Congestiegebieden in Nederland")

    map_df = df.dropna(subset=["lat", "lon"]).copy()

    if map_df.empty:
        st.warning("Geen geolocatie-data beschikbaar voor de huidige selectie.")
        return

    # Aggregeer per locatie
    agg = (
        map_df.groupby(["postcode_eerste", "lat", "lon", "provincie", "netbeheerder"])
        .agg(
            aantal=("id", "count"),
            gem_mw=("gem_vereist_mw", "mean"),
            max_mw=("max_vereist_mw", "max"),
            gem_duur=("duur_uur", "mean"),
        )
        .reset_index()
    )

    fig = px.scatter_map(
        agg,
        lat="lat",
        lon="lon",
        size="aantal",
        color="netbeheerder",
        color_discrete_map=NETBEHEERDER_KLEUREN,
        hover_name="postcode_eerste",
        hover_data={
            "provincie": True,
            "aantal": True,
            "gem_mw": ":.2f",
            "max_mw": ":.2f",
            "gem_duur": ":.1f",
            "lat": False,
            "lon": False,
        },
        size_max=30,
        zoom=6.5,
        center={"lat": 52.2, "lon": 5.5},
        title="Congestiegebieden per postcode",
    )
    fig.update_layout(
        map_style="carto-positron",
        height=600,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Heatmap alternatief
    with st.expander("Dichtheidskaart (heatmap)"):
        fig_heat = px.density_map(
            map_df,
            lat="lat",
            lon="lon",
            z="gem_vereist_mw",
            radius=20,
            center={"lat": 52.2, "lon": 5.5},
            zoom=6.5,
            color_continuous_scale="YlOrRd",
            title="Energie-intensiteit per gebied",
        )
        fig_heat.update_layout(
            map_style="carto-positron",
            height=500,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_heat, use_container_width=True)


# ──────────────────────────────────────────────
# Tab 2: Correlatie-Dashboard (Weer × Congestie)
# ──────────────────────────────────────────────

def render_correlatie(df: pd.DataFrame, weerdata: pd.DataFrame):
    """Toon correlatie tussen weer en congestiefrequentie."""
    st.subheader("Weer & Congestie Correlatie")

    if weerdata.empty:
        st.warning("Geen weerdata beschikbaar. Controleer je internetverbinding.")
        return

    # Dagelijks aantal berichten
    dag_counts = (
        df.groupby(df["datum"].dt.date)
        .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"))
        .reset_index()
    )
    dag_counts.columns = ["datum", "berichten", "gem_mw"]
    dag_counts["datum"] = pd.to_datetime(dag_counts["datum"])

    # Merge met weerdata
    weer_dag = weerdata.copy()
    weer_dag["datum"] = pd.to_datetime(weer_dag["datum"]).dt.normalize()
    dag_counts["datum"] = dag_counts["datum"].dt.normalize()

    merged = pd.merge(dag_counts, weer_dag, on="datum", how="inner")

    if merged.empty:
        st.info("Geen overlap gevonden tussen berichten-data en weerdata.")
        return

    # Dual-axis chart: berichten vs windsnelheid
    col1, col2 = st.columns(2)

    with col1:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(
                x=merged["datum"], y=merged["berichten"],
                name="Berichten", marker_color=COLORS["secondary"], opacity=0.6,
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=merged["datum"], y=merged["windsnelheid"],
                name="Windsnelheid (m/s)", line=dict(color=COLORS["accent"], width=2),
            ),
            secondary_y=True,
        )
        fig.update_layout(
            title="Berichten vs. Windsnelheid",
            height=400,
            legend=dict(orientation="h", y=-0.15),
        )
        fig.update_yaxes(title_text="Aantal berichten", secondary_y=False)
        fig.update_yaxes(title_text="Wind (m/s)", secondary_y=True)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(
            go.Bar(
                x=merged["datum"], y=merged["berichten"],
                name="Berichten", marker_color=COLORS["secondary"], opacity=0.6,
            ),
            secondary_y=False,
        )
        fig2.add_trace(
            go.Scatter(
                x=merged["datum"], y=merged["zonneschijnduur"],
                name="Zonneschijn (uur)", line=dict(color=COLORS["enexis"], width=2),
            ),
            secondary_y=True,
        )
        fig2.update_layout(
            title="Berichten vs. Zonneschijnduur",
            height=400,
            legend=dict(orientation="h", y=-0.15),
        )
        fig2.update_yaxes(title_text="Aantal berichten", secondary_y=False)
        fig2.update_yaxes(title_text="Zon (uur)", secondary_y=True)
        st.plotly_chart(fig2, use_container_width=True)

    # Scatter: correlatie
    st.markdown("#### Correlatie-analyse")
    col3, col4 = st.columns(2)

    with col3:
        fig_scatter = px.scatter(
            merged, x="windsnelheid", y="berichten",
            trendline="ols", color_discrete_sequence=[COLORS["secondary"]],
            title="Wind → Congestiefrequentie",
            labels={"windsnelheid": "Windsnelheid (m/s)", "berichten": "Berichten/dag"},
        )
        fig_scatter.update_layout(height=350)
        st.plotly_chart(fig_scatter, use_container_width=True)

        # Correlatie-coëfficiënt
        corr_wind = merged["windsnelheid"].corr(merged["berichten"])
        if pd.notna(corr_wind):
            st.caption(f"Pearson correlatie wind ↔ berichten: **{corr_wind:.3f}**")

    with col4:
        fig_scatter2 = px.scatter(
            merged, x="zonneschijnduur", y="berichten",
            trendline="ols", color_discrete_sequence=[COLORS["enexis"]],
            title="Zon → Congestiefrequentie",
            labels={"zonneschijnduur": "Zonneschijnduur (uur)", "berichten": "Berichten/dag"},
        )
        fig_scatter2.update_layout(height=350)
        st.plotly_chart(fig_scatter2, use_container_width=True)

        corr_zon = merged["zonneschijnduur"].corr(merged["berichten"])
        if pd.notna(corr_zon):
            st.caption(f"Pearson correlatie zon ↔ berichten: **{corr_zon:.3f}**")


# ──────────────────────────────────────────────
# Tab 3: Provincie-vergelijker
# ──────────────────────────────────────────────

def render_provincie_vergelijker(df: pd.DataFrame):
    """Vergelijk congestie-activiteit per provincie + top 10 hotspots."""
    st.subheader("Provincie-vergelijker")

    prov_df = df.dropna(subset=["provincie"]).copy()

    if prov_df.empty:
        st.warning("Geen provinciedata beschikbaar.")
        return

    col1, col2 = st.columns(2)

    with col1:
        # Berichten per provincie
        prov_agg = (
            prov_df.groupby("provincie")
            .agg(
                berichten=("id", "count"),
                gem_mw=("gem_vereist_mw", "mean"),
                unieke_gebieden=("buy_orders_gebied", "nunique"),
            )
            .reset_index()
            .sort_values("berichten", ascending=True)
        )

        fig = px.bar(
            prov_agg, y="provincie", x="berichten",
            orientation="h",
            color="gem_mw",
            color_continuous_scale="YlOrRd",
            title="Berichten per provincie",
            labels={"berichten": "Aantal", "provincie": "", "gem_mw": "Gem. MW"},
        )
        fig.update_layout(height=450)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Netbeheerder verdeling per provincie
        prov_nb = (
            prov_df.groupby(["provincie", "netbeheerder"])
            .size()
            .reset_index(name="aantal")
        )

        fig2 = px.bar(
            prov_nb, x="provincie", y="aantal", color="netbeheerder",
            color_discrete_map=NETBEHEERDER_KLEUREN,
            title="Netbeheerder per provincie",
            barmode="stack",
        )
        fig2.update_layout(height=450, xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)

    # Top 10 Flex-hotspots
    st.markdown("#### Top 10 Flex-hotspots")

    # Bepaal de meest recente maand met data
    recent = prov_df.copy()
    recent["maand_label"] = recent["datum"].dt.to_period("M").astype(str)
    laatste_maand = recent["maand_label"].max()

    maand_keuze = st.selectbox(
        "Selecteer maand", sorted(recent["maand_label"].unique(), reverse=True),
        index=0,
    )

    maand_df = recent[recent["maand_label"] == maand_keuze]

    hotspots = (
        maand_df.groupby("buy_orders_gebied")
        .agg(
            berichten=("id", "count"),
            gem_mw=("gem_vereist_mw", "mean"),
            max_mw=("max_vereist_mw", "max"),
            gem_duur=("duur_uur", "mean"),
            provincie=("provincie", "first"),
            netbeheerder=("netbeheerder", "first"),
        )
        .reset_index()
        .sort_values("berichten", ascending=False)
        .head(10)
    )

    if not hotspots.empty:
        hotspots_display = hotspots.rename(columns={
            "buy_orders_gebied": "Gebied",
            "berichten": "Berichten",
            "gem_mw": "Gem. MW",
            "max_mw": "Max MW",
            "gem_duur": "Gem. duur (u)",
            "provincie": "Provincie",
            "netbeheerder": "Netbeheerder",
        })
        hotspots_display["Gem. MW"] = hotspots_display["Gem. MW"].round(2)
        hotspots_display["Max MW"] = hotspots_display["Max MW"].round(2)
        hotspots_display["Gem. duur (u)"] = hotspots_display["Gem. duur (u)"].round(1)

        st.dataframe(
            hotspots_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Berichten": st.column_config.ProgressColumn(
                    "Berichten", min_value=0,
                    max_value=int(hotspots_display["Berichten"].max()),
                    format="%d",
                ),
            },
        )
    else:
        st.info(f"Geen data voor {maand_keuze}.")

    # Tijdlijn per provincie
    st.markdown("#### Activiteit over tijd per provincie")
    prov_tijd = (
        prov_df.groupby([prov_df["datum"].dt.to_period("M").astype(str), "provincie"])
        .size()
        .reset_index(name="berichten")
    )
    prov_tijd.columns = ["maand", "provincie", "berichten"]

    fig3 = px.line(
        prov_tijd, x="maand", y="berichten", color="provincie",
        title="Maandelijkse berichten per provincie",
    )
    fig3.update_layout(height=400, xaxis_tickangle=-45)
    st.plotly_chart(fig3, use_container_width=True)


# ──────────────────────────────────────────────
# Tab 4: Gebieds-deep-dive
# ──────────────────────────────────────────────

def render_gebiedsdeepdive(df: pd.DataFrame):
    """Laat details zien voor een specifiek congestiegebied."""
    st.subheader("Gebieds-deep-dive")

    # Selecteer een gebied
    gebieden = sorted(df["buy_orders_gebied"].dropna().unique().tolist())

    if not gebieden:
        st.warning("Geen gebieden beschikbaar.")
        return

    selected = st.selectbox("Selecteer een congestiegebied", gebieden)

    gebied_df = df[df["buy_orders_gebied"] == selected].copy()

    if gebied_df.empty:
        st.info("Geen data voor dit gebied.")
        return

    # KPI's voor dit gebied
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Berichten", len(gebied_df))
    with col2:
        st.metric("Netbeheerder", gebied_df["netbeheerder"].mode().iloc[0] if not gebied_df["netbeheerder"].mode().empty else "—")
    with col3:
        avg = gebied_df["gem_vereist_mw"].mean()
        st.metric("Gem. MW", f"{avg:.2f}" if pd.notna(avg) else "—")
    with col4:
        prov = gebied_df["provincie"].mode().iloc[0] if not gebied_df["provincie"].mode().empty else "—"
        st.metric("Provincie", prov)

    # Tijdlijn
    col_left, col_right = st.columns(2)

    with col_left:
        tijd = (
            gebied_df.groupby(gebied_df["datum"].dt.to_period("M").astype(str))
            .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"))
            .reset_index()
        )
        tijd.columns = ["maand", "berichten", "gem_mw"]

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(x=tijd["maand"], y=tijd["berichten"], name="Berichten",
                   marker_color=COLORS["secondary"]),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(x=tijd["maand"], y=tijd["gem_mw"], name="Gem. MW",
                       line=dict(color=COLORS["accent"], width=3)),
            secondary_y=True,
        )
        fig.update_layout(title="Tijdlijn", height=350)
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        # Uur-verdeling
        uur_dist = gebied_df["uur_aangemaakt"].value_counts().sort_index().reset_index()
        uur_dist.columns = ["uur", "aantal"]

        fig2 = px.bar(
            uur_dist, x="uur", y="aantal",
            title="Verdeling over de dag",
            color_discrete_sequence=[COLORS["secondary"]],
        )
        fig2.update_layout(height=350)
        fig2.update_xaxes(dtick=1, title="Uur van de dag")
        st.plotly_chart(fig2, use_container_width=True)

    # MW-profiel visualisatie
    st.markdown("#### MW-profiel analyse")
    profielen = gebied_df["vereist_profiel_mw"].dropna()

    if not profielen.empty:
        # Pak de laatste 5 profielen
        last_profiles = profielen.tail(5).tolist()
        fig_prof = go.Figure()

        for i, prof_str in enumerate(last_profiles):
            values = parse_mw_profile(prof_str)
            if values:
                fig_prof.add_trace(go.Scatter(
                    y=values,
                    mode="lines+markers",
                    name=f"Profiel {i+1}",
                    line=dict(width=2),
                ))

        fig_prof.update_layout(
            title="Recente MW-profielen (kwartierwaarden)",
            xaxis_title="Kwartier",
            yaxis_title="MW",
            height=350,
        )
        st.plotly_chart(fig_prof, use_container_width=True)

    # Energie-intensiteit inschatting (CBS-gebaseerd)
    with st.expander("Energie-intensiteit & Gebiedsinformatie"):
        postcodes = gebied_df["postcodes"].dropna().iloc[0] if not gebied_df["postcodes"].dropna().empty else None
        provincie = prov if prov != "—" else None

        st.markdown(f"**Postcodes:** {postcodes or 'Onbekend'}")
        st.markdown(f"**Provincie:** {provincie or 'Onbekend'}")

        # CBS-data indicatie
        if provincie:
            # Terreintype inschatting op basis van netbeheerder en regio
            nb = gebied_df["netbeheerder"].mode().iloc[0] if not gebied_df["netbeheerder"].mode().empty else ""
            gem_mw = gebied_df["gem_vereist_mw"].mean()

            if pd.notna(gem_mw) and gem_mw > 2:
                st.info("Hoge energie-intensiteit — waarschijnlijk industrieel/agrarisch gebied met veel opwek.")
            elif pd.notna(gem_mw) and gem_mw > 0.5:
                st.info("Gemiddelde energie-intensiteit — gemengd woon-/industriegebied.")
            else:
                st.info("Lage energie-intensiteit — waarschijnlijk residentieel gebied.")

            st.caption(
                "Gebiedsinformatie is gebaseerd op CBS Kerncijfers Wijken en Buurten. "
                "Koppel de PDOK API voor nauwkeurigere terreinclassificatie."
            )


# ──────────────────────────────────────────────
# Tab 5: Overzicht & Statistieken
# ──────────────────────────────────────────────

def render_overzicht(df: pd.DataFrame):
    """Algemeen overzicht met trends en statistieken."""
    st.subheader("Overzicht & Trends")

    col1, col2 = st.columns(2)

    with col1:
        # Berichten per maand
        maand = (
            df.groupby(df["datum"].dt.to_period("M").astype(str))
            .size()
            .reset_index(name="berichten")
        )
        maand.columns = ["maand", "berichten"]

        fig = px.area(
            maand, x="maand", y="berichten",
            title="Berichten per maand",
            color_discrete_sequence=[COLORS["secondary"]],
        )
        fig.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Per netbeheerder over tijd
        nb_maand = (
            df.groupby([df["datum"].dt.to_period("M").astype(str), "netbeheerder"])
            .size()
            .reset_index(name="berichten")
        )
        nb_maand.columns = ["maand", "netbeheerder", "berichten"]

        fig2 = px.area(
            nb_maand, x="maand", y="berichten", color="netbeheerder",
            color_discrete_map=NETBEHEERDER_KLEUREN,
            title="Berichten per netbeheerder",
        )
        fig2.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)

    col3, col4 = st.columns(2)

    with col3:
        # Type verdeling
        type_counts = df["type"].value_counts().reset_index()
        type_counts.columns = ["type", "aantal"]

        fig3 = px.pie(
            type_counts, names="type", values="aantal",
            title="Verdeling berichttype",
            color_discrete_sequence=px.colors.qualitative.Set2,
            hole=0.4,
        )
        fig3.update_layout(height=350)
        st.plotly_chart(fig3, use_container_width=True)

    with col4:
        # Verplichting verdeling over tijd
        verpl_maand = (
            df.groupby([df["datum"].dt.to_period("M").astype(str), "verplichting"])
            .size()
            .reset_index(name="berichten")
        )
        verpl_maand.columns = ["maand", "verplichting", "berichten"]

        fig4 = px.bar(
            verpl_maand, x="maand", y="berichten", color="verplichting",
            title="Vrijwillig vs. Verplicht",
            barmode="stack",
            color_discrete_sequence=[COLORS["success"], COLORS["danger"]],
        )
        fig4.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig4, use_container_width=True)

    # Weekdag-heatmap
    st.markdown("#### Activiteitspatronen")
    if "uur_aangemaakt" in df.columns:
        heatmap_data = (
            df.groupby(["weekdag", "uur_aangemaakt"])
            .size()
            .reset_index(name="berichten")
        )
        # Sorteer weekdagen
        dag_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dag_nl = {
            "Monday": "Maandag", "Tuesday": "Dinsdag", "Wednesday": "Woensdag",
            "Thursday": "Donderdag", "Friday": "Vrijdag", "Saturday": "Zaterdag",
            "Sunday": "Zondag",
        }
        heatmap_data["weekdag_nl"] = heatmap_data["weekdag"].map(dag_nl)
        heatmap_data["weekdag_order"] = heatmap_data["weekdag"].apply(
            lambda x: dag_order.index(x) if x in dag_order else 7
        )
        heatmap_data = heatmap_data.sort_values("weekdag_order")

        pivot = heatmap_data.pivot_table(
            index="weekdag_nl", columns="uur_aangemaakt",
            values="berichten", fill_value=0,
        )
        # Reorder
        nl_order = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
        pivot = pivot.reindex([d for d in nl_order if d in pivot.index])

        fig5 = px.imshow(
            pivot,
            color_continuous_scale="YlOrRd",
            title="Berichten per weekdag en uur",
            labels=dict(x="Uur", y="Dag", color="Berichten"),
            aspect="auto",
        )
        fig5.update_layout(height=300)
        st.plotly_chart(fig5, use_container_width=True)


# ──────────────────────────────────────────────
# Hoofdapplicatie
# ──────────────────────────────────────────────

def main():
    # Initialiseer database als deze niet bestaat
    if not Path(DB_PATH).exists() and Path(EXCEL_PATH).exists():
        with st.spinner("Excel wordt geïmporteerd naar database..."):
            n = import_excel_to_db(EXCEL_PATH, DB_PATH)
            st.toast(f"{n:,} berichten geïmporteerd!", icon="⚡")

    # Laad data
    df = load_marktberichten()

    if df.empty:
        st.error(
            "Geen data gevonden. Plaats `GOPACS_Marktberichten.xlsx` "
            "in dezelfde map als `app.py`."
        )
        return

    # Sidebar filters
    filters = render_sidebar(df)
    filtered = apply_filters(df, filters)

    # KPI's
    render_kpis(filtered)
    st.divider()

    # Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Kaart", "Weer & Correlatie",
        "Provincies", "Gebieds-deep-dive",
        "Overzicht",
    ])

    with tab1:
        render_kaart(filtered)

    with tab2:
        min_d = filtered["datum"].min()
        max_d = filtered["datum"].max()
        start = min_d.strftime("%Y-%m-%d") if pd.notna(min_d) else "2020-01-01"
        end = max_d.strftime("%Y-%m-%d") if pd.notna(max_d) else "2026-04-12"
        weerdata = load_weerdata(start, end)
        render_correlatie(filtered, weerdata)

    with tab3:
        render_provincie_vergelijker(filtered)

    with tab4:
        render_gebiedsdeepdive(filtered)

    with tab5:
        render_overzicht(filtered)


if __name__ == "__main__":
    main()

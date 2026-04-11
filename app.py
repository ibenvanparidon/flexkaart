"""
Flexkaart - GOPACS Marktberichten Dashboard
Analyseert congestiemanagement in het Nederlandse elektriciteitsnet,
verrijkt met Open-Meteo weerdata en CBS/PDOK-gebiedsinformatie.
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
import tempfile
import os

from data_import import import_excel_to_db, create_database, parse_mw_profile
from api_clients import fetch_weather, cache_weather_to_db

# Configuratie
st.set_page_config(
    page_title="Flexkaart - GOPACS Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Op Streamlit Cloud is de app-directory read-only; gebruik /tmp/ voor de database.
_APP_DIR = Path(__file__).resolve().parent
EXCEL_PATH = str(_APP_DIR / "GOPACS_Marktberichten.xlsx")
DB_PATH = os.path.join(tempfile.gettempdir(), "flexkaart.db")

COLORS = {
    "primary": "#1B4F72", "secondary": "#2E86C1", "accent": "#F39C12",
    "success": "#27AE60", "danger": "#E74C3C",
    "enexis": "#F39C12", "liander": "#2E86C1", "stedin": "#27AE60",
    "tennet": "#8E44AD", "alliander": "#E74C3C",
}

NETBEHEERDER_KLEUREN = {
    "Enexis": COLORS["enexis"], "Liander": COLORS["liander"],
    "Stedin": COLORS["stedin"], "TenneT": COLORS["tennet"],
    "Alliander": COLORS["alliander"],
}


# Data laden & caching

@st.cache_data(ttl=3600)
def load_marktberichten():
    if not Path(DB_PATH).exists():
        with st.spinner("Database wordt aangemaakt vanuit Excel..."):
            import_excel_to_db(EXCEL_PATH, DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM marktberichten", conn)
    conn.close()
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
def load_weerdata(start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    try:
        cached = pd.read_sql("SELECT * FROM weerdata", conn)
        if not cached.empty:
            cached["datum"] = pd.to_datetime(cached["datum"], errors="coerce")
            conn.close()
            return cached
    except Exception:
        pass
    conn.close()
    cache_weather_to_db(DB_PATH, start_date, end_date)
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM weerdata", conn)
        df["datum"] = pd.to_datetime(df["datum"], errors="coerce")
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# Sidebar & Filters

def render_sidebar(df):
    st.sidebar.title("Flexkaart")
    st.sidebar.caption("GOPACS Marktberichten Analyse")
    st.sidebar.divider()
    st.sidebar.subheader("Filters")

    min_date = df["datum"].min()
    max_date = df["datum"].max()
    if pd.isna(min_date):
        min_date = datetime(2020, 1, 1)
    if pd.isna(max_date):
        max_date = datetime.now()

    date_range = st.sidebar.date_input(
        "Periode", value=(min_date, max_date),
        min_value=min_date, max_value=max_date,
    )
    netbeheerders = ["Alle"] + sorted(df["netbeheerder"].dropna().unique().tolist())
    sel_netbeheerder = st.sidebar.multiselect("Netbeheerder", netbeheerders, default=["Alle"])
    types = ["Alle"] + sorted(df["type"].dropna().unique().tolist())
    sel_type = st.sidebar.selectbox("Type bericht", types)
    statussen = ["Alle"] + sorted(df["status"].dropna().unique().tolist())
    sel_status = st.sidebar.selectbox("Status", statussen)
    provincies = ["Alle"] + sorted(df["provincie"].dropna().unique().tolist())
    sel_provincie = st.sidebar.selectbox("Provincie", provincies)

    st.sidebar.divider()
    st.sidebar.markdown(
        "**Data:** GOPACS Marktberichten  \n"
        "**Weer:** Open-Meteo (gratis)  \n"
        "**Geo:** PDOK / CBS Open Data"
    )
    return {
        "date_range": date_range, "netbeheerder": sel_netbeheerder,
        "type": sel_type, "status": sel_status, "provincie": sel_provincie,
    }


def apply_filters(df, filters):
    filtered = df.copy()
    dr = filters["date_range"]
    if isinstance(dr, (list, tuple)) and len(dr) == 2:
        start, end = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
        filtered = filtered[(filtered["datum"] >= start) & (filtered["datum"] <= end)]
    if "Alle" not in filters["netbeheerder"] and filters["netbeheerder"]:
        filtered = filtered[filtered["netbeheerder"].isin(filters["netbeheerder"])]
    if filters["type"] != "Alle":
        filtered = filtered[filtered["type"] == filters["type"]]
    if filters["status"] != "Alle":
        filtered = filtered[filtered["status"] == filters["status"]]
    if filters["provincie"] != "Alle":
        filtered = filtered[filtered["provincie"] == filters["provincie"]]
    return filtered


# KPIs

def render_kpis(df):
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Totaal berichten", f"{len(df):,}")
    with col2:
        st.metric("Open", f"{len(df[df['status'] == 'Open']):,}")
    with col3:
        avg_mw = df["gem_vereist_mw"].mean()
        st.metric("Gem. profiel (MW)", f"{avg_mw:.2f}" if pd.notna(avg_mw) else "-")
    with col4:
        st.metric("Unieke gebieden", f"{df['buy_orders_gebied'].nunique():,}")
    with col5:
        gem_duur = df["duur_uur"].mean()
        st.metric("Gem. duur (uur)", f"{gem_duur:.1f}" if pd.notna(gem_duur) else "-")


# Tab 1: Interactieve Kaart

def render_kaart(df):
    st.subheader("Congestiegebieden in Nederland")
    map_df = df.dropna(subset=["lat", "lon"]).copy()
    if map_df.empty:
        st.warning("Geen geolocatie-data beschikbaar voor de huidige selectie.")
        return

    agg = (
        map_df.groupby(["postcode_eerste", "lat", "lon", "provincie", "netbeheerder"])
        .agg(aantal=("id", "count"), gem_mw=("gem_vereist_mw", "mean"),
             max_mw=("max_vereist_mw", "max"), gem_duur=("duur_uur", "mean"))
        .reset_index()
    )
    fig = px.scatter_map(
        agg, lat="lat", lon="lon", size="aantal", color="netbeheerder",
        color_discrete_map=NETBEHEERDER_KLEUREN, hover_name="postcode_eerste",
        hover_data={"provincie": True, "aantal": True, "gem_mw": ":.2f",
                    "max_mw": ":.2f", "gem_duur": ":.1f", "lat": False, "lon": False},
        size_max=30, zoom=6.5, center={"lat": 52.2, "lon": 5.5},
        title="Congestiegebieden per postcode",
    )
    fig.update_layout(map_style="carto-positron", height=600, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Dichtheidskaart (heatmap)"):
        fig_heat = px.density_map(
            map_df, lat="lat", lon="lon", z="gem_vereist_mw", radius=20,
            center={"lat": 52.2, "lon": 5.5}, zoom=6.5,
            color_continuous_scale="YlOrRd", title="Energie-intensiteit per gebied",
        )
        fig_heat.update_layout(map_style="carto-positron", height=500, margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_heat, use_container_width=True)


# Tab 2: Correlatie-Dashboard

def render_correlatie(df, weerdata):
    st.subheader("Weer & Congestie Correlatie")
    if weerdata.empty:
        st.warning("Geen weerdata beschikbaar. Controleer je internetverbinding.")
        return

    dag_counts = (
        df.groupby(df["datum"].dt.date)
        .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"))
        .reset_index()
    )
    dag_counts.columns = ["datum", "berichten", "gem_mw"]
    dag_counts["datum"] = pd.to_datetime(dag_counts["datum"])

    weer_dag = weerdata.copy()
    weer_dag["datum"] = pd.to_datetime(weer_dag["datum"]).dt.normalize()
    dag_counts["datum"] = dag_counts["datum"].dt.normalize()
    merged = pd.merge(dag_counts, weer_dag, on="datum", how="inner")

    if merged.empty:
        st.info("Geen overlap gevonden tussen berichten-data en weerdata.")
        return

    col1, col2 = st.columns(2)
    with col1:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(x=merged["datum"], y=merged["berichten"],
                             name="Berichten", marker_color=COLORS["secondary"], opacity=0.6), secondary_y=False)
        fig.add_trace(go.Scatter(x=merged["datum"], y=merged["windsnelheid"],
                                 name="Windsnelheid (m/s)", line=dict(color=COLORS["accent"], width=2)), secondary_y=True)
        fig.update_layout(title="Berichten vs. Windsnelheid", height=400, legend=dict(orientation="h", y=-0.15))
        fig.update_yaxes(title_text="Aantal berichten", secondary_y=False)
        fig.update_yaxes(title_text="Wind (m/s)", secondary_y=True)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Bar(x=merged["datum"], y=merged["berichten"],
                              name="Berichten", marker_color=COLORS["secondary"], opacity=0.6), secondary_y=False)
        fig2.add_trace(go.Scatter(x=merged["datum"], y=merged["zonneschijnduur"],
                                  name="Zonneschijn (uur)", line=dict(color=COLORS["enexis"], width=2)), secondary_y=True)
        fig2.update_layout(title="Berichten vs. Zonneschijnduur", height=400, legend=dict(orientation="h", y=-0.15))
        fig2.update_yaxes(title_text="Aantal berichten", secondary_y=False)
        fig2.update_yaxes(title_text="Zon (uur)", secondary_y=True)
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### Correlatie-analyse")
    col3, col4 = st.columns(2)
    with col3:
        fig_s = px.scatter(merged, x="windsnelheid", y="berichten", trendline="ols",
                           color_discrete_sequence=[COLORS["secondary"]],
                           title="Wind -> Congestiefrequentie",
                           labels={"windsnelheid": "Windsnelheid (m/s)", "berichten": "Berichten/dag"})
        fig_s.update_layout(height=350)
        st.plotly_chart(fig_s, use_container_width=True)
        corr_wind = merged["windsnelheid"].corr(merged["berichten"])
        if pd.notna(corr_wind):
            st.caption(f"Pearson correlatie wind - berichten: **{corr_wind:.3f}**")

    with col4:
        fig_s2 = px.scatter(merged, x="zonneschijnduur", y="berichten", trendline="ols",
                            color_discrete_sequence=[COLORS["enexis"]],
                            title="Zon -> Congestiefrequentie",
                            labels={"zonneschijnduur": "Zonneschijnduur (uur)", "berichten": "Berichten/dag"})
        fig_s2.update_layout(height=350)
        st.plotly_chart(fig_s2, use_container_width=True)
        corr_zon = merged["zonneschijnduur"].corr(merged["berichten"])
        if pd.notna(corr_zon):
            st.caption(f"Pearson correlatie zon - berichten: **{corr_zon:.3f}**")


# Tab 3: Provincie-vergelijker

def render_provincie_vergelijker(df):
    st.subheader("Provincie-vergelijker")
    prov_df = df.dropna(subset=["provincie"]).copy()
    if prov_df.empty:
        st.warning("Geen provinciedata beschikbaar.")
        return

    col1, col2 = st.columns(2)
    with col1:
        prov_agg = (
            prov_df.groupby("provincie")
            .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"),
                 unieke_gebieden=("buy_orders_gebied", "nunique"))
            .reset_index().sort_values("berichten", ascending=True)
        )
        fig = px.bar(prov_agg, y="provincie", x="berichten", orientation="h",
                     color="gem_mw", color_continuous_scale="YlOrRd",
                     title="Berichten per provincie",
                     labels={"berichten": "Aantal", "provincie": "", "gem_mw": "Gem. MW"})
        fig.update_layout(height=450)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        prov_nb = prov_df.groupby(["provincie", "netbeheerder"]).size().reset_index(name="aantal")
        fig2 = px.bar(prov_nb, x="provincie", y="aantal", color="netbeheerder",
                      color_discrete_map=NETBEHEERDER_KLEUREN,
                      title="Netbeheerder per provincie", barmode="stack")
        fig2.update_layout(height=450, xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### Top 10 Flex-hotspots")
    recent = prov_df.copy()
    recent["maand_label"] = recent["datum"].dt.to_period("M").astype(str)
    maand_keuze = st.selectbox("Selecteer maand",
                               sorted(recent["maand_label"].unique(), reverse=True), index=0)
    maand_df = recent[recent["maand_label"] == maand_keuze]

    hotspots = (
        maand_df.groupby("buy_orders_gebied")
        .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"),
             max_mw=("max_vereist_mw", "max"), gem_duur=("duur_uur", "mean"),
             provincie=("provincie", "first"), netbeheerder=("netbeheerder", "first"))
        .reset_index().sort_values("berichten", ascending=False).head(10)
    )
    if not hotspots.empty:
        disp = hotspots.rename(columns={
            "buy_orders_gebied": "Gebied", "berichten": "Berichten",
            "gem_mw": "Gem. MW", "max_mw": "Max MW", "gem_duur": "Gem. duur (u)",
            "provincie": "Provincie", "netbeheerder": "Netbeheerder",
        })
        disp["Gem. MW"] = disp["Gem. MW"].round(2)
        disp["Max MW"] = disp["Max MW"].round(2)
        disp["Gem. duur (u)"] = disp["Gem. duur (u)"].round(1)
        st.dataframe(disp, use_container_width=True, hide_index=True,
                     column_config={"Berichten": st.column_config.ProgressColumn(
                         "Berichten", min_value=0, max_value=int(disp["Berichten"].max()), format="%d")})
    else:
        st.info(f"Geen data voor {maand_keuze}.")

    st.markdown("#### Activiteit over tijd per provincie")
    prov_tijd = (
        prov_df.groupby([prov_df["datum"].dt.to_period("M").astype(str), "provincie"])
        .size().reset_index(name="berichten")
    )
    prov_tijd.columns = ["maand", "provincie", "berichten"]
    fig3 = px.line(prov_tijd, x="maand", y="berichten", color="provincie",
                   title="Maandelijkse berichten per provincie")
    fig3.update_layout(height=400, xaxis_tickangle=-45)
    st.plotly_chart(fig3, use_container_width=True)


# Tab 4: Gebieds-deep-dive

def render_gebiedsdeepdive(df):
    st.subheader("Gebieds-deep-dive")
    gebieden = sorted(df["buy_orders_gebied"].dropna().unique().tolist())
    if not gebieden:
        st.warning("Geen gebieden beschikbaar.")
        return

    selected = st.selectbox("Selecteer een congestiegebied", gebieden)
    gebied_df = df[df["buy_orders_gebied"] == selected].copy()
    if gebied_df.empty:
        st.info("Geen data voor dit gebied.")
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Berichten", len(gebied_df))
    with col2:
        nb_mode = gebied_df["netbeheerder"].mode()
        st.metric("Netbeheerder", nb_mode.iloc[0] if not nb_mode.empty else "-")
    with col3:
        avg = gebied_df["gem_vereist_mw"].mean()
        st.metric("Gem. MW", f"{avg:.2f}" if pd.notna(avg) else "-")
    with col4:
        prov_mode = gebied_df["provincie"].mode()
        prov = prov_mode.iloc[0] if not prov_mode.empty else "-"
        st.metric("Provincie", prov)

    col_left, col_right = st.columns(2)
    with col_left:
        tijd = (
            gebied_df.groupby(gebied_df["datum"].dt.to_period("M").astype(str))
            .agg(berichten=("id", "count"), gem_mw=("gem_vereist_mw", "mean"))
            .reset_index()
        )
        tijd.columns = ["maand", "berichten", "gem_mw"]
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(x=tijd["maand"], y=tijd["berichten"], name="Berichten",
                             marker_color=COLORS["secondary"]), secondary_y=False)
        fig.add_trace(go.Scatter(x=tijd["maand"], y=tijd["gem_mw"], name="Gem. MW",
                                 line=dict(color=COLORS["accent"], width=3)), secondary_y=True)
        fig.update_layout(title="Tijdlijn", height=350)
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        uur_dist = gebied_df["uur_aangemaakt"].value_counts().sort_index().reset_index()
        uur_dist.columns = ["uur", "aantal"]
        fig2 = px.bar(uur_dist, x="uur", y="aantal", title="Verdeling over de dag",
                      color_discrete_sequence=[COLORS["secondary"]])
        fig2.update_layout(height=350)
        fig2.update_xaxes(dtick=1, title="Uur van de dag")
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### MW-profiel analyse")
    profielen = gebied_df["vereist_profiel_mw"].dropna()
    if not profielen.empty:
        last_profiles = profielen.tail(5).tolist()
        fig_prof = go.Figure()
        for i, prof_str in enumerate(last_profiles):
            values = parse_mw_profile(prof_str)
            if values:
                fig_prof.add_trace(go.Scatter(y=values, mode="lines+markers",
                                              name=f"Profiel {i+1}", line=dict(width=2)))
        fig_prof.update_layout(title="Recente MW-profielen (kwartierwaarden)",
                               xaxis_title="Kwartier", yaxis_title="MW", height=350)
        st.plotly_chart(fig_prof, use_container_width=True)

    with st.expander("Energie-intensiteit & Gebiedsinformatie"):
        postcodes = gebied_df["postcodes"].dropna().iloc[0] if not gebied_df["postcodes"].dropna().empty else None
        provincie = prov if prov != "-" else None
        st.markdown(f"**Postcodes:** {postcodes or 'Onbekend'}")
        st.markdown(f"**Provincie:** {provincie or 'Onbekend'}")
        if provincie:
            gem_mw = gebied_df["gem_vereist_mw"].mean()
            if pd.notna(gem_mw) and gem_mw > 2:
                st.info("Hoge energie-intensiteit - waarschijnlijk industrieel/agrarisch gebied met veel opwek.")
            elif pd.notna(gem_mw) and gem_mw > 0.5:
                st.info("Gemiddelde energie-intensiteit - gemengd woon-/industriegebied.")
            else:
                st.info("Lage energie-intensiteit - waarschijnlijk residentieel gebied.")
            st.caption("Gebiedsinformatie is gebaseerd op CBS Kerncijfers Wijken en Buurten. "
                       "Koppel de PDOK API voor nauwkeurigere terreinclassificatie.")


# Tab 5: Overzicht & Statistieken

def render_overzicht(df):
    st.subheader("Overzicht & Trends")
    col1, col2 = st.columns(2)
    with col1:
        maand = df.groupby(df["datum"].dt.to_period("M").astype(str)).size().reset_index(name="berichten")
        maand.columns = ["maand", "berichten"]
        fig = px.area(maand, x="maand", y="berichten", title="Berichten per maand",
                      color_discrete_sequence=[COLORS["secondary"]])
        fig.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        nb_maand = (
            df.groupby([df["datum"].dt.to_period("M").astype(str), "netbeheerder"])
            .size().reset_index(name="berichten")
        )
        nb_maand.columns = ["maand", "netbeheerder", "berichten"]
        fig2 = px.area(nb_maand, x="maand", y="berichten", color="netbeheerder",
                       color_discrete_map=NETBEHEERDER_KLEUREN, title="Berichten per netbeheerder")
        fig2.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        type_counts = df["type"].value_counts().reset_index()
        type_counts.columns = ["type", "aantal"]
        fig3 = px.pie(type_counts, names="type", values="aantal", title="Verdeling berichttype",
                      color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4)
        fig3.update_layout(height=350)
        st.plotly_chart(fig3, use_container_width=True)

    with col4:
        verpl_maand = (
            df.groupby([df["datum"].dt.to_period("M").astype(str), "verplichting"])
            .size().reset_index(name="berichten")
        )
        verpl_maand.columns = ["maand", "verplichting", "berichten"]
        fig4 = px.bar(verpl_maand, x="maand", y="berichten", color="verplichting",
                      title="Vrijwillig vs. Verplicht", barmode="stack",
                      color_discrete_sequence=[COLORS["success"], COLORS["danger"]])
        fig4.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig4, use_container_width=True)

    st.markdown("#### Activiteitspatronen")
    if "uur_aangemaakt" in df.columns:
        heatmap_data = df.groupby(["weekdag", "uur_aangemaakt"]).size().reset_index(name="berichten")
        dag_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dag_nl = {"Monday": "Maandag", "Tuesday": "Dinsdag", "Wednesday": "Woensdag",
                  "Thursday": "Donderdag", "Friday": "Vrijdag", "Saturday": "Zaterdag", "Sunday": "Zondag"}
        heatmap_data["weekdag_nl"] = heatmap_data["weekdag"].map(dag_nl)
        heatmap_data["weekdag_order"] = heatmap_data["weekdag"].apply(
            lambda x: dag_order.index(x) if x in dag_order else 7)
        heatmap_data = heatmap_data.sort_values("weekdag_order")
        pivot = heatmap_data.pivot_table(index="weekdag_nl", columns="uur_aangemaakt",
                                         values="berichten", fill_value=0)
        nl_order = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
        pivot = pivot.reindex([d for d in nl_order if d in pivot.index])
        fig5 = px.imshow(pivot, color_continuous_scale="YlOrRd",
                         title="Berichten per weekdag en uur",
                         labels=dict(x="Uur", y="Dag", color="Berichten"), aspect="auto")
        fig5.update_layout(height=300)
        st.plotly_chart(fig5, use_container_width=True)


# Hoofdapplicatie

def main():
    if not Path(DB_PATH).exists() and Path(EXCEL_PATH).exists():
        with st.spinner("Excel wordt geimporteerd naar database..."):
            n = import_excel_to_db(EXCEL_PATH, DB_PATH)
            st.toast(f"{n:,} berichten geimporteerd!", icon="⚡")

    df = load_marktberichten()
    if df.empty:
        st.error("Geen data gevonden. Plaats GOPACS_Marktberichten.xlsx in dezelfde map als app.py.")
        return

    filters = render_sidebar(df)
    filtered = apply_filters(df, filters)
    render_kpis(filtered)
    st.divider()

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Kaart", "Weer & Correlatie", "Provincies", "Gebieds-deep-dive", "Overzicht",
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

"""
Flexkaart — Professioneel GOPACS Congestiemanagement Dashboard
Multi-source marktdata met 4 strategische tabs.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sqlite3
import tempfile
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────
st.set_page_config(
    page_title="Flexkaart — GOPACS Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = os.path.join(tempfile.gettempdir(), "flexkaart.db")
_APP_DIR = Path(__file__).resolve().parent
EXCEL_PATH = str(_APP_DIR / "GOPACS_Marktberichten.xlsx")

# Kleurenpalet
COLORS = {
    "primary": "#1B4F72",
    "secondary": "#2E86C1",
    "accent": "#F39C12",
    "success": "#27AE60",
    "danger": "#E74C3C",
    "bg": "#F8F9FA",
    "grid": "#ECF0F1",
}

NETBEHEERDER_COLORS = {
    "TenneT": "#003366",
    "Stedin": "#E87722",
    "Liander": "#009B3A",
    "Enexis": "#0072CE",
    "Westland Infra": "#8B4513",
    "Coteq Netbeheer": "#6A0DAD",
    "Enduris": "#C0392B",
    "Rendo": "#16A085",
}


# ── Data ophalen & caching ───────────────────────────

def _ensure_db():
    """Zorg dat de database bestaat en gevuld is."""
    from data_fetcher import init_db, fetch_all
    if not os.path.exists(DB_PATH):
        init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    except Exception:
        count = 0
    conn.close()
    if count == 0:
        return True  # needs fetch
    return False


def _do_fetch():
    """Voer een volledige data-fetch uit."""
    from data_fetcher import fetch_all
    progress = st.sidebar.progress(0, text="Data ophalen...")
    status_text = st.sidebar.empty()

    step_count = 5
    current = [0]

    def callback(msg):
        current[0] += 1
        progress.progress(min(current[0] / step_count, 1.0), text=msg)
        status_text.text(msg)

    results = fetch_all(DB_PATH, progress_callback=callback)
    progress.progress(1.0, text="Klaar!")
    return results


def _also_import_excel():
    """Importeer Excel data als marktberichten tabel (fallback/aanvulling)."""
    if os.path.exists(EXCEL_PATH):
        try:
            from data_import import import_excel_to_db
            import_excel_to_db(EXCEL_PATH, DB_PATH)
            return True
        except Exception:
            pass
    return False


@st.cache_data(ttl=3600)
def load_announcements():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM announcements ORDER BY created_ts DESC", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_cleared_buckets():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM cleared_buckets ORDER BY start_time DESC", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_expenses():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM expenses ORDER BY year, month", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_performance():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM performance ORDER BY year, month", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_weather():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM weather ORDER BY datum", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_marktberichten():
    """Laad de originele Excel-gebaseerde marktberichten (voor geodata tab)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM marktberichten", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=3600)
def load_fetch_log():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM fetch_log", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# ── Helper functies ──────────────────────────────────

def format_eur(val):
    if pd.isna(val):
        return "—"
    return f"\u20ac {val:,.0f}"


def format_mwh(val):
    if pd.isna(val):
        return "—"
    return f"{val:,.1f} MWh"


def plotly_layout(fig, title="", height=400):
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color=COLORS["primary"])),
        height=height,
        margin=dict(l=40, r=20, t=50, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Inter, sans-serif", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
    )
    fig.update_xaxes(gridcolor=COLORS["grid"], showgrid=True)
    fig.update_yaxes(gridcolor=COLORS["grid"], showgrid=True)
    return fig


def parse_zip_codes(zip_json):
    """Parse zip_codes JSON array naar lijst."""
    if not zip_json or zip_json == "[]":
        return []
    try:
        return json.loads(zip_json)
    except Exception:
        return []


def zip_to_province(zip_code):
    """Eenvoudige postcode -> provincie mapping."""
    from data_import import postcode_to_provincie
    return postcode_to_provincie(str(zip_code)) or "Onbekend"


def zip_to_coords(zip_code):
    """Postcode -> (lat, lon) mapping."""
    from data_import import geocode_postcode
    return geocode_postcode(str(zip_code))


# ── Custom CSS ───────────────────────────────────────

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 24px;
        font-weight: 600;
    }
    .metric-card {
        background: white;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        border-left: 4px solid #2E86C1;
    }
    .metric-value {
        font-size: 28px;
        font-weight: 700;
        color: #1B4F72;
    }
    .metric-label {
        font-size: 13px;
        color: #7F8C8D;
        margin-top: 4px;
    }
    div[data-testid="stMetric"] {
        background: white;
        border-radius: 10px;
        padding: 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/energy-saving-bulb.png", width=48)
    st.title("Flexkaart")
    st.caption("GOPACS Congestiemanagement Dashboard")
    st.divider()

    needs_fetch = _ensure_db()

    if st.button("Data vernieuwen", type="primary", use_container_width=True):
        results = _do_fetch()
        _also_import_excel()
        st.cache_data.clear()
        st.success("Data succesvol opgehaald!")
        for src, cnt in results.items():
            st.write(f"  {src}: {cnt}")
        st.rerun()

    if needs_fetch:
        st.warning("Database is leeg — klik 'Data vernieuwen' om te starten.")
        _do_fetch()
        _also_import_excel()
        st.cache_data.clear()
        st.rerun()

    # Fetch log
    log_df = load_fetch_log()
    if not log_df.empty:
        st.divider()
        st.subheader("Laatste sync")
        for _, row in log_df.iterrows():
            st.text(f"{row['source']}: {row.get('records_fetched', '?')} records")
            if row.get("last_fetch"):
                ts = row["last_fetch"][:16].replace("T", " ")
                st.caption(ts)


# ── Main Tabs ────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "Flexkaart (Geodata)",
    "Marktwaarde (Financials)",
    "Kostenanalyse (Expenses)",
    "Performance",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: FLEXKAART / GEODATA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab1:
    st.header("Flexkaart — Congestiegebieden")

    ann_df = load_announcements()
    weather_df = load_weather()
    mkb_df = load_marktberichten()

    # Probeer eerst announcements, fallback naar marktberichten
    if not ann_df.empty:
        # Enrich announcements met postcodes en geocoding
        map_data = []
        for _, row in ann_df.iterrows():
            zips = parse_zip_codes(row.get("zip_codes", "[]"))
            if zips:
                first_zip = zips[0]
                lat, lon = zip_to_coords(first_zip)
                if lat and lon:
                    map_data.append({
                        "lat": lat, "lon": lon,
                        "id": row["id"],
                        "organisation": row.get("organisation", ""),
                        "state": row.get("state", ""),
                        "type": row.get("type", ""),
                        "gem_required_mw": row.get("gem_required_mw"),
                        "datum": row.get("datum"),
                        "problem_area": row.get("problem_area", ""),
                        "provincie": zip_to_province(first_zip),
                        "postcode": first_zip,
                    })
        map_df = pd.DataFrame(map_data) if map_data else pd.DataFrame()
    elif not mkb_df.empty:
        map_df = mkb_df[mkb_df["lat"].notna() & mkb_df["lon"].notna()].copy()
        if "gem_vereist_mw" in map_df.columns:
            map_df.rename(columns={"gem_vereist_mw": "gem_required_mw"}, inplace=True)
        if "netbeheerder" in map_df.columns:
            map_df.rename(columns={"netbeheerder": "organisation"}, inplace=True)
    else:
        map_df = pd.DataFrame()

    if not map_df.empty:
        # KPI rij
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total_ann = len(ann_df) if not ann_df.empty else len(mkb_df)
            st.metric("Totaal berichten", f"{total_ann:,}")
        with col2:
            n_gebieden = map_df["problem_area"].nunique() if "problem_area" in map_df.columns else map_df.get("congestiegebied", pd.Series()).nunique()
            st.metric("Congestiegebieden", n_gebieden)
        with col3:
            if "gem_required_mw" in map_df.columns:
                avg_mw = map_df["gem_required_mw"].mean()
                st.metric("Gem. vereist MW", f"{avg_mw:.1f}" if pd.notna(avg_mw) else "—")
            else:
                st.metric("Gem. vereist MW", "—")
        with col4:
            n_prov = map_df["provincie"].nunique() if "provincie" in map_df.columns else 0
            st.metric("Provincies", n_prov)

        st.divider()

        # Kaart
        col_map, col_side = st.columns([2, 1])

        with col_map:
            color_col = "organisation" if "organisation" in map_df.columns else None
            hover_data = ["datum", "state", "problem_area"] if "problem_area" in map_df.columns else []

            # Prepareer size kolom: vul NaN met 0 en zorg voor positieve waarden
            use_size = None
            if "gem_required_mw" in map_df.columns and map_df["gem_required_mw"].notna().any():
                map_df["_size"] = map_df["gem_required_mw"].fillna(0).clip(lower=0.1)
                use_size = "_size"

            fig_map = px.scatter_map(
                map_df,
                lat="lat", lon="lon",
                size=use_size,
                color=color_col,
                color_discrete_map=NETBEHEERDER_COLORS if color_col else None,
                hover_data=[c for c in hover_data if c in map_df.columns],
                zoom=6.5,
                center={"lat": 52.2, "lon": 5.3},
                height=550,
                title="Congestielocaties Nederland",
            )
            fig_map.update_layout(
                map_style="carto-positron",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_map, use_container_width=True)

        with col_side:
            st.subheader("Per provincie")
            if "provincie" in map_df.columns:
                prov_counts = map_df["provincie"].value_counts().reset_index()
                prov_counts.columns = ["Provincie", "Aantal"]
                fig_prov = px.bar(
                    prov_counts, x="Aantal", y="Provincie", orientation="h",
                    color_discrete_sequence=[COLORS["secondary"]],
                )
                plotly_layout(fig_prov, height=350)
                st.plotly_chart(fig_prov, use_container_width=True)

            # Weer-correlatie als beschikbaar
            if not weather_df.empty and not ann_df.empty and "datum" in ann_df.columns:
                st.subheader("Weer-correlatie")
                ann_per_dag = ann_df.groupby("datum").size().reset_index(name="n_berichten")
                weer_merge = weather_df.merge(ann_per_dag, on="datum", how="inner")
                if not weer_merge.empty and "temp_gem" in weer_merge.columns:
                    corr = weer_merge[["temp_gem", "windsnelheid", "neerslag", "n_berichten"]].corr()
                    st.caption("Correlatie met aantal berichten:")
                    st.write(f"  Temperatuur: {corr.loc['temp_gem', 'n_berichten']:.2f}")
                    st.write(f"  Wind: {corr.loc['windsnelheid', 'n_berichten']:.2f}")
                    st.write(f"  Neerslag: {corr.loc['neerslag', 'n_berichten']:.2f}")

    else:
        st.info("Geen geodata beschikbaar. Klik op 'Data vernieuwen' in de sidebar.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: MARKTWAARDE / FINANCIALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab2:
    st.header("Marktwaarde — Gerealiseerde Transacties")

    cb_df = load_cleared_buckets()
    perf_df = load_performance()

    if not cb_df.empty:
        # Bereken MWh en prijzen
        cb_df["total_volume_mwh"] = cb_df["buy_volume_mwh"].fillna(0) + cb_df["sell_volume_mwh"].fillna(0)
        cb_df["datum_dt"] = pd.to_datetime(cb_df["datum"], errors="coerce")
        cb_df["maand_label"] = cb_df["datum_dt"].dt.to_period("M").astype(str)

        # KPI's
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Transacties", f"{len(cb_df):,}")
        with col2:
            total_buy = cb_df["buy_volume_mwh"].sum()
            st.metric("Totaal buy volume", format_mwh(total_buy))
        with col3:
            total_sell = cb_df["sell_volume_mwh"].sum()
            st.metric("Totaal sell volume", format_mwh(total_sell))
        with col4:
            if not perf_df.empty:
                total_spread = perf_df["spread_eur"].sum()
                st.metric("Totale spread", format_eur(total_spread))
            else:
                st.metric("Totale spread", "—")

        st.divider()

        # Maandelijks volume
        col_vol, col_price = st.columns(2)

        with col_vol:
            monthly = cb_df.groupby("maand_label").agg(
                buy=("buy_volume_mwh", "sum"),
                sell=("sell_volume_mwh", "sum"),
            ).reset_index()

            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(
                x=monthly["maand_label"], y=monthly["buy"],
                name="Buy volume (MWh)", marker_color=COLORS["secondary"],
            ))
            fig_vol.add_trace(go.Bar(
                x=monthly["maand_label"], y=monthly["sell"],
                name="Sell volume (MWh)", marker_color=COLORS["accent"],
            ))
            plotly_layout(fig_vol, "Maandelijks transactievolume (MWh)", 400)
            fig_vol.update_layout(barmode="group")
            st.plotly_chart(fig_vol, use_container_width=True)

        with col_price:
            if not perf_df.empty:
                perf_df["maand_label"] = perf_df.apply(
                    lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1
                )
                perf_df["avg_buy_price"] = np.where(
                    perf_df["buy_volume_mwh"] > 0,
                    perf_df["buy_price_eur"] / perf_df["buy_volume_mwh"],
                    0
                )
                perf_df["avg_sell_price"] = np.where(
                    perf_df["sell_volume_mwh"] > 0,
                    perf_df["sell_price_eur"] / perf_df["sell_volume_mwh"],
                    0
                )

                fig_price = go.Figure()
                fig_price.add_trace(go.Scatter(
                    x=perf_df["maand_label"], y=perf_df["avg_buy_price"],
                    mode="lines+markers", name="Gem. buy prijs (EUR/MWh)",
                    line=dict(color=COLORS["secondary"], width=2),
                ))
                fig_price.add_trace(go.Scatter(
                    x=perf_df["maand_label"], y=perf_df["avg_sell_price"],
                    mode="lines+markers", name="Gem. sell prijs (EUR/MWh)",
                    line=dict(color=COLORS["accent"], width=2),
                ))
                plotly_layout(fig_price, "Gemiddelde prijs per MWh", 400)
                st.plotly_chart(fig_price, use_container_width=True)

        # Volume per netbeheerder
        st.subheader("Transacties per netbeheerder")
        if "organisation" in cb_df.columns:
            org_vol = cb_df.groupby("organisation").agg(
                buy_total=("buy_volume_mwh", "sum"),
                sell_total=("sell_volume_mwh", "sum"),
                n_transacties=("clearing_event_id", "count"),
            ).reset_index().sort_values("buy_total", ascending=False)

            fig_org = px.bar(
                org_vol.melt(id_vars=["organisation"], value_vars=["buy_total", "sell_total"],
                             var_name="type", value_name="MWh"),
                x="organisation", y="MWh", color="type", barmode="group",
                color_discrete_map={"buy_total": COLORS["secondary"], "sell_total": COLORS["accent"]},
                labels={"organisation": "Netbeheerder", "MWh": "Volume (MWh)"},
            )
            plotly_layout(fig_org, "Volume per netbeheerder", 400)
            st.plotly_chart(fig_org, use_container_width=True)

        # Spread tijdlijn
        if not perf_df.empty:
            st.subheader("Spread-ontwikkeling")
            fig_spread = go.Figure()
            fig_spread.add_trace(go.Scatter(
                x=perf_df["maand_label"], y=perf_df["spread_eur"],
                mode="lines+markers", name="Spread (EUR)",
                fill="tozeroy", fillcolor="rgba(46,134,193,0.1)",
                line=dict(color=COLORS["secondary"], width=2),
            ))
            plotly_layout(fig_spread, "Maandelijkse spread (EUR)", 350)
            st.plotly_chart(fig_spread, use_container_width=True)

    else:
        st.info("Geen transactiedata beschikbaar. Klik op 'Data vernieuwen' in de sidebar.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: KOSTENANALYSE / EXPENSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab3:
    st.header("Kostenanalyse — Expenses per Netbeheerder")

    exp_df = load_expenses()

    if not exp_df.empty:
        exp_df["maand_label"] = exp_df.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1
        )
        exp_df["total_volume"] = exp_df["sell_volume"].fillna(0) + exp_df["buy_volume"].fillna(0)

        # KPI's
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total_spread = exp_df["spread"].sum()
            st.metric("Totale spread", format_eur(total_spread))
        with col2:
            total_buy = exp_df["buy_volume"].sum()
            st.metric("Totaal buy volume", format_mwh(total_buy))
        with col3:
            total_sell = exp_df["sell_volume"].sum()
            st.metric("Totaal sell volume", format_mwh(total_sell))
        with col4:
            n_orgs = exp_df["organisation_name"].nunique()
            st.metric("Netbeheerders", n_orgs)

        st.divider()

        # Filter per netbeheerder
        all_orgs = sorted(exp_df["organisation_name"].unique())
        selected_orgs = st.multiselect(
            "Filter netbeheerders", all_orgs, default=all_orgs,
            key="exp_org_filter"
        )
        filtered = exp_df[exp_df["organisation_name"].isin(selected_orgs)]

        col_left, col_right = st.columns(2)

        with col_left:
            # Spread per netbeheerder (stacked bar)
            fig_spread = px.bar(
                filtered, x="maand_label", y="spread",
                color="organisation_name",
                color_discrete_map=NETBEHEERDER_COLORS,
                labels={"maand_label": "Maand", "spread": "Spread (EUR)", "organisation_name": "Netbeheerder"},
            )
            plotly_layout(fig_spread, "Maandelijkse spread per netbeheerder", 450)
            fig_spread.update_layout(barmode="stack")
            st.plotly_chart(fig_spread, use_container_width=True)

        with col_right:
            # Volume per netbeheerder (stacked area)
            monthly_vol = filtered.groupby(["maand_label", "organisation_name"]).agg(
                total=("total_volume", "sum")
            ).reset_index()

            fig_area = px.area(
                monthly_vol, x="maand_label", y="total",
                color="organisation_name",
                color_discrete_map=NETBEHEERDER_COLORS,
                labels={"maand_label": "Maand", "total": "Volume (MWh)", "organisation_name": "Netbeheerder"},
            )
            plotly_layout(fig_area, "Cumulatief volume per netbeheerder", 450)
            st.plotly_chart(fig_area, use_container_width=True)

        # Totaal per netbeheerder (pie + bar)
        col_pie, col_bar = st.columns(2)

        with col_pie:
            org_totals = filtered.groupby("organisation_name").agg(
                spread_total=("spread", "sum"),
            ).reset_index().sort_values("spread_total", ascending=False)

            fig_pie = px.pie(
                org_totals, values="spread_total", names="organisation_name",
                color="organisation_name", color_discrete_map=NETBEHEERDER_COLORS,
                hole=0.4,
            )
            plotly_layout(fig_pie, "Aandeel spread per netbeheerder", 400)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_bar:
            org_vol = filtered.groupby("organisation_name").agg(
                buy=("buy_volume", "sum"),
                sell=("sell_volume", "sum"),
            ).reset_index().sort_values("buy", ascending=False)

            fig_bs = px.bar(
                org_vol.melt(id_vars=["organisation_name"],
                             value_vars=["buy", "sell"],
                             var_name="Richting", value_name="MWh"),
                x="organisation_name", y="MWh", color="Richting",
                barmode="group",
                color_discrete_map={"buy": COLORS["secondary"], "sell": COLORS["accent"]},
                labels={"organisation_name": "Netbeheerder"},
            )
            plotly_layout(fig_bs, "Buy vs Sell volume per netbeheerder", 400)
            st.plotly_chart(fig_bs, use_container_width=True)

        # Detailtabel
        with st.expander("Ruwe data bekijken"):
            st.dataframe(
                filtered[["maand_label", "organisation_name", "buy_volume", "sell_volume", "spread"]].rename(
                    columns={
                        "maand_label": "Maand",
                        "organisation_name": "Netbeheerder",
                        "buy_volume": "Buy (MWh)",
                        "sell_volume": "Sell (MWh)",
                        "spread": "Spread (EUR)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    else:
        st.info("Geen kostendata beschikbaar. Klik op 'Data vernieuwen' in de sidebar.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: PERFORMANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab4:
    st.header("Performance — Congestiemanagement Effectiviteit")

    perf_df2 = load_performance()
    ann_df2 = load_announcements()

    if not perf_df2.empty:
        perf_df2["maand_label"] = perf_df2.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1
        )

        # Berekende metrics
        perf_df2["net_volume"] = perf_df2["buy_volume_mwh"] - perf_df2["sell_volume_mwh"]
        perf_df2["cost_efficiency"] = np.where(
            perf_df2["buy_volume_mwh"] + perf_df2["sell_volume_mwh"] > 0,
            perf_df2["spread_eur"] / (perf_df2["buy_volume_mwh"] + perf_df2["sell_volume_mwh"]),
            0
        )

        # KPI's
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            cumul_spread = perf_df2["spread_eur"].sum()
            st.metric("Cumulatieve spread", format_eur(cumul_spread))
        with col2:
            cumul_buy = perf_df2["buy_volume_mwh"].sum()
            st.metric("Cumulatief buy volume", format_mwh(cumul_buy))
        with col3:
            avg_eff = perf_df2["cost_efficiency"].mean()
            st.metric("Gem. kosten per MWh", f"\u20ac {avg_eff:.2f}" if pd.notna(avg_eff) else "—")
        with col4:
            if not ann_df2.empty:
                success_rate = (ann_df2["state"] == "CLEARED").mean() * 100 if "state" in ann_df2.columns else 0
                st.metric("Clearing rate", f"{success_rate:.1f}%")
            else:
                st.metric("Maanden actief", len(perf_df2))

        st.divider()

        # Jaarlijkse vergelijking
        col_year, col_eff = st.columns(2)

        with col_year:
            yearly = perf_df2.groupby("year").agg(
                spread=("spread_eur", "sum"),
                buy=("buy_volume_mwh", "sum"),
                sell=("sell_volume_mwh", "sum"),
            ).reset_index()

            fig_yearly = make_subplots(specs=[[{"secondary_y": True}]])
            fig_yearly.add_trace(
                go.Bar(x=yearly["year"], y=yearly["spread"], name="Spread (EUR)",
                       marker_color=COLORS["secondary"], opacity=0.7),
                secondary_y=False,
            )
            fig_yearly.add_trace(
                go.Scatter(x=yearly["year"], y=yearly["buy"], name="Buy volume (MWh)",
                           mode="lines+markers", line=dict(color=COLORS["accent"], width=2)),
                secondary_y=True,
            )
            fig_yearly.add_trace(
                go.Scatter(x=yearly["year"], y=yearly["sell"], name="Sell volume (MWh)",
                           mode="lines+markers", line=dict(color=COLORS["success"], width=2, dash="dash")),
                secondary_y=True,
            )
            plotly_layout(fig_yearly, "Jaarlijks overzicht", 420)
            fig_yearly.update_yaxes(title_text="Spread (EUR)", secondary_y=False)
            fig_yearly.update_yaxes(title_text="Volume (MWh)", secondary_y=True)
            st.plotly_chart(fig_yearly, use_container_width=True)

        with col_eff:
            # Cost efficiency trend
            fig_eff = go.Figure()
            fig_eff.add_trace(go.Scatter(
                x=perf_df2["maand_label"], y=perf_df2["cost_efficiency"],
                mode="lines+markers", name="Kosten/MWh (EUR)",
                fill="tozeroy", fillcolor="rgba(39,174,96,0.1)",
                line=dict(color=COLORS["success"], width=2),
            ))
            # Voortschrijdend gemiddelde
            if len(perf_df2) > 3:
                perf_df2["eff_ma3"] = perf_df2["cost_efficiency"].rolling(3, min_periods=1).mean()
                fig_eff.add_trace(go.Scatter(
                    x=perf_df2["maand_label"], y=perf_df2["eff_ma3"],
                    mode="lines", name="3-maands gem.",
                    line=dict(color=COLORS["danger"], width=2, dash="dot"),
                ))
            plotly_layout(fig_eff, "Kostenefficiency (EUR/MWh)", 420)
            st.plotly_chart(fig_eff, use_container_width=True)

        # Maandelijkse trend - volume en spread
        st.subheader("Maandelijkse trends")
        col_t1, col_t2 = st.columns(2)

        with col_t1:
            fig_mtrend = go.Figure()
            fig_mtrend.add_trace(go.Bar(
                x=perf_df2["maand_label"], y=perf_df2["buy_volume_mwh"],
                name="Buy (MWh)", marker_color=COLORS["secondary"],
            ))
            fig_mtrend.add_trace(go.Bar(
                x=perf_df2["maand_label"], y=perf_df2["sell_volume_mwh"],
                name="Sell (MWh)", marker_color=COLORS["accent"],
            ))
            plotly_layout(fig_mtrend, "Volume per maand", 350)
            fig_mtrend.update_layout(barmode="group")
            st.plotly_chart(fig_mtrend, use_container_width=True)

        with col_t2:
            fig_sprtrend = go.Figure()
            fig_sprtrend.add_trace(go.Scatter(
                x=perf_df2["maand_label"], y=perf_df2["spread_eur"].cumsum(),
                mode="lines+markers", name="Cumulatieve spread",
                fill="tozeroy", fillcolor="rgba(27,79,114,0.1)",
                line=dict(color=COLORS["primary"], width=2),
            ))
            plotly_layout(fig_sprtrend, "Cumulatieve spread (EUR)", 350)
            st.plotly_chart(fig_sprtrend, use_container_width=True)

        # Announcements analyse (als beschikbaar)
        if not ann_df2.empty and "state" in ann_df2.columns:
            st.subheader("Marktberichten analyse")
            col_s1, col_s2 = st.columns(2)

            with col_s1:
                state_counts = ann_df2["state"].value_counts().reset_index()
                state_counts.columns = ["Status", "Aantal"]
                fig_state = px.pie(
                    state_counts, values="Aantal", names="Status",
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                plotly_layout(fig_state, "Verdeling berichtstatus", 350)
                st.plotly_chart(fig_state, use_container_width=True)

            with col_s2:
                if "datum" in ann_df2.columns:
                    daily_count = ann_df2.groupby("datum").size().reset_index(name="n")
                    daily_count["datum_dt"] = pd.to_datetime(daily_count["datum"])
                    daily_count = daily_count.sort_values("datum_dt")

                    fig_daily = go.Figure()
                    fig_daily.add_trace(go.Scatter(
                        x=daily_count["datum_dt"], y=daily_count["n"],
                        mode="lines", name="Berichten/dag",
                        line=dict(color=COLORS["secondary"], width=1),
                    ))
                    if len(daily_count) > 7:
                        daily_count["ma7"] = daily_count["n"].rolling(7, min_periods=1).mean()
                        fig_daily.add_trace(go.Scatter(
                            x=daily_count["datum_dt"], y=daily_count["ma7"],
                            mode="lines", name="7-daags gem.",
                            line=dict(color=COLORS["danger"], width=2),
                        ))
                    plotly_layout(fig_daily, "Dagelijkse marktberichten", 350)
                    st.plotly_chart(fig_daily, use_container_width=True)

    else:
        st.info("Geen performance data beschikbaar. Klik op 'Data vernieuwen' in de sidebar.")


# ── Footer ───────────────────────────────────────────
st.divider()
st.caption("Flexkaart v2.0 — GOPACS Congestiemanagement Dashboard | Data: GOPACS API, Open-Meteo, PDOK/CBS")

"""
Flexkaart — Professioneel GOPACS Congestiemanagement Dashboard
Multi-source marktdata met 6 strategische tabs.
v3.0 — Voorspellingsmodule, consistente filters, Dark Mode, Download.
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
import io
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

COLORS = {
    "primary": "#1B4F72",
    "secondary": "#2E86C1",
    "accent": "#F39C12",
    "success": "#27AE60",
    "danger": "#E74C3C",
    "bg": "#F8F9FA",
    "grid": "#ECF0F1",
    "forecast": "#8E44AD",
    "ci_fill": "rgba(142,68,173,0.15)",
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

ALLE_PROVINCIES = [
    "Groningen","Friesland","Drenthe","Overijssel","Flevoland",
    "Gelderland","Utrecht","Noord-Holland","Zuid-Holland",
    "Zeeland","Noord-Brabant","Limburg",
]


# ── DB & fetch helpers ───────────────────────────────

def _ensure_db():
    from data_fetcher import init_db
    if not os.path.exists(DB_PATH):
        init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    except Exception:
        count = 0
    conn.close()
    return count == 0


def _do_fetch():
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


# ── Helpers ──────────────────────────────────────────

def format_eur(val):
    if pd.isna(val): return "—"
    return f"€ {val:,.0f}"

def format_mwh(val):
    if pd.isna(val): return "—"
    return f"{val:,.1f} MWh"

def plotly_layout(fig, title="", height=400):
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=COLORS["secondary"])),
        height=height,
        margin=dict(l=40, r=20, t=50, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28, xanchor="center", x=0.5,
                    font=dict(size=10)),
    )
    fig.update_xaxes(gridcolor="rgba(127,127,127,0.15)", showgrid=True)
    fig.update_yaxes(gridcolor="rgba(127,127,127,0.15)", showgrid=True)
    return fig

def parse_zip_codes(zip_json):
    if not zip_json or zip_json == "[]": return []
    try: return json.loads(zip_json)
    except Exception: return []

def zip_to_province(zip_code):
    from data_import import postcode_to_provincie
    return postcode_to_provincie(str(zip_code)) or "Onbekend"

def zip_to_coords(zip_code):
    from data_import import geocode_postcode
    return geocode_postcode(str(zip_code))

def certainty_color(pct):
    if pct >= 80: return COLORS["success"]
    elif pct >= 60: return COLORS["accent"]
    return COLORS["danger"]

def certainty_label(pct):
    if pct >= 80: return "Hoog"
    elif pct >= 60: return "Gemiddeld"
    return "Laag"

# ── Filter helpers ───────────────────────────────────

def filter_by_period(df, date_col, start_date, end_date):
    if df.empty or date_col not in df.columns: return df
    df = df.copy()
    df["_fdt"] = pd.to_datetime(df[date_col], errors="coerce")
    df = df[(df["_fdt"] >= pd.Timestamp(start_date)) & (df["_fdt"] <= pd.Timestamp(end_date))]
    return df.drop(columns=["_fdt"])

def filter_by_org(df, org_col, selected_orgs):
    if df.empty or not selected_orgs or org_col not in df.columns: return df
    return df[df[org_col].isin(selected_orgs)]

def get_date_range(*dfs):
    all_dates = []
    for df in dfs:
        for col in ["datum", "start_time"]:
            if col in df.columns and not df.empty:
                dates = pd.to_datetime(df[col], errors="coerce").dropna()
                if not dates.empty:
                    all_dates.extend([dates.min(), dates.max()])
    if not all_dates:
        end = datetime.today()
        return (end - timedelta(days=730)).date(), end.date()
    return min(all_dates).date(), max(all_dates).date()

def filter_widget(key, ann_df, exp_df, cb_df):
    """Rendert Periode / Netbeheerder / Provincie filters. Retourneert (start, end, orgs, provs)."""
    global_start, global_end = get_date_range(ann_df, exp_df, cb_df)
    col1, col2, col3, col4 = st.columns([2, 2, 3, 3])
    with col1:
        start = st.date_input("Periode van", value=global_start,
                              min_value=global_start, max_value=global_end, key=f"{key}_s")
    with col2:
        end = st.date_input("Periode tot", value=global_end,
                            min_value=global_start, max_value=global_end, key=f"{key}_e")
    all_orgs = set()
    for df, col in [(ann_df, "organisation"), (exp_df, "organisation_name"), (cb_df, "organisation")]:
        if not df.empty and col in df.columns:
            all_orgs.update(df[col].dropna().unique())
    with col3:
        sel_orgs = st.multiselect("Netbeheerder", sorted(all_orgs),
                                   default=sorted(all_orgs), key=f"{key}_org")
    avail_provs = []
    if not ann_df.empty and "zip_codes" in ann_df.columns:
        try:
            zips = ann_df["zip_codes"].dropna().apply(parse_zip_codes)
            first_zips = zips.apply(lambda z: z[0] if z else None).dropna()
            avail_provs = sorted(set(p for p in first_zips.apply(zip_to_province)
                                     if p and p != "Onbekend"))
        except Exception:
            pass
    if not avail_provs:
        avail_provs = ALLE_PROVINCIES
    with col4:
        sel_provs = st.multiselect("Provincie", avail_provs,
                                    default=avail_provs, key=f"{key}_prov")
    return start, end, sel_orgs, sel_provs


# ── CSS — Dark Mode compatible ───────────────────────

st.markdown("""
<style>
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { padding: 8px 20px; font-weight: 600; font-size: 13px; }

div[data-testid="stMetric"] {
    background: rgba(46,134,193,0.07);
    border-radius: 10px;
    padding: 12px 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    border-left: 3px solid #2E86C1;
}
div[data-testid="stMetric"] label { font-size: 11px !important; opacity: 0.72; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 700; }
div[data-testid="stMetric"] [data-testid="stMetricDelta"] { font-size: 11px !important; }

.fc-card {
    background: rgba(142,68,173,0.07);
    border-radius: 10px; padding: 14px 16px;
    border-left: 3px solid #8E44AD; margin-bottom: 8px;
}
.fc-title { font-size: 11px; opacity: 0.68; margin-bottom: 3px; }
.fc-value { font-size: 20px; font-weight: 700; }
.fc-ci   { font-size: 10px; opacity: 0.62; margin-top: 3px; }
.cert-badge {
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 11px; font-weight: 600; margin-top: 5px;
}
.info-xs { font-size: 11px; opacity: 0.65; line-height: 1.55; }
hr { margin: 0.6rem 0; }
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
        st.warning("Database leeg — klik 'Data vernieuwen'.")
        _do_fetch(); _also_import_excel()
        st.cache_data.clear(); st.rerun()
    log_df = load_fetch_log()
    if not log_df.empty:
        st.divider(); st.subheader("Laatste sync")
        for _, row in log_df.iterrows():
            st.text(f"{row['source']}: {row.get('records_fetched','?')} records")
            if row.get("last_fetch"):
                st.caption(row["last_fetch"][:16].replace("T"," "))
    st.divider(); st.caption("Flexkaart v3.0")

# ── Globale data ─────────────────────────────────────

ann_raw  = load_announcements()
cb_raw   = load_cleared_buckets()
exp_raw  = load_expenses()
perf_raw = load_performance()
weer_df  = load_weather()
mkb_df   = load_marktberichten()

# ── Tabs ─────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "⚡ Flexkaart (Geodata)",
    "📈 Marktberichten & Voorspellingen",
    "💶 Marktwaarde (Financials)",
    "💰 Kostenanalyse (Expenses)",
    "📊 Performance",
    "📥 Download",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: FLEXKAART / GEODATA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab1:
    st.header("Flexkaart — Congestiegebieden Nederland")
    with st.expander("🔍 Filters", expanded=True):
        s1, e1, orgs1, provs1 = filter_widget("t1", ann_raw, exp_raw, cb_raw)

    ann1 = filter_by_period(ann_raw, "datum", s1, e1)
    ann1 = filter_by_org(ann1, "organisation", orgs1)

    # Opbouw kaartdata
    map_data = []
    if not ann1.empty:
        for _, row in ann1.iterrows():
            zips = parse_zip_codes(row.get("zip_codes", "[]"))
            if zips:
                fz = zips[0]
                lat, lon = zip_to_coords(fz)
                prov = zip_to_province(fz)
                if lat and lon and (not provs1 or prov in provs1):
                    map_data.append({
                        "lat": lat, "lon": lon,
                        "organisation": row.get("organisation",""),
                        "state": row.get("state",""),
                        "type": row.get("type",""),
                        "gem_required_mw": row.get("gem_required_mw"),
                        "datum": row.get("datum"),
                        "problem_area": row.get("problem_area",""),
                        "provincie": prov,
                    })
        map_df = pd.DataFrame(map_data)
    elif not mkb_df.empty:
        map_df = mkb_df[mkb_df["lat"].notna() & mkb_df["lon"].notna()].copy()
        if "gem_vereist_mw" in map_df.columns:
            map_df.rename(columns={"gem_vereist_mw":"gem_required_mw"}, inplace=True)
        if "netbeheerder" in map_df.columns:
            map_df.rename(columns={"netbeheerder":"organisation"}, inplace=True)
    else:
        map_df = pd.DataFrame()

    if not map_df.empty:
        c1,c2,c3,c4 = st.columns(4)
        with c1: st.metric("Berichten (gefilterd)", f"{len(ann1):,}")
        with c2:
            n_geb = map_df["problem_area"].nunique() if "problem_area" in map_df.columns else 0
            st.metric("Congestiegebieden", n_geb)
        with c3:
            avg_mw = map_df["gem_required_mw"].mean() if "gem_required_mw" in map_df.columns else None
            st.metric("Gem. vereist MW", f"{avg_mw:.1f}" if avg_mw and pd.notna(avg_mw) else "—")
        with c4:
            st.metric("Provincies", map_df["provincie"].nunique() if "provincie" in map_df.columns else 0)
        st.divider()
        cm, cs = st.columns([2,1])
        with cm:
            if "gem_required_mw" in map_df.columns and map_df["gem_required_mw"].notna().any():
                map_df["_sz"] = map_df["gem_required_mw"].fillna(0).clip(lower=0.1)
                use_sz = "_sz"
            else:
                use_sz = None
            fig_map = px.scatter_map(
                map_df, lat="lat", lon="lon",
                size=use_sz,
                color="organisation" if "organisation" in map_df.columns else None,
                color_discrete_map=NETBEHEERDER_COLORS,
                hover_data=[c for c in ["datum","state","problem_area","provincie"] if c in map_df.columns],
                zoom=6.5, center={"lat":52.2,"lon":5.3}, height=520,
                title="Congestielocaties Nederland",
            )
            fig_map.update_layout(map_style="carto-positron",
                                   margin=dict(l=0,r=0,t=40,b=0),
                                   paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_map, use_container_width=True)
        with cs:
            st.subheader("Per provincie")
            if "provincie" in map_df.columns:
                pc = map_df["provincie"].value_counts().reset_index()
                pc.columns = ["Provincie","Aantal"]
                fig_prov = px.bar(pc, x="Aantal", y="Provincie", orientation="h",
                                   color_discrete_sequence=[COLORS["secondary"]])
                plotly_layout(fig_prov, height=280)
                st.plotly_chart(fig_prov, use_container_width=True)
            if not weer_df.empty and not ann1.empty and "datum" in ann1.columns:
                st.subheader("Weercorrelatie")
                apd = ann1.groupby("datum").size().reset_index(name="n")
                wm = weer_df.merge(apd, on="datum", how="inner")
                if not wm.empty and "temp_gem" in wm.columns:
                    corr = wm[["temp_gem","windsnelheid","neerslag","n"]].corr()
                    ca,cb2,cc = st.columns(3)
                    with ca: st.metric("🌡️ Temp", f"{corr.loc['temp_gem','n']:.2f}")
                    with cb2: st.metric("💨 Wind", f"{corr.loc['windsnelheid','n']:.2f}")
                    with cc: st.metric("🌧️ Regen", f"{corr.loc['neerslag','n']:.2f}")
    else:
        st.info("Geen geodata beschikbaar. Klik op 'Data vernieuwen' in de sidebar.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: MARKTBERICHTEN & VOORSPELLINGEN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab2:
    st.header("Marktberichten & Voorspellingen")
    st.caption(
        "Voorspellingsmodule op basis van historische GOPACS-data, netbeheerdertrends "
        "en Nederlandse weerpatronen. Scenario: as-is (huidige omstandigheden ongewijzigd)."
    )
    with st.expander("🔍 Filters", expanded=True):
        s2, e2, orgs2, provs2 = filter_widget("t2", ann_raw, exp_raw, cb_raw)

    ann2 = filter_by_period(ann_raw, "datum", s2, e2)
    ann2 = filter_by_org(ann2, "organisation", orgs2)

    # ── Historisch overzicht ──────────────────────────
    st.subheader("📋 Historisch overzicht")
    if not ann2.empty and "datum" in ann2.columns:
        ann2 = ann2.copy()
        ann2["datum_dt"] = pd.to_datetime(ann2["datum"], errors="coerce")
        ann2 = ann2.dropna(subset=["datum_dt"])
        ann2["maand_label"] = ann2["datum_dt"].dt.to_period("M").astype(str)

        h1,h2,h3,h4 = st.columns(4)
        with h1: st.metric("Berichten", f"{len(ann2):,}")
        with h2:
            if "state" in ann2.columns:
                cl = (ann2["state"]=="CLEARED").sum()
                st.metric("Cleared", f"{cl:,} ({cl/max(len(ann2),1)*100:.0f}%)")
        with h3:
            if "gem_required_mw" in ann2.columns:
                amw = ann2["gem_required_mw"].mean()
                st.metric("Gem. MW", f"{amw:.1f}" if pd.notna(amw) else "—")
        with h4:
            no = ann2["organisation"].nunique() if "organisation" in ann2.columns else 0
            st.metric("Netbeheerders", no)
        st.divider()

        # Dagelijks met 7-daags MA
        daily = ann2.groupby("datum_dt").size().reset_index(name="n").sort_values("datum_dt")
        daily["ma7"] = daily["n"].rolling(7, min_periods=1).mean()
        fig_h = go.Figure()
        fig_h.add_trace(go.Bar(x=daily["datum_dt"], y=daily["n"],
                                name="Berichten/dag", marker_color=COLORS["secondary"]+"88"))
        fig_h.add_trace(go.Scatter(x=daily["datum_dt"], y=daily["ma7"],
                                    mode="lines", name="7-daags gem.",
                                    line=dict(color=COLORS["accent"], width=2.5)))
        plotly_layout(fig_h, "Dagelijkse marktberichten (historisch)", 300)
        st.plotly_chart(fig_h, use_container_width=True)

        if "organisation" in ann2.columns:
            mo = ann2.groupby(["maand_label","organisation"]).size().reset_index(name="n")
            fig_mo = px.bar(mo, x="maand_label", y="n", color="organisation",
                             color_discrete_map=NETBEHEERDER_COLORS,
                             labels={"maand_label":"Maand","n":"Berichten","organisation":"Netbeheerder"})
            plotly_layout(fig_mo, "Maandelijks per netbeheerder", 300)
            fig_mo.update_layout(barmode="stack")
            st.plotly_chart(fig_mo, use_container_width=True)
    else:
        st.info("Geen historische berichten voor de geselecteerde filters.")

    st.divider()

    # ── Voorspellingsmodule ───────────────────────────
    st.subheader("🔮 Marktvoorspellingen")
    st.markdown(
        '<p class="info-xs">Voorspellingen via trendanalyse + seizoenscorrectie. '
        'Confidence interval: 90%. Zekerheid daalt naarmate de horizon verder ligt.</p>',
        unsafe_allow_html=True)

    horizon_map = {
        "Komende week (7 dagen)": "week",
        "Komende maand (30 dagen)": "maand",
        "Komend jaar (12 maanden)": "jaar",
    }
    h_label = st.radio("Tijdshorizon", list(horizon_map.keys()), index=1,
                        horizontal=True, key="fc_horizon")
    horizon = horizon_map[h_label]

    METRIC_META = {
        "spread_eur":       ("Spread",           "EUR"),
        "buy_volume_mwh":   ("Buy Volume",        "MWh"),
        "sell_volume_mwh":  ("Sell Volume",       "MWh"),
        "n_berichten":      ("Marktberichten",    "stuks"),
    }

    try:
        from prediction_engine import prepare_time_series, forecast_all_metrics, weather_correlation_summary
        with st.spinner("Voorspellingen berekenen..."):
            ts_df = prepare_time_series(ann2, perf_raw, weer_df)
            fc = forecast_all_metrics(ts_df, horizon=horizon)

        if fc:
            # Zekerheidsoverzicht
            st.markdown("**Zekerheidsoverzicht:**")
            cert_cols = st.columns(len(fc))
            for i, (cname, data) in enumerate(fc.items()):
                cert = data["certainty"]
                clr  = certainty_color(cert)
                lbl  = certainty_label(cert)
                m_lbl, m_unit = METRIC_META.get(cname, (data["name"], data["unit"]))
                fc_avg = data["forecast"].mean()
                fc_str = format_eur(fc_avg) if m_unit == "EUR" else f"{fc_avg:,.1f} {m_unit}"
                with cert_cols[i]:
                    st.markdown(
                        f'<div class="fc-card">'
                        f'<div class="fc-title">{m_lbl}</div>'
                        f'<div class="fc-value">{fc_str}</div>'
                        f'<div class="fc-ci">gem. over {h_label.split("(")[0].strip().lower()}</div>'
                        f'<span class="cert-badge" style="background:{clr}22;color:{clr};'
                        f'border:1px solid {clr}66;">{cert:.0f}% — {lbl}</span>'
                        f'</div>',
                        unsafe_allow_html=True)

            st.markdown("---")

            # Grafieken per metric
            show_metrics = [k for k in ["spread_eur","buy_volume_mwh","n_berichten"] if k in fc]
            for cname in show_metrics:
                data    = fc[cname]
                m_lbl, m_unit = METRIC_META.get(cname, (data["name"], data["unit"]))
                cert    = data["certainty"]
                clr     = certainty_color(cert)

                fig_fc = go.Figure()
                hist_d = data["historical_dates"]
                hist_v = data["historical"]
                # Toon max 24 maanden historisch
                if len(hist_v) > 24:
                    hist_v = hist_v.iloc[-24:]
                    hist_d = hist_d.iloc[-24:]

                fig_fc.add_trace(go.Scatter(
                    x=hist_d, y=hist_v.values,
                    mode="lines+markers", name="Historisch",
                    line=dict(color=COLORS["secondary"], width=2),
                    marker=dict(size=4)))

                fc_dates = list(data["dates"])
                fc_vals  = data["forecast"].values
                lo_vals  = data["lower"].values
                hi_vals  = data["upper"].values

                if len(hist_d) > 0:
                    ld = hist_d.iloc[-1]; lv = float(hist_v.iloc[-1])
                    fd_full = [ld] + fc_dates
                    fv_full = np.concatenate([[lv], fc_vals])
                    lo_full = np.concatenate([[lv], lo_vals])
                    hi_full = np.concatenate([[lv], hi_vals])
                else:
                    fd_full = fc_dates; fv_full = fc_vals
                    lo_full = lo_vals;  hi_full = hi_vals

                fig_fc.add_trace(go.Scatter(
                    x=fd_full, y=hi_full, mode="lines",
                    line=dict(color=COLORS["forecast"], width=0), showlegend=False))
                fig_fc.add_trace(go.Scatter(
                    x=fd_full, y=lo_full, mode="lines",
                    line=dict(color=COLORS["forecast"], width=0),
                    fill="tonexty", fillcolor=COLORS["ci_fill"],
                    name="90% betrouwbaarheidsband"))
                fig_fc.add_trace(go.Scatter(
                    x=fd_full, y=fv_full, mode="lines",
                    name=f"Voorspelling ({cert:.0f}% zekerheid)",
                    line=dict(color=COLORS["forecast"], width=2.5, dash="dash")))

                if len(hist_d) > 0:
                    fig_fc.add_vline(x=hist_d.iloc[-1],
                                      line_width=1.5, line_dash="dot",
                                      line_color="rgba(127,127,127,0.45)",
                                      annotation_text="Nu", annotation_font_size=10)

                plotly_layout(fig_fc, f"{m_lbl} — {h_label}", 360)
                fig_fc.update_yaxes(title_text=f"{m_lbl} ({m_unit})")
                st.plotly_chart(fig_fc, use_container_width=True)

            # Weercorrelatie heatmap
            st.subheader("🌦️ Weercorrelatie analyse")
            corr_df = weather_correlation_summary(ts_df)
            if corr_df is not None and not corr_df.empty:
                cca, ccb = st.columns([2,1])
                rename_idx = {"temp_gem":"Temperatuur (°C)","wind_gem":"Wind (m/s)",
                               "zon_gem":"Zon (uur)","neerslag_som":"Neerslag (mm)"}
                rename_col = {"n_berichten":"# Berichten","spread_eur":"Spread (EUR)",
                               "buy_volume_mwh":"Buy Vol (MWh)"}
                cd = corr_df.rename(index=rename_idx, columns=rename_col)
                with cca:
                    fig_corr = px.imshow(cd, color_continuous_scale="RdBu_r",
                                          zmin=-1, zmax=1, text_auto=".2f", aspect="auto")
                    fig_corr.update_layout(
                        height=260, margin=dict(l=10,r=10,t=40,b=10),
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(size=10),
                        title=dict(text="Correlatiematrix", font=dict(size=13, color=COLORS["secondary"])),
                        coloraxis_colorbar=dict(len=0.7, thickness=12))
                    st.plotly_chart(fig_corr, use_container_width=True)
                with ccb:
                    st.markdown('<p class="info-xs">'
                        "<b>+1.0</b> = sterke positieve correlatie<br>"
                        "<b>−1.0</b> = sterke negatieve correlatie<br>"
                        "<b>~0.0</b> = geen verband<br><br>"
                        "Koude winterperiodes gaan doorgaans samen met meer "
                        "congestieberichten en hogere spreads vanwege "
                        "toegenomen balanceringsbehoefte op het TenneT-net "
                        "en bij regionale netbeheerders.</p>",
                        unsafe_allow_html=True)
            else:
                st.info("Onvoldoende weerdata voor correlatie-analyse.")
        else:
            st.warning("Onvoldoende historische data voor voorspellingen. "
                       "Vernieuw de data via de sidebar.")
    except ImportError as ex:
        st.error(f"Voorspellingsmodule niet beschikbaar: {ex}")
    except Exception as ex:
        st.error(f"Fout bij voorspellen: {ex}")
        with st.expander("Technische details"): st.exception(ex)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: MARKTWAARDE / FINANCIALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab3:
    st.header("Marktwaarde — Gerealiseerde Transacties")
    with st.expander("🔍 Filters", expanded=True):
        s3, e3, orgs3, _ = filter_widget("t3", ann_raw, exp_raw, cb_raw)

    cb3  = filter_by_period(cb_raw, "datum", s3, e3)
    cb3  = filter_by_org(cb3, "organisation", orgs3)
    pf3  = perf_raw.copy() if not perf_raw.empty else pd.DataFrame()

    if not cb3.empty:
        cb3 = cb3.copy()
        cb3["total_volume_mwh"] = cb3["buy_volume_mwh"].fillna(0) + cb3["sell_volume_mwh"].fillna(0)
        cb3["datum_dt"] = pd.to_datetime(cb3["datum"], errors="coerce")
        cb3["maand_label"] = cb3["datum_dt"].dt.to_period("M").astype(str)

        c1,c2,c3,c4 = st.columns(4)
        with c1: st.metric("Transacties", f"{len(cb3):,}")
        with c2: st.metric("Buy volume", format_mwh(cb3["buy_volume_mwh"].sum()))
        with c3: st.metric("Sell volume", format_mwh(cb3["sell_volume_mwh"].sum()))
        with c4:
            if not pf3.empty and "spread_eur" in pf3.columns:
                st.metric("Totale spread", format_eur(pf3["spread_eur"].sum()))
            else:
                st.metric("Totale spread", "—")
        st.divider()

        cv, cp = st.columns(2)
        with cv:
            mon3 = cb3.groupby("maand_label").agg(
                buy=("buy_volume_mwh","sum"), sell=("sell_volume_mwh","sum")).reset_index()
            fig_v = go.Figure()
            fig_v.add_trace(go.Bar(x=mon3["maand_label"], y=mon3["buy"],
                                    name="Buy (MWh)", marker_color=COLORS["secondary"]))
            fig_v.add_trace(go.Bar(x=mon3["maand_label"], y=mon3["sell"],
                                    name="Sell (MWh)", marker_color=COLORS["accent"]))
            plotly_layout(fig_v, "Maandelijks transactievolume", 370)
            fig_v.update_layout(barmode="group")
            st.plotly_chart(fig_v, use_container_width=True)

        with cp:
            if not pf3.empty and "buy_price_eur" in pf3.columns:
                pf3 = pf3.copy()
                pf3["maand_label"] = pf3.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
                pf3["avg_buy"]  = np.where(pf3["buy_volume_mwh"]>0,
                                            pf3["buy_price_eur"]/pf3["buy_volume_mwh"], 0)
                pf3["avg_sell"] = np.where(pf3["sell_volume_mwh"]>0,
                                            pf3["sell_price_eur"]/pf3["sell_volume_mwh"], 0)
                fig_p = go.Figure()
                fig_p.add_trace(go.Scatter(x=pf3["maand_label"], y=pf3["avg_buy"],
                                            mode="lines+markers", name="Buy (EUR/MWh)",
                                            line=dict(color=COLORS["secondary"],width=2)))
                fig_p.add_trace(go.Scatter(x=pf3["maand_label"], y=pf3["avg_sell"],
                                            mode="lines+markers", name="Sell (EUR/MWh)",
                                            line=dict(color=COLORS["accent"],width=2)))
                plotly_layout(fig_p, "Gem. prijs per MWh", 370)
                st.plotly_chart(fig_p, use_container_width=True)

        st.subheader("Transacties per netbeheerder")
        if "organisation" in cb3.columns:
            ov3 = cb3.groupby("organisation").agg(
                buy_total=("buy_volume_mwh","sum"),
                sell_total=("sell_volume_mwh","sum")).reset_index()
            fig_org3 = px.bar(
                ov3.melt(id_vars=["organisation"],value_vars=["buy_total","sell_total"],
                          var_name="type", value_name="MWh"),
                x="organisation", y="MWh", color="type", barmode="group",
                color_discrete_map={"buy_total":COLORS["secondary"],"sell_total":COLORS["accent"]},
                labels={"organisation":"Netbeheerder"})
            plotly_layout(fig_org3, "Volume per netbeheerder", 360)
            st.plotly_chart(fig_org3, use_container_width=True)

        if not pf3.empty and "spread_eur" in pf3.columns:
            st.subheader("Spread-ontwikkeling")
            fig_spr = go.Figure()
            fig_spr.add_trace(go.Scatter(
                x=pf3["maand_label"], y=pf3["spread_eur"],
                mode="lines+markers", name="Spread (EUR)",
                fill="tozeroy", fillcolor="rgba(46,134,193,0.07)",
                line=dict(color=COLORS["secondary"],width=2)))
            plotly_layout(fig_spr, "Maandelijkse spread (EUR)", 300)
            st.plotly_chart(fig_spr, use_container_width=True)
    else:
        st.info("Geen transactiedata. Klik op 'Data vernieuwen'.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: KOSTENANALYSE / EXPENSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab4:
    st.header("Kostenanalyse — Expenses per Netbeheerder")
    with st.expander("🔍 Filters", expanded=True):
        s4, e4, orgs4, _ = filter_widget("t4", ann_raw, exp_raw, cb_raw)

    exp4 = exp_raw.copy() if not exp_raw.empty else pd.DataFrame()
    if not exp4.empty and "year" in exp4.columns:
        exp4["_fdt"] = pd.to_datetime(
            exp4.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}-01", axis=1),
            errors="coerce")
        exp4 = exp4[(exp4["_fdt"] >= pd.Timestamp(s4)) &
                     (exp4["_fdt"] <= pd.Timestamp(e4))].drop(columns=["_fdt"])
    exp4 = filter_by_org(exp4, "organisation_name", orgs4)

    if not exp4.empty:
        exp4 = exp4.copy()
        exp4["maand_label"] = exp4.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
        exp4["total_volume"] = exp4["sell_volume"].fillna(0) + exp4["buy_volume"].fillna(0)

        c1,c2,c3,c4 = st.columns(4)
        with c1: st.metric("Totale spread", format_eur(exp4["spread"].sum()))
        with c2: st.metric("Buy volume", format_mwh(exp4["buy_volume"].sum()))
        with c3: st.metric("Sell volume", format_mwh(exp4["sell_volume"].sum()))
        with c4: st.metric("Netbeheerders", exp4["organisation_name"].nunique())
        st.divider()

        cl, cr = st.columns(2)
        with cl:
            fig_spe = px.bar(exp4, x="maand_label", y="spread",
                              color="organisation_name", color_discrete_map=NETBEHEERDER_COLORS,
                              labels={"maand_label":"Maand","spread":"Spread (EUR)",
                                      "organisation_name":"Netbeheerder"})
            plotly_layout(fig_spe, "Maandelijkse spread per netbeheerder", 400)
            fig_spe.update_layout(barmode="stack")
            st.plotly_chart(fig_spe, use_container_width=True)
        with cr:
            mv4 = exp4.groupby(["maand_label","organisation_name"]).agg(
                total=("total_volume","sum")).reset_index()
            fig_ar = px.area(mv4, x="maand_label", y="total",
                              color="organisation_name", color_discrete_map=NETBEHEERDER_COLORS,
                              labels={"maand_label":"Maand","total":"Volume (MWh)",
                                      "organisation_name":"Netbeheerder"})
            plotly_layout(fig_ar, "Cumulatief volume per netbeheerder", 400)
            st.plotly_chart(fig_ar, use_container_width=True)

        cp4, cb4 = st.columns(2)
        with cp4:
            ot4 = exp4.groupby("organisation_name").agg(
                spread_total=("spread","sum")).reset_index()
            fig_pie = px.pie(ot4, values="spread_total", names="organisation_name",
                              color="organisation_name", color_discrete_map=NETBEHEERDER_COLORS,
                              hole=0.4)
            plotly_layout(fig_pie, "Aandeel spread per netbeheerder", 370)
            st.plotly_chart(fig_pie, use_container_width=True)
        with cb4:
            ov4 = exp4.groupby("organisation_name").agg(
                buy=("buy_volume","sum"), sell=("sell_volume","sum")).reset_index()
            fig_bs4 = px.bar(
                ov4.melt(id_vars=["organisation_name"], value_vars=["buy","sell"],
                          var_name="Richting", value_name="MWh"),
                x="organisation_name", y="MWh", color="Richting", barmode="group",
                color_discrete_map={"buy":COLORS["secondary"],"sell":COLORS["accent"]},
                labels={"organisation_name":"Netbeheerder"})
            plotly_layout(fig_bs4, "Buy vs Sell per netbeheerder", 370)
            st.plotly_chart(fig_bs4, use_container_width=True)

        with st.expander("Ruwe data"):
            st.dataframe(
                exp4[["maand_label","organisation_name","buy_volume","sell_volume","spread"]].rename(
                    columns={"maand_label":"Maand","organisation_name":"Netbeheerder",
                              "buy_volume":"Buy (MWh)","sell_volume":"Sell (MWh)","spread":"Spread (EUR)"}),
                use_container_width=True, hide_index=True)
    else:
        st.info("Geen kostendata. Klik op 'Data vernieuwen'.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5: PERFORMANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab5:
    st.header("Performance — Congestiemanagement Effectiviteit")
    with st.expander("🔍 Filters", expanded=True):
        s5, e5, orgs5, _ = filter_widget("t5", ann_raw, exp_raw, cb_raw)

    pf5 = perf_raw.copy() if not perf_raw.empty else pd.DataFrame()
    if not pf5.empty and "year" in pf5.columns:
        pf5["_fdt"] = pd.to_datetime(
            pf5.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}-01", axis=1), errors="coerce")
        pf5 = pf5[(pf5["_fdt"] >= pd.Timestamp(s5)) &
                   (pf5["_fdt"] <= pd.Timestamp(e5))].drop(columns=["_fdt"])

    ann5 = filter_by_period(ann_raw, "datum", s5, e5)
    ann5 = filter_by_org(ann5, "organisation", orgs5)

    if not pf5.empty:
        pf5 = pf5.copy()
        pf5["maand_label"] = pf5.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
        pf5["cost_efficiency"] = np.where(
            pf5["buy_volume_mwh"] + pf5["sell_volume_mwh"] > 0,
            pf5["spread_eur"] / (pf5["buy_volume_mwh"] + pf5["sell_volume_mwh"]), 0)

        c1,c2,c3,c4 = st.columns(4)
        with c1: st.metric("Cumulatieve spread", format_eur(pf5["spread_eur"].sum()))
        with c2: st.metric("Cumulatief buy", format_mwh(pf5["buy_volume_mwh"].sum()))
        with c3:
            ae = pf5["cost_efficiency"].mean()
            st.metric("Gem. kosten/MWh", f"€ {ae:.2f}" if pd.notna(ae) else "—")
        with c4:
            if not ann5.empty and "state" in ann5.columns:
                st.metric("Clearing rate", f"{(ann5['state']=='CLEARED').mean()*100:.1f}%")
            else:
                st.metric("Maanden actief", len(pf5))
        st.divider()

        cy, ce = st.columns(2)
        with cy:
            yr5 = pf5.groupby("year").agg(
                spread=("spread_eur","sum"), buy=("buy_volume_mwh","sum"),
                sell=("sell_volume_mwh","sum")).reset_index()
            fig_yr = make_subplots(specs=[[{"secondary_y":True}]])
            fig_yr.add_trace(go.Bar(x=yr5["year"], y=yr5["spread"],
                                     name="Spread (EUR)", marker_color=COLORS["secondary"],opacity=0.75),
                              secondary_y=False)
            fig_yr.add_trace(go.Scatter(x=yr5["year"], y=yr5["buy"],
                                         mode="lines+markers", name="Buy (MWh)",
                                         line=dict(color=COLORS["accent"],width=2)), secondary_y=True)
            fig_yr.add_trace(go.Scatter(x=yr5["year"], y=yr5["sell"],
                                         mode="lines+markers", name="Sell (MWh)",
                                         line=dict(color=COLORS["success"],width=2,dash="dash")),
                              secondary_y=True)
            plotly_layout(fig_yr, "Jaarlijks overzicht", 390)
            fig_yr.update_yaxes(title_text="Spread (EUR)", secondary_y=False)
            fig_yr.update_yaxes(title_text="Volume (MWh)", secondary_y=True)
            st.plotly_chart(fig_yr, use_container_width=True)
        with ce:
            fig_eff = go.Figure()
            fig_eff.add_trace(go.Scatter(
                x=pf5["maand_label"], y=pf5["cost_efficiency"],
                mode="lines+markers", name="Kosten/MWh",
                fill="tozeroy", fillcolor="rgba(39,174,96,0.07)",
                line=dict(color=COLORS["success"],width=2)))
            if len(pf5) > 3:
                pf5["eff_ma3"] = pf5["cost_efficiency"].rolling(3, min_periods=1).mean()
                fig_eff.add_trace(go.Scatter(
                    x=pf5["maand_label"], y=pf5["eff_ma3"],
                    mode="lines", name="3-maands gem.",
                    line=dict(color=COLORS["danger"],width=2,dash="dot")))
            plotly_layout(fig_eff, "Kostenefficiency (EUR/MWh)", 390)
            st.plotly_chart(fig_eff, use_container_width=True)

        st.subheader("Maandelijkse trends")
        ct1, ct2 = st.columns(2)
        with ct1:
            fig_mt = go.Figure()
            fig_mt.add_trace(go.Bar(x=pf5["maand_label"], y=pf5["buy_volume_mwh"],
                                     name="Buy (MWh)", marker_color=COLORS["secondary"]))
            fig_mt.add_trace(go.Bar(x=pf5["maand_label"], y=pf5["sell_volume_mwh"],
                                     name="Sell (MWh)", marker_color=COLORS["accent"]))
            plotly_layout(fig_mt, "Volume per maand", 300)
            fig_mt.update_layout(barmode="group")
            st.plotly_chart(fig_mt, use_container_width=True)
        with ct2:
            fig_cs = go.Figure()
            fig_cs.add_trace(go.Scatter(
                x=pf5["maand_label"], y=pf5["spread_eur"].cumsum(),
                mode="lines+markers", name="Cumulatieve spread",
                fill="tozeroy", fillcolor="rgba(27,79,114,0.07)",
                line=dict(color=COLORS["primary"],width=2)))
            plotly_layout(fig_cs, "Cumulatieve spread (EUR)", 300)
            st.plotly_chart(fig_cs, use_container_width=True)

        if not ann5.empty and "state" in ann5.columns:
            st.subheader("Marktberichten analyse")
            cs1, cs2 = st.columns(2)
            with cs1:
                sc5 = ann5["state"].value_counts().reset_index()
                sc5.columns = ["Status","Aantal"]
                fig_st = px.pie(sc5, values="Aantal", names="Status",
                                 hole=0.4, color_discrete_sequence=px.colors.qualitative.Set2)
                plotly_layout(fig_st, "Verdeling berichtstatus", 300)
                st.plotly_chart(fig_st, use_container_width=True)
            with cs2:
                if "datum" in ann5.columns:
                    dc5 = ann5.groupby("datum").size().reset_index(name="n")
                    dc5["datum_dt"] = pd.to_datetime(dc5["datum"])
                    dc5 = dc5.sort_values("datum_dt")
                    fig_dc = go.Figure()
                    fig_dc.add_trace(go.Scatter(x=dc5["datum_dt"], y=dc5["n"],
                                                 mode="lines", name="Berichten/dag",
                                                 line=dict(color=COLORS["secondary"],width=1)))
                    if len(dc5) > 7:
                        dc5["ma7"] = dc5["n"].rolling(7, min_periods=1).mean()
                        fig_dc.add_trace(go.Scatter(x=dc5["datum_dt"], y=dc5["ma7"],
                                                     mode="lines", name="7-daags gem.",
                                                     line=dict(color=COLORS["danger"],width=2)))
                    plotly_layout(fig_dc, "Dagelijkse marktberichten", 300)
                    st.plotly_chart(fig_dc, use_container_width=True)
    else:
        st.info("Geen performance data. Klik op 'Data vernieuwen'.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 6: DOWNLOAD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab6:
    st.header("📥 Data Export")
    st.caption("Exporteer gefilterde GOPACS-data naar Excel (.xlsx). Elke dataset op een apart werkblad.")

    with st.expander("🔍 Filters", expanded=True):
        s6, e6, orgs6, _ = filter_widget("t6", ann_raw, exp_raw, cb_raw)

    ann6 = filter_by_period(ann_raw, "datum", s6, e6)
    ann6 = filter_by_org(ann6, "organisation", orgs6)
    cb6  = filter_by_period(cb_raw, "datum", s6, e6)
    cb6  = filter_by_org(cb6, "organisation", orgs6)
    exp6 = exp_raw.copy() if not exp_raw.empty else pd.DataFrame()
    if not exp6.empty and "year" in exp6.columns:
        exp6["_fdt"] = pd.to_datetime(
            exp6.apply(lambda r: f"{int(r['year'])}-{int(r['month']):02d}-01", axis=1), errors="coerce")
        exp6 = exp6[(exp6["_fdt"] >= pd.Timestamp(s6)) &
                     (exp6["_fdt"] <= pd.Timestamp(e6))].drop(columns=["_fdt"])
    exp6 = filter_by_org(exp6, "organisation_name", orgs6)

    # Preview
    pv1, pv2 = st.columns(2)
    with pv1:
        st.subheader("Kostenanalyse preview")
        if not exp6.empty:
            st.metric("Rijen", len(exp6))
            st.dataframe(
                exp6[["year","month","organisation_name","buy_volume","sell_volume","spread"]].head(6),
                use_container_width=True, hide_index=True)
        else:
            st.info("Geen data voor geselecteerde filters.")
    with pv2:
        st.subheader("Transacties preview")
        if not cb6.empty:
            st.metric("Rijen", len(cb6))
            st.dataframe(
                cb6[["datum","organisation","buy_volume_mwh","sell_volume_mwh"]].head(6),
                use_container_width=True, hide_index=True)
        else:
            st.info("Geen data voor geselecteerde filters.")

    st.divider()
    st.markdown("**Selecteer werkbladen voor export:**")
    co1, co2 = st.columns(2)
    with co1:
        inc_ann  = st.checkbox("Marktberichten (announcements)", value=True)
        inc_exp  = st.checkbox("Kostenanalyse (expenses)", value=True)
    with co2:
        inc_cb   = st.checkbox("Transacties (cleared buckets)", value=True)
        inc_perf = st.checkbox("Performance metrics", value=True)
        inc_weer = st.checkbox("Weerdata (Open-Meteo)", value=False)

    st.divider()
    if st.button("📊 Excel-bestand genereren", type="primary"):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as wr:
            n_sheets = 0

            if inc_ann and not ann6.empty:
                cols = [c for c in ["datum","jaar","maand","organisation","state","type",
                                     "problem_area","gem_required_mw","max_required_mw",
                                     "zip_codes","message"] if c in ann6.columns]
                ann6[cols].rename(columns={
                    "datum":"Datum","jaar":"Jaar","maand":"Maand",
                    "organisation":"Netbeheerder","state":"Status","type":"Type",
                    "problem_area":"Congestiegebied","gem_required_mw":"Gem. MW",
                    "max_required_mw":"Max MW","zip_codes":"Postcodes","message":"Bericht"
                }).to_excel(wr, sheet_name="Marktberichten", index=False)
                n_sheets += 1

            if inc_exp and not exp6.empty:
                cols = [c for c in ["year","month","organisation_name",
                                     "buy_volume","sell_volume","spread"] if c in exp6.columns]
                exp6[cols].rename(columns={
                    "year":"Jaar","month":"Maand","organisation_name":"Netbeheerder",
                    "buy_volume":"Buy (MWh)","sell_volume":"Sell (MWh)","spread":"Spread (EUR)"
                }).to_excel(wr, sheet_name="Kostenanalyse", index=False)
                n_sheets += 1

            if inc_cb and not cb6.empty:
                cols = [c for c in ["datum","jaar","maand","organisation",
                                     "buy_volume_mwh","sell_volume_mwh"] if c in cb6.columns]
                cb6[cols].rename(columns={
                    "datum":"Datum","jaar":"Jaar","maand":"Maand",
                    "organisation":"Netbeheerder",
                    "buy_volume_mwh":"Buy (MWh)","sell_volume_mwh":"Sell (MWh)"
                }).to_excel(wr, sheet_name="Transacties", index=False)
                n_sheets += 1

            if inc_perf and not perf_raw.empty:
                perf_raw.rename(columns={
                    "year":"Jaar","month":"Maand","spread_eur":"Spread (EUR)",
                    "buy_volume_mwh":"Buy (MWh)","sell_volume_mwh":"Sell (MWh)",
                    "buy_price_eur":"Buy Prijs (EUR)","sell_price_eur":"Sell Prijs (EUR)"
                }).to_excel(wr, sheet_name="Performance", index=False)
                n_sheets += 1

            if inc_weer and not weer_df.empty:
                weer_df.rename(columns={
                    "datum":"Datum","temp_gem":"Gem. Temp (°C)","temp_max":"Max Temp (°C)",
                    "temp_min":"Min Temp (°C)","windsnelheid":"Wind (m/s)",
                    "zonneschijnduur":"Zon (uur)","neerslag":"Neerslag (mm)"
                }).to_excel(wr, sheet_name="Weerdata", index=False)
                n_sheets += 1

            if n_sheets == 0:
                pd.DataFrame({"Info":["Geen data geselecteerd."]}).to_excel(
                    wr, sheet_name="Info", index=False)

        buf.seek(0)
        fname = f"flexkaart_{s6}_{e6}.xlsx"
        st.download_button(
            label="⬇️ Download Excel-bestand",
            data=buf,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.success(f"✅ Excel klaar — {n_sheets} werkblad(en) gegenereerd: `{fname}`")

# ── Footer ────────────────────────────────────────────
st.divider()
st.caption(
    f"Flexkaart v3.0 — GOPACS Congestiemanagement Dashboard | "
    f"Data: GOPACS API · Open-Meteo · PDOK/CBS | "
    f"{datetime.now().strftime('%d-%m-%Y %H:%M')}"
)

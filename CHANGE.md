# CHANGE.md — Flexkaart Wijzigingslog

---

## v3.0 — 2026-04-13

### Nieuwe bestanden
- **`prediction_engine.py`** — Zelfstandige voorspellingsmodule met:
  - `prepare_time_series()`: combineert announcements, performance en weerdata tot één geïntegreerde tijdreeks per maand
  - `simple_trend_forecast()`: lineaire trendvoorspelling + seizoenscorrectie + 90% betrouwbaarheidsintervallen
  - `forecast_all_metrics()`: genereert voorspellingen voor spread, buy volume, sell volume en marktberichten over drie tijdshorizonnen (week / maand / jaar)
  - `weather_correlation_summary()`: berekent correlatiematrix weerdata vs. congestiemetrics
  - Seizoensindices afgestemd op Nederlands congestiepatroon (winter hoog, zomer laag)
  - Weeraanpassingsfactor op basis van temperatuur, wind en zonneschijn

### Gewijzigde bestanden

#### `app.py` (volledig herschreven, v2.0 → v3.0)

**Structuur: 4 tabs → 6 tabs**
| # | Tab | Status |
|---|-----|--------|
| 1 | ⚡ Flexkaart (Geodata) | Bestaand, bijgewerkt |
| 2 | 📈 Marktberichten & Voorspellingen | **NIEUW** |
| 3 | 💶 Marktwaarde (Financials) | Bestaand, bijgewerkt |
| 4 | 💰 Kostenanalyse (Expenses) | Bestaand, bijgewerkt |
| 5 | 📊 Performance | Bestaand, bijgewerkt |
| 6 | 📥 Download | **NIEUW** |

**Tab 2 — Marktberichten & Voorspellingen (nieuw)**
- Historisch overzicht: dagelijks berichten + 7-daags voortschrijdend gemiddelde
- Maandelijkse stapelbalk per netbeheerder
- Voorspellingsmodule met drie tijdshorizonnen (radio selector)
- Zekerheidsoverzicht: per metric een kaart met gemiddelde voorspelling + zekerheidspercentage (kleurcodering groen/oranje/rood)
- Plotly-grafieken: historische lijn + voorspellingslijn (gestippeld) + 90% CI-band (gearceerd)
- Verticale scheidingslijn "Nu" op overgang historisch→voorspelling
- Weercorrelatie heatmap (RdBu_r kleurenschaal) met interpretatie-uitleg

**Tab 6 — Download (nieuw)**
- Periode- en netbeheerderfilters (consistent met overige tabs)
- Preview van kostenanalyse en transactiedata (eerste 6 rijen)
- Checkboxen voor werkbladkeuze: Marktberichten, Kostenanalyse, Transacties, Performance, Weerdata
- `st.download_button` genereert in-memory Excel met `pd.ExcelWriter` + `openpyxl`
- Bestandsnaam bevat automatisch de geselecteerde datumrange

**Consistente filters op alle tabs**
- Elke tab heeft een eigen `st.expander("🔍 Filters")` met:
  - Periode van/tot (`st.date_input`, automatische range uit beschikbare data)
  - Netbeheerder multiselect (alle bekende operators, default=alles)
  - Provincie multiselect (afgeleid uit postcodes, default=alles)
- Helper-functies: `filter_widget()`, `filter_by_period()`, `filter_by_org()`, `get_date_range()`

**Dark Mode — UI verbeteringen**
- Alle Plotly-grafieken: `plot_bgcolor="rgba(0,0,0,0)"` en `paper_bgcolor="rgba(0,0,0,0)"` (transparant)
- Gridlijnen: `rgba(127,127,127,0.15)` (thema-neutraal)
- `div[data-testid="stMetric"]`: achtergrond `rgba(46,134,193,0.07)` i.p.v. hardcoded wit
- Font metric-value: 22px (was 28px), label: 11px (was 13px)
- Forecast-kaarten: `rgba(142,68,173,0.07)` achtergrond met paarse linker border
- CSS-klassen: `.fc-card`, `.cert-badge`, `.info-xs` — volledig thema-neutraal

**Overige verbeteringen**
- `plotly_layout()`: legend `y=-0.28` (meer ruimte), `font.size=11`
- Globale data éénmalig geladen bovenin (`ann_raw`, `cb_raw`, `exp_raw`, `perf_raw`, `weer_df`, `mkb_df`)
- Sidebar: compacter, versienummer toegevoegd

---

## v2.0 — 2026-04-11

- Initiële opzet met 4 tabs: Flexkaart, Marktwaarde, Kostenanalyse, Performance
- Multi-source data: GOPACS API, Open-Meteo, Excel fallback
- SQLite-caching met 1-uur TTL
- Plotly-kaart met congestielocaties Nederland
- Netbeheerder-kleurcodering (8 operators)
- Kostenanalyse met netbeheerder-filter (multiselect)

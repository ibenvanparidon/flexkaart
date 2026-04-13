"""
prediction_engine.py — Voorspellingsmodule voor Flexkaart
Genereert marktvoorspellingen op basis van historische data en weerpatronen.
Ondersteunt drie tijdshorizonnen: week, maand, jaar.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional


def _seasonal_index(month: int) -> float:
    """
    Seizoensindices voor congestiemanagement in Nederland.
    Winter/herfst = meer congestie (hogere warmtevraag + meer wind).
    Zomer = minder congestie (lager verbruik).
    """
    indices = {
        1: 1.25, 2: 1.20, 3: 1.10,
        4: 0.95, 5: 0.85, 6: 0.75,
        7: 0.70, 8: 0.78, 9: 0.90,
        10: 1.05, 11: 1.18, 12: 1.30,
    }
    return indices.get(month, 1.0)


def _weather_adjustment(temp: float, wind: float, sun: float) -> float:
    """
    Berekent een weeraanpassingsfactor voor de voorspelling.
    Koud + hard wind + weinig zon = meer congestie (factor > 1).
    """
    factor = 1.0
    # Temperatuureffect: elke graad onder 10°C verhoogt congestie
    if temp < 10:
        factor += (10 - temp) * 0.018
    elif temp > 20:
        factor -= (temp - 20) * 0.012

    # Windeffect: veel wind kan zowel positief (aanbod) als negatief zijn
    if wind > 8:
        factor += (wind - 8) * 0.008

    # Zonneschijn: veel zon = meer zonnepanelen = minder netdruk
    if sun > 6:
        factor -= (sun - 6) * 0.015

    return max(0.5, min(2.0, factor))


def prepare_time_series(
    ann_df: pd.DataFrame,
    perf_df: pd.DataFrame,
    weather_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Combineert announcements, performance en weerdata tot een
    geïntegreerde tijdreeks voor voorspelling.
    """
    frames = []

    # ── Announcements aggregeren per maand ────────────
    if not ann_df.empty and "datum" in ann_df.columns:
        ann_df = ann_df.copy()
        ann_df["datum_dt"] = pd.to_datetime(ann_df["datum"], errors="coerce")
        ann_df = ann_df.dropna(subset=["datum_dt"])
        ann_df["jaar"] = ann_df["datum_dt"].dt.year
        ann_df["maand"] = ann_df["datum_dt"].dt.month

        monthly_ann = ann_df.groupby(["jaar", "maand"]).agg(
            n_berichten=("id", "count"),
            gem_required_mw=("gem_required_mw", "mean"),
        ).reset_index()
        frames.append(monthly_ann)

    # ── Performance data ───────────────────────────────
    if not perf_df.empty:
        perf = perf_df[["year", "month", "spread_eur", "buy_volume_mwh", "sell_volume_mwh"]].copy()
        perf.rename(columns={"year": "jaar", "month": "maand"}, inplace=True)
        frames.append(perf)

    if not frames:
        return pd.DataFrame()

    # Samenvoegen op jaar/maand
    combined = frames[0]
    for df in frames[1:]:
        combined = combined.merge(df, on=["jaar", "maand"], how="outer")

    combined["datum_dt"] = pd.to_datetime(
        combined.apply(lambda r: f"{int(r['jaar'])}-{int(r['maand']):02d}-01", axis=1),
        errors="coerce",
    )
    combined = combined.dropna(subset=["datum_dt"]).sort_values("datum_dt").reset_index(drop=True)

    # ── Weerdata aggregeren per maand ─────────────────
    if not weather_df.empty and "datum" in weather_df.columns:
        wdf = weather_df.copy()
        wdf["datum_dt"] = pd.to_datetime(wdf["datum"], errors="coerce")
        wdf = wdf.dropna(subset=["datum_dt"])
        wdf["jaar"] = wdf["datum_dt"].dt.year
        wdf["maand"] = wdf["datum_dt"].dt.month

        weer_maand = wdf.groupby(["jaar", "maand"]).agg(
            temp_gem=("temp_gem", "mean"),
            wind_gem=("windsnelheid", "mean"),
            zon_gem=("zonneschijnduur", "mean"),
            neerslag_som=("neerslag", "sum"),
        ).reset_index()

        combined = combined.merge(weer_maand, on=["jaar", "maand"], how="left")

    # Seizoensindex toevoegen
    combined["seizoensindex"] = combined["maand"].apply(_seasonal_index)

    # Ontbrekende waarden vullen
    for col in ["spread_eur", "buy_volume_mwh", "sell_volume_mwh", "n_berichten", "gem_required_mw"]:
        if col in combined.columns:
            combined[col] = combined[col].fillna(combined[col].median())

    return combined


def simple_trend_forecast(
    series: pd.Series,
    n_periods: int,
    confidence_level: float = 0.90,
) -> Tuple[pd.Series, pd.Series, pd.Series, float]:
    """
    Enkelvoudige voorspelling via lineaire trend + seizoenspatroon.
    Retourneert: (forecast, lower_bound, upper_bound, certainty_pct)
    """
    if len(series) < 3:
        last_val = series.iloc[-1] if len(series) > 0 else 0.0
        forecast = pd.Series([last_val] * n_periods)
        lb = forecast * 0.8
        ub = forecast * 1.2
        return forecast, lb, ub, 50.0

    n = len(series)
    x = np.arange(n)
    y = series.values.astype(float)

    # Lineaire regressie voor de trend
    coeffs = np.polyfit(x, y, 1)
    trend_slope = coeffs[0]
    trend_intercept = coeffs[1]

    # Residuen voor betrouwbaarheidsinterval
    trend_line = trend_slope * x + trend_intercept
    residuals = y - trend_line
    std_resid = np.std(residuals)

    # z-score voor confidence level
    z = {0.80: 1.282, 0.85: 1.440, 0.90: 1.645, 0.95: 1.960}.get(confidence_level, 1.645)

    # Voorspelling genereren
    future_x = np.arange(n, n + n_periods)
    forecast_vals = trend_slope * future_x + trend_intercept

    # Seizoenscorrectie gebaseerd op historisch patroon
    # Bereken gemiddeld seizoenspatroon
    if len(series) >= 12:
        seasonal = np.array([
            series.iloc[i::12].mean() / max(series.mean(), 1e-9)
            for i in range(12)
        ])
    else:
        seasonal = np.ones(12)

    # Pas seizoenscorrectie toe
    last_month_idx = (n - 1) % 12
    corrected = []
    for i, val in enumerate(forecast_vals):
        month_idx = (last_month_idx + i + 1) % 12
        s_factor = seasonal[month_idx]
        corrected.append(max(0, val * s_factor))

    forecast = pd.Series(corrected)

    # Confidence interval (groeit met tijdshorizon)
    horizon_factor = np.sqrt(np.arange(1, n_periods + 1))
    margin = z * std_resid * horizon_factor

    lower = (forecast - margin).clip(lower=0)
    upper = forecast + margin

    # Zekerheidspercentage: smallere band = hoger percentage
    avg_margin_pct = (margin.mean() / max(forecast.mean(), 1e-9)) * 100
    certainty_pct = max(10.0, min(99.0, 100 - avg_margin_pct * 0.8))

    return forecast, lower, upper, round(certainty_pct, 1)


def forecast_all_metrics(
    ts_df: pd.DataFrame,
    horizon: str = "maand",
) -> Dict:
    """
    Genereert voorspellingen voor alle beschikbare metrics.

    Args:
        ts_df: Gecombineerde tijdreeks (uitkomst van prepare_time_series)
        horizon: 'week' | 'maand' | 'jaar'

    Returns:
        Dict met per metric: {'forecast', 'lower', 'upper', 'certainty', 'dates'}
    """
    horizon_map = {"week": 1, "maand": 1, "jaar": 12}
    n_months = horizon_map.get(horizon, 1)

    # Voor week-horizon: we interpoleren per dag
    if horizon == "week":
        n_periods = 7
        freq = "D"
    elif horizon == "maand":
        n_periods = 30
        freq = "D"
    else:  # jaar
        n_periods = 12
        freq = "ME"

    results = {}

    if ts_df.empty:
        return results

    # Metriek-configuratie: (kolom, display naam, eenheid)
    metrics_config = [
        ("spread_eur", "Spread", "EUR"),
        ("buy_volume_mwh", "Buy Volume", "MWh"),
        ("sell_volume_mwh", "Sell Volume", "MWh"),
        ("n_berichten", "Marktberichten", "stuks"),
    ]

    last_date = ts_df["datum_dt"].max()

    for col, name, unit in metrics_config:
        if col not in ts_df.columns:
            continue

        series = ts_df[col].dropna()
        if len(series) < 2:
            continue

        if horizon == "jaar":
            # Maandelijkse voorspelling
            fc, lo, hi, certainty = simple_trend_forecast(series, n_months)
            future_dates = pd.date_range(
                start=last_date + pd.DateOffset(months=1),
                periods=n_months,
                freq="MS",
            )
        else:
            # Dagelijkse interpolatie vanuit maandelijkse trend
            fc_monthly, lo_monthly, hi_monthly, certainty = simple_trend_forecast(series, 2)

            # Dagelijks uitrekken
            last_val = series.iloc[-1]
            next_month_fc = fc_monthly.iloc[0]
            next_next_fc = fc_monthly.iloc[1] if len(fc_monthly) > 1 else next_month_fc

            daily_trend = (next_month_fc - last_val) / 30
            daily_fc = [last_val + daily_trend * i for i in range(1, n_periods + 1)]

            margin_pct = (hi_monthly.iloc[0] - lo_monthly.iloc[0]) / max(fc_monthly.iloc[0], 1e-9) / 2
            daily_margin = [abs(v) * margin_pct * np.sqrt(i / n_periods) for i, v in enumerate(daily_fc, 1)]

            fc = pd.Series([max(0, v) for v in daily_fc])
            lo = pd.Series([max(0, v - m) for v, m in zip(daily_fc, daily_margin)])
            hi = pd.Series([max(0, v + m) for v, m in zip(daily_fc, daily_margin)])
            future_dates = pd.date_range(
                start=last_date + timedelta(days=1),
                periods=n_periods,
                freq="D",
            )

        results[col] = {
            "name": name,
            "unit": unit,
            "forecast": fc,
            "lower": lo,
            "upper": hi,
            "certainty": certainty,
            "dates": future_dates,
            "historical": series,
            "historical_dates": ts_df["datum_dt"].iloc[-len(series):],
        }

    return results


def weather_correlation_summary(ts_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Berekent correlatie tussen weerfactoren en congestiemetrics.
    """
    if ts_df.empty:
        return None

    weer_cols = ["temp_gem", "wind_gem", "zon_gem", "neerslag_som"]
    target_cols = ["n_berichten", "spread_eur", "buy_volume_mwh"]

    available_weer = [c for c in weer_cols if c in ts_df.columns and ts_df[c].notna().sum() > 3]
    available_targets = [c for c in target_cols if c in ts_df.columns and ts_df[c].notna().sum() > 3]

    if not available_weer or not available_targets:
        return None

    corr_df = ts_df[available_weer + available_targets].corr()
    return corr_df.loc[available_weer, available_targets]

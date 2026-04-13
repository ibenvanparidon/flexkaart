"""
prediction_engine.py — Verbeterde Voorspellingsmodule voor Flexkaart v3.1
Genereert marktvoorspellingen op basis van historische data en weerpatronen.
Ondersteunt drie tijdshorizonnen: week, maand, jaar.

Verbeteringen t.o.v. v3.0:
- Zekerheid gebaseerd op R2 + CV + datahoeveelheid + horizonpenalty
- EWMA (Exponential Weighted Moving Average) voor recente data zwaarder wegen
- Horizon-specifieke zekerheidsscore: week > maand > jaar
- Betere seizoensdecompositie (genormaliseerd)
- Gecombineerde trend+EWMA blend (blend-ratio per horizon)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional


def _seasonal_index(month: int) -> float:
    indices = {
        1: 1.25, 2: 1.20, 3: 1.10,
        4: 0.95, 5: 0.85, 6: 0.75,
        7: 0.70, 8: 0.78, 9: 0.90,
        10: 1.05, 11: 1.18, 12: 1.30,
    }
    return indices.get(month, 1.0)


def _weather_adjustment(temp: float, wind: float, sun: float) -> float:
    factor = 1.0
    if temp < 10:
        factor += (10 - temp) * 0.018
    elif temp > 20:
        factor -= (temp - 20) * 0.012
    if wind > 8:
        factor += (wind - 8) * 0.008
    if sun > 6:
        factor -= (sun - 6) * 0.015
    return max(0.5, min(2.0, factor))


def _compute_certainty(y: np.ndarray, residuals: np.ndarray, horizon: str) -> float:
    """
    Zekerheidspercentage op basis van vier factoren:
      1. R2   - hoe goed past de trend op historische data
      2. CV   - relatieve ruis (coeff. of variation van residuen)
      3. Data - meer punten => betrouwbaarder (bonus t/m 24 mnd)
      4. Horizon - week=0pt penalty, maand=8pt, jaar=18pt
    Resultaat: 15-95% (nooit vast op 10% door formule-overflow)
    """
    n = len(y)
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = max(0.0, 1.0 - ss_res / max(ss_tot, 1e-9))
    cv = float(np.std(residuals)) / max(abs(float(np.mean(y))), 1e-9)
    cv_penalty = min(40.0, cv * 80.0)
    data_bonus = min(15.0, (n / 24.0) * 15.0)
    h_penalties = {"week": 0.0, "maand": 8.0, "jaar": 18.0}
    h_penalty = h_penalties.get(horizon, 8.0)
    base = 20.0 + r2 * 65.0
    certainty = base + data_bonus - cv_penalty - h_penalty
    return round(max(15.0, min(95.0, certainty)), 1)


def _seasonal_factors(series: pd.Series, n: int) -> np.ndarray:
    if n >= 12:
        raw = np.array([
            float(series.iloc[i::12].mean()) / max(float(series.mean()), 1e-9)
            for i in range(12)
        ])
        mean_raw = float(raw.mean())
        return raw / max(mean_raw, 1e-9)
    else:
        return np.array([_seasonal_index(m) for m in range(1, 13)])


def simple_trend_forecast(
    series: pd.Series,
    n_periods: int,
    confidence_level: float = 0.90,
    horizon: str = "maand",
) -> Tuple[pd.Series, pd.Series, pd.Series, float]:
    """
    Verbeterde voorspelling via lineaire trend + EWMA blend.

    Methode:
    - Lineaire regressie voor lange-termijn richting
    - EWMA (alpha=0.25) geeft meer gewicht aan recente data
    - Blend-ratio per horizon: week=70% EWMA, maand=50%, jaar=20%
    - Seizoenscorrectie op basis van historische maandgemiddelden
    - CI groeit met sqrt(t); zekerheid via R2/CV/data/horizon
    """
    if len(series) < 2:
        last_val = float(series.iloc[-1]) if len(series) > 0 else 0.0
        fc = pd.Series([last_val] * n_periods)
        return fc, fc * 0.8, fc * 1.2, 30.0

    n = len(series)
    x = np.arange(n, dtype=float)
    y = series.values.astype(float)

    coeffs = np.polyfit(x, y, 1)
    slope, intercept = float(coeffs[0]), float(coeffs[1])
    trend_line = slope * x + intercept
    residuals = y - trend_line
    std_resid = float(np.std(residuals))

    ewma_last = float(series.ewm(alpha=0.25, adjust=False).mean().iloc[-1])
    trend_last = float(slope * (n - 1) + intercept)

    blend_ewma = {"week": 0.70, "maand": 0.50, "jaar": 0.20}.get(horizon, 0.50)
    blended_start = blend_ewma * ewma_last + (1.0 - blend_ewma) * trend_last
    correction = blended_start - trend_last

    seasonal = _seasonal_factors(series, n)

    future_x = np.arange(n, n + n_periods, dtype=float)
    raw_forecast = slope * future_x + intercept + correction

    last_month_idx = (n - 1) % 12
    corrected = []
    for i, val in enumerate(raw_forecast):
        month_idx = (last_month_idx + i + 1) % 12
        corrected.append(max(0.0, val * float(seasonal[month_idx])))

    forecast = pd.Series(corrected)

    z = {0.80: 1.282, 0.85: 1.440, 0.90: 1.645, 0.95: 1.960}.get(confidence_level, 1.645)
    horizon_factor = np.sqrt(np.arange(1, n_periods + 1, dtype=float))
    margin = z * std_resid * horizon_factor

    lower = (forecast - margin).clip(lower=0)
    upper = forecast + margin

    certainty = _compute_certainty(y, residuals, horizon)
    return forecast, lower, upper, certainty


def prepare_time_series(
    ann_df: pd.DataFrame,
    perf_df: pd.DataFrame,
    weather_df: pd.DataFrame,
) -> pd.DataFrame:
    frames = []

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

    if not perf_df.empty:
        perf = perf_df[["year", "month", "spread_eur", "buy_volume_mwh", "sell_volume_mwh"]].copy()
        perf.rename(columns={"year": "jaar", "month": "maand"}, inplace=True)
        frames.append(perf)

    if not frames:
        return pd.DataFrame()

    combined = frames[0]
    for df in frames[1:]:
        combined = combined.merge(df, on=["jaar", "maand"], how="outer")

    combined["datum_dt"] = pd.to_datetime(
        combined.apply(lambda r: f"{int(r['jaar'])}-{int(r['maand']):02d}-01", axis=1),
        errors="coerce",
    )
    combined = combined.dropna(subset=["datum_dt"]).sort_values("datum_dt").reset_index(drop=True)

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

    combined["seizoensindex"] = combined["maand"].apply(_seasonal_index)

    for col in ["spread_eur", "buy_volume_mwh", "sell_volume_mwh", "n_berichten", "gem_required_mw"]:
        if col in combined.columns:
            combined[col] = combined[col].fillna(combined[col].median())

    return combined


def forecast_all_metrics(ts_df: pd.DataFrame, horizon: str = "maand") -> Dict:
    """
    Genereert verbeterde voorspellingen voor alle metrics.
    horizon: 'week' | 'maand' | 'jaar'
    """
    if horizon == "week":
        n_periods = 7
    elif horizon == "maand":
        n_periods = 30
    else:
        n_periods = 12

    results = {}
    if ts_df.empty:
        return results

    metrics_config = [
        ("spread_eur",      "Spread",         "EUR"),
        ("buy_volume_mwh",  "Buy Volume",      "MWh"),
        ("sell_volume_mwh", "Sell Volume",     "MWh"),
        ("n_berichten",     "Marktberichten",  "stuks"),
    ]

    last_date = ts_df["datum_dt"].max()

    for col, name, unit in metrics_config:
        if col not in ts_df.columns:
            continue
        series = ts_df[col].dropna()
        if len(series) < 2:
            continue

        if horizon == "jaar":
            fc, lo, hi, certainty = simple_trend_forecast(series, n_periods, horizon=horizon)
            future_dates = pd.date_range(
                start=last_date + pd.DateOffset(months=1),
                periods=n_periods,
                freq="MS",
            )
        else:
            fc_monthly, lo_monthly, hi_monthly, certainty = simple_trend_forecast(series, 2, horizon=horizon)
            last_val = float(series.iloc[-1])
            next_month_fc = float(fc_monthly.iloc[0])
            daily_trend = (next_month_fc - last_val) / 30.0
            week_pattern = np.array([1.02, 1.01, 1.00, 0.99, 1.01, 0.97, 0.98])

            daily_fc = []
            for i in range(n_periods):
                base_val = last_val + daily_trend * (i + 1)
                daily_fc.append(max(0.0, base_val * float(week_pattern[i % 7])))

            hi0 = float(hi_monthly.iloc[0])
            lo0 = float(lo_monthly.iloc[0])
            margin_ratio = (hi0 - lo0) / (2.0 * max(abs(next_month_fc), 1e-9))
            daily_margin = [
                abs(v) * margin_ratio * np.sqrt((i + 1) / n_periods)
                for i, v in enumerate(daily_fc)
            ]

            fc = pd.Series([max(0.0, v) for v in daily_fc])
            lo = pd.Series([max(0.0, v - m) for v, m in zip(daily_fc, daily_margin)])
            hi = pd.Series([v + m for v, m in zip(daily_fc, daily_margin)])
            future_dates = pd.date_range(
                start=last_date + timedelta(days=1),
                periods=n_periods,
                freq="D",
            )

        results[col] = {
            "name":             name,
            "unit":             unit,
            "forecast":         fc,
            "lower":            lo,
            "upper":            hi,
            "certainty":        certainty,
            "dates":            future_dates,
            "historical":       series,
            "historical_dates": ts_df["datum_dt"].iloc[-len(series):],
        }

    return results


def weather_correlation_summary(ts_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if ts_df.empty:
        return None
    weer_cols   = ["temp_gem", "wind_gem", "zon_gem", "neerslag_som"]
    target_cols = ["n_berichten", "spread_eur", "buy_volume_mwh"]
    available_weer    = [c for c in weer_cols   if c in ts_df.columns and ts_df[c].notna().sum() > 3]
    available_targets = [c for c in target_cols if c in ts_df.columns and ts_df[c].notna().sum() > 3]
    if not available_weer or not available_targets:
        return None
    corr_df = ts_df[available_weer + available_targets].corr()
    return corr_df.loc[available_weer, available_targets]

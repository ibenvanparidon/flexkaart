"""
api_clients.py - Externe API-koppelingen voor Flexkaart
Open-Meteo (gratis, geen key), PDOK geocoding, CBS gebiedsinformatie.
"""

import requests
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional

OPEN_METEO_HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_LAT = 52.10
DEFAULT_LON = 5.18


def fetch_weather(start_date, end_date):
    try:
        return _fetch_open_meteo(start_date, end_date)
    except Exception as e:
        print(f"Open-Meteo API fout: {e}")
        return _generate_synthetic_weather(start_date, end_date)


def _fetch_open_meteo(start_date, end_date):
    all_frames = []
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    today = pd.Timestamp.now().normalize()
    if end >= today:
        end = today - timedelta(days=2)

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=364), end)
        params = {
            "latitude": DEFAULT_LAT,
            "longitude": DEFAULT_LON,
            "start_date": chunk_start.strftime("%Y-%m-%d"),
            "end_date": chunk_end.strftime("%Y-%m-%d"),
            "daily": ",".join([
                "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
                "wind_speed_10m_max", "wind_direction_10m_dominant",
                "sunshine_duration", "precipitation_sum", "cloud_cover_mean",
            ]),
            "timezone": "Europe/Amsterdam",
        }
        response = requests.get(OPEN_METEO_HISTORICAL_URL, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            daily = data.get("daily", {})
            if daily and daily.get("time"):
                sun_raw = daily.get("sunshine_duration") or []
                sun_hours = [round(s / 3600, 1) if s is not None else None for s in sun_raw]
                chunk_df = pd.DataFrame({
                    "datum": pd.to_datetime(daily["time"]),
                    "station": "De Bilt",
                    "temp_gem": daily.get("temperature_2m_mean"),
                    "temp_max": daily.get("temperature_2m_max"),
                    "temp_min": daily.get("temperature_2m_min"),
                    "windsnelheid": daily.get("wind_speed_10m_max"),
                    "windrichting": daily.get("wind_direction_10m_dominant"),
                    "zonneschijnduur": sun_hours if sun_hours else None,
                    "neerslag": daily.get("precipitation_sum"),
                    "bewolking": daily.get("cloud_cover_mean"),
                })
                all_frames.append(chunk_df)
        else:
            raise Exception(f"Open-Meteo status {response.status_code}")
        chunk_start = chunk_end + timedelta(days=1)

    if not all_frames:
        raise Exception("Geen data ontvangen van Open-Meteo")
    result = pd.concat(all_frames, ignore_index=True)
    result = result.drop_duplicates(subset=["datum"]).sort_values("datum").reset_index(drop=True)
    return result


def _generate_synthetic_weather(start_date, end_date):
    dates = pd.date_range(start=start_date, end=end_date, freq="D")
    np.random.seed(42)
    rows = []
    for d in dates:
        month = d.month
        if month in [12, 1, 2]:
            temp_base, wind_base, sun_base = 3, 5, 1.5
        elif month in [3, 4, 5]:
            temp_base, wind_base, sun_base = 10, 4, 5
        elif month in [6, 7, 8]:
            temp_base, wind_base, sun_base = 19, 3.5, 8
        else:
            temp_base, wind_base, sun_base = 11, 5, 3
        temp = temp_base + np.random.normal(0, 3)
        wind = max(0.5, wind_base + np.random.normal(0, 2))
        sun = max(0, sun_base + np.random.normal(0, 2))
        rain = max(0, np.random.exponential(2))
        rows.append({
            "datum": d, "station": "De Bilt (synthetisch)",
            "temp_gem": round(temp, 1),
            "temp_max": round(temp + abs(np.random.normal(3, 1)), 1),
            "temp_min": round(temp - abs(np.random.normal(3, 1)), 1),
            "windsnelheid": round(wind, 1),
            "windrichting": round(np.random.uniform(0, 360)),
            "zonneschijnduur": round(sun, 1),
            "neerslag": round(rain, 1),
            "bewolking": round(np.clip(np.random.normal(50, 20), 0, 100)),
        })
    return pd.DataFrame(rows)


PDOK_LOCATIE_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"


def geocode_postcode_pdok(postcode):
    try:
        params = {"q": postcode, "fq": "type:postcode", "rows": 1,
                  "fl": "centroide_ll,gemeentenaam,wijknaam,buurtnaam"}
        response = requests.get(PDOK_LOCATIE_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            docs = data.get("response", {}).get("docs", [])
            if docs:
                doc = docs[0]
                centroid = doc.get("centroide_ll", "")
                lat, lon = _parse_wkt_point(centroid)
                return {"lat": lat, "lon": lon, "gemeente": doc.get("gemeentenaam", ""),
                        "wijk": doc.get("wijknaam", ""), "buurt": doc.get("buurtnaam", "")}
    except Exception:
        pass
    return {"lat": None, "lon": None, "gemeente": None, "wijk": None, "buurt": None}


def _parse_wkt_point(wkt):
    import re
    match = re.search(r"POINT\(([\d.]+)\s+([\d.]+)\)", wkt)
    if match:
        return float(match.group(2)), float(match.group(1))
    return None, None


CBS_ODATA_URL = "https://opendata.cbs.nl/ODataApi/odata"


def fetch_cbs_kerncijfers_wijken(gemeente=None):
    try:
        dataset_id = "85618NED"
        url = f"{CBS_ODATA_URL}/{dataset_id}/TypedDataSet"
        params = {"$format": "json", "$top": 500}
        if gemeente:
            params["$filter"] = f"substringof('{gemeente}', Gemeentenaam)"
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            records = data.get("value", [])
            if records:
                df = pd.DataFrame(records)
                str_cols = df.select_dtypes(include=["object"]).columns
                for col in str_cols:
                    df[col] = df[col].str.strip() if hasattr(df[col], "str") else df[col]
                return df
    except Exception as e:
        print(f"CBS API fout: {e}")
    return pd.DataFrame()


def get_terreintype_for_area(gemeente, wijk=None):
    try:
        df = fetch_cbs_kerncijfers_wijken(gemeente)
        if df.empty:
            return "Onbekend"
        if "OmgevingsadressendichtheidPerKm2_153" in df.columns:
            dichtheid = pd.to_numeric(df["OmgevingsadressendichtheidPerKm2_153"], errors="coerce").mean()
            if dichtheid and dichtheid > 2000:
                return "Stedelijk"
            elif dichtheid and dichtheid > 500:
                return "Woonwijk"
            elif dichtheid and dichtheid > 100:
                return "Gemengd"
            else:
                return "Agrarisch"
    except Exception:
        pass
    return "Onbekend"


def cache_weather_to_db(db_path, start_date, end_date):
    df = fetch_weather(start_date, end_date)
    if df.empty:
        return 0
    conn = sqlite3.connect(db_path)
    df["datum"] = df["datum"].astype(str)
    df.to_sql("weerdata", conn, if_exists="replace", index=False)
    count = len(df)
    conn.close()
    return count

"""
data_fetcher.py - Multi-source GOPACS data fetcher met SQLite upsert.
Haalt data op van 4 GOPACS-endpoints + Open-Meteo weerdata.
"""

import requests
import sqlite3
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# GOPACS API endpoints (ontdekt via runtime config)
GOPACS_CONFIG = {
    "publicReporting": "https://public-reporting.gopacs-services.eu",
    "idcons": "https://idcons.gopacs-services.eu",
}

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

# Organisatie ID -> naam mapping (uit expenses endpoint)
ORG_NAMES = {}  # wordt dynamisch gevuld


# ── Database ──────────────────────────────────────────

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS announcements (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        message TEXT,
        created_ts INTEGER,
        updated_ts INTEGER,
        problem_area TEXT,
        buy_area TEXT,
        sell_area TEXT,
        required_profile_mw TEXT,
        remaining_profile_mw TEXT,
        state TEXT,
        period_start INTEGER,
        period_end INTEGER,
        duration_hours REAL,
        n_quarters INTEGER,
        organisation TEXT,
        compliance_type TEXT,
        type TEXT,
        bid_start INTEGER,
        bid_end INTEGER,
        zip_codes TEXT,
        day_ts INTEGER,
        -- afgeleide kolommen
        datum TEXT,
        jaar INTEGER,
        maand INTEGER,
        gem_required_mw REAL,
        max_required_mw REAL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS cleared_buckets (
        clearing_event_id TEXT PRIMARY KEY,
        organisation TEXT,
        buy_volume_mwh REAL,
        sell_volume_mwh REAL,
        start_time TEXT,
        end_time TEXT,
        ptu_data TEXT,
        datum TEXT,
        jaar INTEGER,
        maand INTEGER
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS expenses (
        year INTEGER,
        month INTEGER,
        organisation_id TEXT,
        organisation_name TEXT,
        sell_volume REAL,
        buy_volume REAL,
        spread REAL,
        PRIMARY KEY (year, month, organisation_id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS performance (
        year INTEGER,
        month INTEGER,
        spread_eur REAL,
        buy_volume_mwh REAL,
        sell_volume_mwh REAL,
        buy_price_eur REAL,
        sell_price_eur REAL,
        PRIMARY KEY (year, month)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS weather (
        datum TEXT PRIMARY KEY,
        temp_gem REAL,
        temp_max REAL,
        temp_min REAL,
        windsnelheid REAL,
        windrichting REAL,
        zonneschijnduur REAL,
        neerslag REAL,
        bewolking REAL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS fetch_log (
        source TEXT PRIMARY KEY,
        last_fetch TEXT,
        records_fetched INTEGER
    )""")

    conn.commit()
    conn.close()


# ── Announcements ─────────────────────────────────────

def fetch_announcements(db_path, max_pages=50, page_size=500):
    base = GOPACS_CONFIG["idcons"]
    conn = sqlite3.connect(db_path)
    total_new = 0

    for page in range(max_pages):
        url = f"{base}/public/announcements?page={page}&size={page_size}"
        try:
            r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  Announcements page {page} error: {e}")
            break

        items = data.get("content", [])
        if not items:
            break

        for item in items:
            req_mw = item.get("requiredProblemProfileInMW") or []
            rem_mw = item.get("remainingProblemProfileInMW") or []
            period = item.get("problemPeriod") or {}
            bid = item.get("bidValidityPeriod") or {}
            created_ts = item.get("createdTimestamp")
            dt = datetime.fromtimestamp(created_ts / 1000) if created_ts else None

            row = (
                item["id"],
                item.get("problemId"),
                item.get("message"),
                created_ts,
                item.get("lastUpdatedTimestamp"),
                item.get("problemAreaDescription"),
                item.get("requestAreaDescriptionBuyOrders"),
                item.get("requestAreaDescriptionSellOrders"),
                json.dumps(req_mw),
                json.dumps(rem_mw),
                item.get("announcementState"),
                period.get("startTime"),
                period.get("endTime"),
                period.get("durationInHours"),
                period.get("numberOfQuartersInTimeSpan"),
                item.get("organisationName"),
                item.get("complianceType"),
                item.get("type"),
                bid.get("startTime"),
                bid.get("endTime"),
                json.dumps(item.get("congestedZipCodes") or []),
                item.get("day"),
                dt.strftime("%Y-%m-%d") if dt else None,
                dt.year if dt else None,
                dt.month if dt else None,
                round(np.mean(req_mw), 3) if req_mw else None,
                round(max(req_mw), 3) if req_mw else None,
            )

            try:
                conn.execute("""INSERT OR IGNORE INTO announcements VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
                if conn.total_changes:
                    total_new += 1
            except Exception:
                pass

        conn.commit()

        if data.get("last", True):
            break

        time.sleep(0.2)

    conn.execute("""INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)""",
                 ("announcements", datetime.now().isoformat(), total_new))
    conn.commit()
    conn.close()
    return total_new


# ── Cleared Buckets ───────────────────────────────────

def fetch_cleared_buckets(db_path, max_pages=20, page_size=500):
    base = GOPACS_CONFIG["publicReporting"]
    conn = sqlite3.connect(db_path)
    total_new = 0

    for page in range(max_pages):
        url = f"{base}/clearedbuckets?page={page}&size={page_size}"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ClearedBuckets page {page} error: {e}")
            break

        items = data.get("content", [])
        if not items:
            break

        for item in items:
            start = item.get("startTime", "")
            dt = pd.to_datetime(start, errors="coerce")

            row = (
                item["clearingEventId"],
                item.get("organisationName"),
                item.get("buyVolumeInMWh"),
                item.get("sellVolumeInMWh"),
                start,
                item.get("endTime"),
                json.dumps(item.get("clearedVolumesForPtus") or []),
                dt.strftime("%Y-%m-%d") if pd.notna(dt) else None,
                dt.year if pd.notna(dt) else None,
                dt.month if pd.notna(dt) else None,
            )

            try:
                conn.execute("INSERT OR IGNORE INTO cleared_buckets VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            except Exception:
                pass

        conn.commit()
        total_new += len(items)

        if data.get("last", True):
            break

        time.sleep(0.2)

    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                 ("cleared_buckets", datetime.now().isoformat(), total_new))
    conn.commit()
    conn.close()
    return total_new


# ── Expenses ──────────────────────────────────────────

def fetch_expenses(db_path):
    base = GOPACS_CONFIG["publicReporting"]
    try:
        r = requests.get(f"{base}/expenses", timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Expenses error: {e}")
        return 0

    orgs = {o["id"]: o["name"] for o in data.get("organisations", [])}
    global ORG_NAMES
    ORG_NAMES = orgs
    expenses = data.get("expenses", [])

    conn = sqlite3.connect(db_path)
    total = 0

    for exp in expenses:
        year = exp["year"]
        month = exp["month"]
        sell = exp.get("sellVolumesPerOrganisation", {})
        buy = exp.get("buyVolumesPerOrganisation", {})
        spread = exp.get("spreadPerOrganisation", {})

        for org_id in orgs:
            row = (
                year, month, org_id, orgs[org_id],
                sell.get(org_id, 0),
                buy.get(org_id, 0),
                spread.get(org_id, 0) if spread else 0,
            )
            conn.execute("INSERT OR REPLACE INTO expenses VALUES (?,?,?,?,?,?,?)", row)
            total += 1

    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                 ("expenses", datetime.now().isoformat(), total))
    conn.commit()
    conn.close()
    return total


# ── Performance Metrics ───────────────────────────────

def fetch_performance(db_path):
    base = GOPACS_CONFIG["publicReporting"]
    try:
        r = requests.get(f"{base}/performance-metrics", timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Performance error: {e}")
        return 0

    conn = sqlite3.connect(db_path)
    total = 0

    for year_data in data.get("volumeAndCostsPerYear", []):
        year = year_data["year"]
        for m in year_data.get("volumeAndCostsPerMonth", []):
            vc = m["volumeAndCosts"]
            row = (
                year, m["month"],
                vc.get("spreadInEuro", 0),
                vc.get("buyVolumeInMWh", 0),
                vc.get("sellVolumeInMWh", 0),
                vc.get("buyOrderPriceInEuro", 0),
                vc.get("sellOrderPriceInEuro", 0),
            )
            conn.execute("INSERT OR REPLACE INTO performance VALUES (?,?,?,?,?,?,?)", row)
            total += 1

    # Sla ook totalen op
    meta = data.get("volumeAndCostsInTotal", {})
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                 ("performance", datetime.now().isoformat(), total))
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                 ("perf_grid_connections", datetime.now().isoformat(),
                  data.get("numberOfActiveGridConnections", 0)))
    conn.commit()
    conn.close()
    return total


# ── Weather (Open-Meteo) ─────────────────────────────

def fetch_weather(db_path, start_date="2020-01-01", end_date=None):
    if end_date is None:
        end_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    existing = set()
    try:
        rows = conn.execute("SELECT datum FROM weather").fetchall()
        existing = {r[0] for r in rows}
    except Exception:
        pass

    all_frames = []
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=364), end)
        params = {
            "latitude": 52.10, "longitude": 5.18,
            "start_date": chunk_start.strftime("%Y-%m-%d"),
            "end_date": chunk_end.strftime("%Y-%m-%d"),
            "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,"
                     "wind_speed_10m_max,wind_direction_10m_dominant,"
                     "sunshine_duration,precipitation_sum,cloud_cover_mean",
            "timezone": "Europe/Amsterdam",
        }
        try:
            r = requests.get(OPEN_METEO_URL, params=params, timeout=30)
            if r.status_code == 200:
                daily = r.json().get("daily", {})
                if daily and daily.get("time"):
                    sun = daily.get("sunshine_duration") or []
                    for i, d in enumerate(daily["time"]):
                        if d not in existing:
                            s = sun[i] if i < len(sun) else None
                            conn.execute("INSERT OR IGNORE INTO weather VALUES (?,?,?,?,?,?,?,?,?)", (
                                d,
                                (daily.get("temperature_2m_mean") or [None]*(i+1))[i],
                                (daily.get("temperature_2m_max") or [None]*(i+1))[i],
                                (daily.get("temperature_2m_min") or [None]*(i+1))[i],
                                (daily.get("wind_speed_10m_max") or [None]*(i+1))[i],
                                (daily.get("wind_direction_10m_dominant") or [None]*(i+1))[i],
                                round(s / 3600, 1) if s else None,
                                (daily.get("precipitation_sum") or [None]*(i+1))[i],
                                (daily.get("cloud_cover_mean") or [None]*(i+1))[i],
                            ))
                    conn.commit()
        except Exception as e:
            print(f"  Weather chunk error: {e}")

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.3)

    total = conn.execute("SELECT COUNT(*) FROM weather").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                 ("weather", datetime.now().isoformat(), total))
    conn.commit()
    conn.close()
    return total


# ── Orchestrator ──────────────────────────────────────

def fetch_all(db_path, progress_callback=None):
    init_db(db_path)
    results = {}

    steps = [
        ("Marktberichten", lambda: fetch_announcements(db_path)),
        ("Transacties", lambda: fetch_cleared_buckets(db_path)),
        ("Kosten", lambda: fetch_expenses(db_path)),
        ("Performance", lambda: fetch_performance(db_path)),
        ("Weer", lambda: fetch_weather(db_path)),
    ]

    for name, fn in steps:
        if progress_callback:
            progress_callback(f"{name} ophalen...")
        try:
            n = fn()
            results[name] = n
        except Exception as e:
            results[name] = f"Error: {e}"

    return results

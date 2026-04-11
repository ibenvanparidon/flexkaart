"""
data_import.py — GOPACS Excel → SQLite import pipeline
Leest de Excel, schoont op, verrijkt met geocoding, en schrijft naar SQLite.
"""

import sqlite3
import os
import re
import pandas as pd
import numpy as np
from pathlib import Path

# Postcode → Provincie mapping op basis van eerste 2 cijfers
# Bron: https://nl.wikipedia.org/wiki/Postcodes_in_Nederland
POSTCODE_PROVINCIE_MAP = {
    range(10, 20): "Groningen",
    range(78, 80): "Groningen",
    range(96, 100): "Groningen",
    range(80, 86): "Friesland",
    range(86, 88): "Friesland",
    range(88, 93): "Drenthe",
    range(93, 96): "Drenthe",
    range(37, 39): "Overijssel",
    range(74, 78): "Overijssel",
    range(49, 50): "Overijssel",
    range(80, 83): "Flevoland",  # deels overlap, specifieke postcodes
    range(38, 39): "Flevoland",
    range(82, 83): "Flevoland",
    range(13, 14): "Flevoland",
    range(36, 37): "Flevoland",
    range(39, 40): "Gelderland",
    range(40, 42): "Gelderland",
    range(65, 69): "Gelderland",
    range(69, 74): "Gelderland",
    range(34, 36): "Utrecht",
    range(14, 15): "Noord-Holland",
    range(15, 24): "Noord-Holland",
    range(10, 13): "Noord-Holland",
    range(24, 30): "Zuid-Holland",
    range(30, 34): "Zuid-Holland",
    range(43, 47): "Zeeland",
    range(47, 50): "Noord-Brabant",
    range(50, 58): "Noord-Brabant",
    range(58, 65): "Limburg",
}


def postcode_to_provincie(postcode: str) -> str | None:
    """Bepaal de provincie op basis van de eerste 2 cijfers van een postcode."""
    if not postcode or not isinstance(postcode, str):
        return None
    digits = re.findall(r"\d+", postcode)
    if not digits:
        return None
    pc_num = int(digits[0][:2]) if len(digits[0]) >= 2 else int(digits[0])

    # Specifiekere mapping
    pc4 = int(digits[0][:4]) if len(digits[0]) >= 4 else pc_num * 100
    if 1300 <= pc4 <= 1399:
        return "Flevoland"
    if 3600 <= pc4 <= 3699:
        return "Flevoland"
    if 8200 <= pc4 <= 8299:
        return "Flevoland"

    for pc_range, prov in POSTCODE_PROVINCIE_MAP.items():
        if pc_num in pc_range:
            return prov
    return None


# Geschatte lat/lon per postcodegebied (eerste 2 cijfers)
# Gebruikt voor de kaartvisualisatie als PDOK niet beschikbaar is
POSTCODE_COORDS = {
    "10": (52.374, 4.890), "11": (52.35, 4.85), "12": (52.40, 4.95),
    "13": (52.50, 5.15), "14": (52.62, 4.75), "15": (52.65, 4.80),
    "16": (52.72, 5.05), "17": (52.75, 5.20), "18": (52.68, 5.30),
    "19": (52.50, 4.65), "20": (52.08, 4.30), "21": (52.00, 4.35),
    "22": (51.92, 4.47), "23": (52.05, 4.50), "24": (52.16, 4.48),
    "25": (52.10, 4.28), "26": (52.06, 4.50), "27": (51.92, 4.50),
    "28": (51.82, 4.65), "29": (51.85, 4.52),
    "30": (52.09, 5.12), "31": (52.05, 5.10), "32": (52.15, 5.38),
    "33": (52.22, 5.17), "34": (52.10, 5.08), "35": (52.03, 5.05),
    "36": (52.35, 5.45), "37": (52.52, 6.10), "38": (52.55, 5.70),
    "39": (52.22, 5.95),
    "40": (52.00, 6.20), "41": (51.95, 5.90), "42": (51.90, 5.85),
    "43": (51.50, 3.60), "44": (51.45, 3.80), "45": (51.38, 3.95),
    "46": (51.55, 4.00), "47": (51.55, 4.45), "48": (51.60, 4.80),
    "49": (52.45, 6.45),
    "50": (51.44, 5.47), "51": (51.48, 5.40), "52": (51.68, 5.30),
    "53": (51.70, 5.05), "54": (51.55, 5.08), "55": (51.42, 5.20),
    "56": (51.58, 5.55), "57": (51.50, 5.60),
    "58": (51.44, 5.70), "59": (51.35, 5.85), "60": (51.45, 5.95),
    "61": (51.25, 5.95), "62": (50.88, 5.98), "63": (50.85, 5.70),
    "64": (50.88, 5.85),
    "65": (51.96, 5.90), "66": (52.00, 6.05), "67": (51.98, 5.95),
    "68": (51.85, 6.00), "69": (51.95, 5.85),
    "70": (52.15, 6.15), "71": (52.22, 6.90), "72": (52.45, 6.25),
    "73": (52.35, 6.65), "74": (52.50, 6.75), "75": (52.60, 6.50),
    "76": (52.75, 6.55), "77": (52.72, 6.95), "78": (52.55, 6.60),
    "79": (53.05, 6.60),
    "80": (52.75, 5.70), "81": (52.78, 5.55), "82": (52.52, 5.50),
    "83": (52.90, 5.90), "84": (53.00, 5.65), "85": (53.10, 5.85),
    "86": (53.20, 5.80), "87": (53.10, 5.65), "88": (52.85, 6.50),
    "89": (52.75, 6.45),
    "90": (53.22, 6.55), "91": (53.15, 6.75), "92": (53.10, 7.00),
    "93": (52.95, 6.75), "94": (53.00, 6.60), "95": (53.10, 6.95),
    "96": (53.25, 6.85), "97": (53.20, 7.00), "98": (53.20, 6.60),
    "99": (53.10, 6.55),
}


def geocode_postcode(postcode: str) -> tuple[float | None, float | None]:
    """Geeft geschatte (lat, lon) voor een postcode op basis van de eerste 2 cijfers."""
    if not postcode or not isinstance(postcode, str):
        return None, None
    digits = re.findall(r"\d{2}", postcode)
    if not digits:
        return None, None
    key = digits[0]
    if key in POSTCODE_COORDS:
        return POSTCODE_COORDS[key]
    return None, None


def parse_mw_profile(profile_str: str) -> list[float]:
    """Parse een semicolon-gescheiden MW profiel string naar een lijst van floats."""
    if not profile_str or not isinstance(profile_str, str):
        return []
    try:
        return [float(v.strip()) for v in profile_str.split(";") if v.strip()]
    except (ValueError, TypeError):
        return []


def clean_gopacs_data(df: pd.DataFrame) -> pd.DataFrame:
    """Schoont de GOPACS DataFrame op en voegt afgeleide kolommen toe."""

    # Maak kopie
    df = df.copy()

    # Hernoem kolommen naar snake_case voor SQLite compatibiliteit
    column_map = {
        "ID": "id",
        "Probleem ID": "probleem_id",
        "Type": "type",
        "Status": "status",
        "Netbeheerder": "netbeheerder",
        "Verplichting": "verplichting",
        "Extra informatie": "extra_informatie",
        "Buy orders gebied": "buy_orders_gebied",
        "Sell orders gebied": "sell_orders_gebied",
        "Probleem gebied": "probleem_gebied",
        "Datum aangemaakt": "datum_aangemaakt",
        "Datum laatste update": "datum_laatste_update",
        "Dag": "dag",
        "Periode start": "periode_start",
        "Periode einde": "periode_einde",
        "Duur (uur)": "duur_uur",
        "Kwartieren": "kwartieren",
        "Bieding start": "bieding_start",
        "Bieding einde": "bieding_einde",
        "Vereist profiel (MW)": "vereist_profiel_mw",
        "Resterend profiel (MW)": "resterend_profiel_mw",
        "Postcodes": "postcodes",
    }
    df.rename(columns=column_map, inplace=True)

    # Verwijder volledige duplicaten
    df.drop_duplicates(subset=["id"], keep="first", inplace=True)

    # Bereken afgeleide kolommen
    # Eerste postcode uit de lijst
    df["postcode_eerste"] = df["postcodes"].apply(
        lambda x: x.split(";")[0].strip() if isinstance(x, str) else None
    )

    # Provincie
    df["provincie"] = df["postcode_eerste"].apply(postcode_to_provincie)

    # Lat/lon voor kaartvisualisatie
    coords = df["postcode_eerste"].apply(geocode_postcode)
    df["lat"] = coords.apply(lambda x: x[0])
    df["lon"] = coords.apply(lambda x: x[1])

    # Gemiddeld vereist profiel (MW)
    df["gem_vereist_mw"] = df["vereist_profiel_mw"].apply(
        lambda x: np.mean(parse_mw_profile(x)) if isinstance(x, str) else None
    )

    # Max vereist profiel (MW)
    df["max_vereist_mw"] = df["vereist_profiel_mw"].apply(
        lambda x: max(parse_mw_profile(x)) if isinstance(x, str) and parse_mw_profile(x) else None
    )

    # Gemiddeld resterend profiel (MW)
    df["gem_resterend_mw"] = df["resterend_profiel_mw"].apply(
        lambda x: np.mean(parse_mw_profile(x)) if isinstance(x, str) else None
    )

    # Datum-afgeleide kolommen
    df["datum"] = pd.to_datetime(df["datum_aangemaakt"]).dt.date
    df["jaar"] = pd.to_datetime(df["datum_aangemaakt"]).dt.year
    df["maand"] = pd.to_datetime(df["datum_aangemaakt"]).dt.month
    df["weekdag"] = pd.to_datetime(df["datum_aangemaakt"]).dt.day_name()
    df["uur_aangemaakt"] = pd.to_datetime(df["datum_aangemaakt"]).dt.hour

    # Congestiegebied (combinatie van buy/sell)
    df["congestiegebied"] = df["buy_orders_gebied"].fillna("") + " / " + df["sell_orders_gebied"].fillna("")
    df["congestiegebied"] = df["congestiegebied"].str.strip(" / ")

    return df


def create_database(db_path: str = "flexkaart.db") -> str:
    """Maak de SQLite-database aan met het juiste schema."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Hoofdtabel: marktberichten
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS marktberichten (
            id TEXT PRIMARY KEY,
            probleem_id TEXT,
            type TEXT,
            status TEXT,
            netbeheerder TEXT,
            verplichting TEXT,
            extra_informatie TEXT,
            buy_orders_gebied TEXT,
            sell_orders_gebied TEXT,
            probleem_gebied TEXT,
            datum_aangemaakt TEXT,
            datum_laatste_update TEXT,
            dag TEXT,
            periode_start TEXT,
            periode_einde TEXT,
            duur_uur REAL,
            kwartieren REAL,
            bieding_start TEXT,
            bieding_einde TEXT,
            vereist_profiel_mw TEXT,
            resterend_profiel_mw TEXT,
            postcodes TEXT,
            postcode_eerste TEXT,
            provincie TEXT,
            lat REAL,
            lon REAL,
            gem_vereist_mw REAL,
            max_vereist_mw REAL,
            gem_resterend_mw REAL,
            datum TEXT,
            jaar INTEGER,
            maand INTEGER,
            weekdag TEXT,
            uur_aangemaakt INTEGER,
            congestiegebied TEXT
        )
    """)

    # Weerdata cache
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weerdata (
            datum TEXT PRIMARY KEY,
            station TEXT,
            temp_gem REAL,
            temp_max REAL,
            temp_min REAL,
            windsnelheid REAL,
            windrichting REAL,
            zonneschijnduur REAL,
            neerslag REAL,
            bewolking REAL
        )
    """)

    # Gebiedsinfo cache (PDOK/CBS)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gebiedsinfo (
            postcode TEXT PRIMARY KEY,
            gemeente TEXT,
            wijk TEXT,
            buurt TEXT,
            terreintype TEXT,
            inwoners INTEGER,
            oppervlakte REAL,
            energieverbruik_kwh REAL
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def import_excel_to_db(excel_path: str, db_path: str = "flexkaart.db") -> int:
    """
    Importeert de GOPACS Excel naar de SQLite-database.
    Returns: aantal geïmporteerde rijen.
    """
    # Lees Excel
    df = pd.read_excel(excel_path, sheet_name="GOPACS_Marktberichten")

    # Schoon op
    df = clean_gopacs_data(df)

    # Maak database
    create_database(db_path)

    # Schrijf naar SQLite
    conn = sqlite3.connect(db_path)

    # Datetime kolommen naar string (SQLite-compatibel)
    datetime_cols = [
        "datum_aangemaakt", "datum_laatste_update", "dag",
        "periode_start", "periode_einde", "bieding_start", "bieding_einde"
    ]
    for col in datetime_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).replace("NaT", None)

    df["datum"] = df["datum"].astype(str)

    df.to_sql("marktberichten", conn, if_exists="replace", index=False)
    count = len(df)
    conn.close()

    return count


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "GOPACS_Marktberichten.xlsx"
    n = import_excel_to_db(path)
    print(f"✓ {n} marktberichten geïmporteerd in flexkaart.db")

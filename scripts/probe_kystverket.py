"""Throwaway probe (not for merging): if only stops near an official Kystverket
berth counted as port calls, how many would OpenStreetMap confirm?"""

import requests

from HarbourOS.reliability import refresh_harbour_map, run_check
from HarbourOS.storage import DB_PATH, connect

BASE = "https://kystdatahuset.no/ws/api"
BERTHS = ("QUAY", "FERRY_QUAY", "PORT_FACILITY", "HARBOUR")

rows = []
for f in requests.get(f"{BASE}/location/norway/all/geojson", timeout=120).json()["features"]:
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    rows.append((p["systemname"], p["locationnamenor"], lat, lon))

conn = connect(DB_PATH)
conn.execute("CREATE TEMP TABLE kv (kind VARCHAR, name VARCHAR, lat DOUBLE, lon DOUBLE)")
conn.executemany("INSERT INTO kv VALUES (?, ?, ?, ?)", rows)
print("OSM features:", refresh_harbour_map(conn, save=False))
for hours in (6, 24):
    conn.execute("DROP TABLE IF EXISTS reliability_check_stops")
    conn.execute("DROP TABLE IF EXISTS reliability_checks")
    print(hours, "h:", run_check(conn, size=100000, seed=1, hours=hours, save=False))
    HAV = """6371000 * 2 * asin(sqrt(pow(sin(radians(k.lat - s.stop_latitude) / 2), 2)
        + cos(radians(s.stop_latitude)) * cos(radians(k.lat))
        * pow(sin(radians(k.lon - s.stop_longitude) / 2), 2)))"""
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE j AS
        SELECT s.port_call_key, s.verdict,
            min(CASE WHEN k.kind IN {BERTHS} THEN {HAV} END) AS berth_m,
            min(CASE WHEN k.kind = 'ANCHORAGE' THEN {HAV} END) AS anchorage_m
        FROM reliability_check_stops s LEFT JOIN kv k
          ON k.lat BETWEEN s.stop_latitude - 0.05 AND s.stop_latitude + 0.05
         AND k.lon BETWEEN s.stop_longitude - 0.1 AND s.stop_longitude + 0.1
        GROUP BY ALL
        """
    )
    print(
        conn.sql(
            """
            SELECT t.m AS berth_within_m, count(*) FILTER (WHERE berth_m <= t.m) AS kept,
              round(avg((verdict = 'confirmed')::int) FILTER (WHERE berth_m <= t.m), 3) AS confirmed,
              round(avg((verdict = 'moved')::int) FILTER (WHERE berth_m <= t.m), 3) AS moved,
              round(avg((verdict = 'confirmed')::int)
                FILTER (WHERE berth_m > t.m OR berth_m IS NULL), 3) AS confirmed_dropped,
              count(*) AS total
            FROM j, (VALUES (200), (300), (500), (750), (1000), (1500), (2000)) t(m)
            GROUP BY t.m ORDER BY t.m
            """
        )
    )
    print(
        conn.sql(
            """
            SELECT verdict, count(*) n, round(quantile_cont(berth_m, 0.5)) p50,
                   round(avg((anchorage_m <= 2000)::int), 3) near_anchorage
            FROM j GROUP BY 1
            """
        )
    )

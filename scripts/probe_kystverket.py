"""Throwaway probe (not for merging): the new rules' expected score."""

import csv
from pathlib import Path

from HarbourOS.reliability import refresh_harbour_map, run_check
from HarbourOS.storage import DB_PATH, connect

conn = connect(DB_PATH)
rows = list(csv.DictReader(Path("dbt/seeds/kystverket_locations.csv").open()))
conn.execute(
    "CREATE TEMP TABLE kystverket_locations (location_id INTEGER, locode VARCHAR, "
    "location_name VARCHAR, kind VARCHAR, latitude DOUBLE, longitude DOUBLE)"
)
conn.executemany(
    "INSERT INTO kystverket_locations VALUES (?, ?, ?, ?, ?, ?)",
    [tuple(r.values()) for r in rows],
)
import time
for attempt in range(4):
    try:
        print("OSM features:", refresh_harbour_map(conn, save=False))
        break
    except RuntimeError as error:
        print("retrying:", str(error)[:120])
        time.sleep(60)
print(run_check(conn, size=100000, seed=1, hours=24, save=False))
conn.execute(
    """
    CREATE TEMP TABLE j AS
    SELECT s.port_call_key, s.verdict, s.harbour_kind, s.official_place, s.fixes, s.still_fixes,
      min(6371000 * 2 * asin(sqrt(pow(sin(radians(k.latitude - s.stop_latitude) / 2), 2)
        + cos(radians(s.stop_latitude)) * cos(radians(k.latitude))
        * pow(sin(radians(k.longitude - s.stop_longitude) / 2), 2))))
        FILTER (WHERE k.kind IN ('quay', 'ferry_quay', 'port_facility', 'harbour')) AS berth_m,
      min(6371000 * 2 * asin(sqrt(pow(sin(radians(k.latitude - s.stop_latitude) / 2), 2)
        + cos(radians(s.stop_latitude)) * cos(radians(k.latitude))
        * pow(sin(radians(k.longitude - s.stop_longitude) / 2), 2))))
        FILTER (WHERE k.kind = 'anchorage') AS anch_m
    FROM reliability_check_stops s LEFT JOIN kystverket_locations k
      ON k.latitude BETWEEN s.stop_latitude - 0.03 AND s.stop_latitude + 0.03
     AND k.longitude BETWEEN s.stop_longitude - 0.06 AND s.stop_longitude + 0.06
    GROUP BY ALL
    """
)
print(
    conn.sql(
        """
        SELECT berth_m <= 500 AND coalesce(anch_m, 1e9) > 1500 AS new_port_call, count(*) n,
          round(avg((verdict = 'confirmed')::int), 3) confirmed,
          round(avg((official_place IS NOT NULL)::int), 3) official,
          round(avg((harbour_kind IS NOT NULL)::int), 3) on_osm,
          round(avg((verdict = 'moved')::int), 3) moved,
          round(avg((verdict = 'not_at_harbour')::int), 3) not_at_harbour
        FROM j GROUP BY 1
        """
    )
)
print(conn.sql("SELECT official_place, count(*) FROM j WHERE official_place IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10"))
print(conn.sql("SELECT place, count(*) n FROM voyage_ends GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))
print(conn.sql("SELECT count(*) ends, count(DISTINCT mmsi) ships, count(*) FILTER (WHERE lower(place) IN (SELECT lower(location_name) FROM kystverket_locations)) named FROM voyage_ends"))

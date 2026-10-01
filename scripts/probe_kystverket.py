"""Throwaway probe (not for merging): how far recent stops are from Kystverket's
official quays and anchorages, and whether Kystdatahuset's voyage API answers."""

import json
from datetime import timedelta

import requests

from HarbourOS.storage import DB_PATH, connect

BASE = "https://kystdatahuset.no/ws/api"
BERTHS = ("QUAY", "FERRY_QUAY", "PORT_FACILITY", "HARBOUR")

resp = requests.get(f"{BASE}/location/norway/all/geojson", timeout=120)
print("locations:", resp.status_code, len(resp.content))
rows = []
for f in resp.json()["features"]:
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    if lat == 0 and lon == 0:
        continue
    rows.append((p["systemname"], p["locationnamenor"], p.get("locationcode"), lat, lon))
print("usable locations:", len(rows))

conn = connect(DB_PATH)
conn.execute("CREATE TEMP TABLE kv (kind VARCHAR, name VARCHAR, code VARCHAR, lat DOUBLE, lon DOUBLE)")
conn.executemany("INSERT INTO kv VALUES (?, ?, ?, ?, ?)", rows)

latest = conn.execute("SELECT max(berth_start) FROM fact_port_call").fetchone()[0]
since = latest - timedelta(days=3)
print("stops since", since)

HAV = """6371000 * 2 * asin(sqrt(pow(sin(radians(k.lat - c.stop_latitude) / 2), 2)
    + cos(radians(c.stop_latitude)) * cos(radians(k.lat))
    * pow(sin(radians(k.lon - c.stop_longitude) / 2), 2)))"""
conn.execute(
    f"""
    CREATE TEMP TABLE d AS
    WITH c AS (
        SELECT port_call_key, mmsi, visit_type, nearest_port_km, stop_latitude, stop_longitude,
               minutes_alongside, port_locode
        FROM fact_port_call WHERE berth_start >= ? AND stop_latitude IS NOT NULL
    )
    SELECT c.*,
        min(CASE WHEN k.kind IN {BERTHS} THEN {HAV} END) AS berth_m,
        min(CASE WHEN k.kind = 'ANCHORAGE' THEN {HAV} END) AS anchorage_m,
        min(CASE WHEN k.kind = 'LOCATION_AT_SEA' THEN {HAV} END) AS at_sea_m
    FROM c LEFT JOIN kv k
      ON k.lat BETWEEN c.stop_latitude - 0.05 AND c.stop_latitude + 0.05
     AND k.lon BETWEEN c.stop_longitude - 0.1 AND c.stop_longitude + 0.1
    GROUP BY ALL
    """,
    [since],
)
print(conn.sql("SELECT visit_type, count(*) n FROM d GROUP BY 1 ORDER BY 2 DESC"))
print(
    conn.sql(
        """
        SELECT visit_type,
          round(avg((berth_m <= 300)::int), 3) le300, round(avg((berth_m <= 500)::int), 3) le500,
          round(avg((berth_m <= 1000)::int), 3) le1000, round(avg((berth_m <= 2000)::int), 3) le2000,
          round(avg((berth_m IS NULL)::int), 3) none_5km,
          round(avg((anchorage_m <= 1500 AND coalesce(berth_m, 1e9) > 1000)::int), 3) anch_only
        FROM d GROUP BY 1
        """
    )
)
print(
    conn.sql(
        """
        SELECT CASE WHEN nearest_port_km <= 2 THEN 'a <=2km' WHEN nearest_port_km <= 5 THEN 'b 2-5km'
                    WHEN nearest_port_km <= 10 THEN 'c 5-10km' ELSE 'd >10km' END band,
          count(*) n, round(quantile_cont(berth_m, 0.5)) p50, round(quantile_cont(berth_m, 0.8)) p80,
          round(quantile_cont(berth_m, 0.9)) p90
        FROM d WHERE visit_type = 'port_call' GROUP BY 1 ORDER BY 1
        """
    )
)
print("port calls 1-5 km from any official berth (sample):")
print(
    conn.sql(
        """
        SELECT mmsi, port_locode, round(berth_m) berth_m, round(anchorage_m) anch_m,
               minutes_alongside, round(stop_latitude, 4) lat, round(stop_longitude, 4) lon
        FROM d WHERE visit_type = 'port_call' AND berth_m BETWEEN 1000 AND 5000
        USING SAMPLE 20 ROWS (reservoir, 7)
        """
    ).fetchall()
)

mmsis = [
    r[0]
    for r in conn.execute(
        "SELECT mmsi FROM d WHERE visit_type = 'port_call' AND berth_m <= 500 "
        "GROUP BY mmsi USING SAMPLE 25 ROWS (reservoir, 3)"
    ).fetchall()
]
body = {
    "mmsiIds": mmsis,
    "startTime": (latest - timedelta(days=4)).isoformat(timespec="seconds"),
    "endTime": latest.isoformat(timespec="seconds"),
}
v = requests.post(f"{BASE}/voyage/for-ships/by-mmsi", json=body, timeout=120)
print("voyages:", v.status_code, v.text[:300] if v.status_code != 200 else "")
if v.ok:
    data = v.json().get("data") or []
    print("voyage rows:", len(data), "ships with voyages:", len({x["mmsi"] for x in data}), "of", len(mmsis))
    for x in data[:8]:
        print(json.dumps(x, ensure_ascii=False)[:400])
    found = {x["mmsi"] for x in data}
    print(
        conn.execute(
            "SELECT d.mmsi, p.port_name, d.port_locode FROM d LEFT JOIN dim_port p USING (port_locode) "
            "WHERE d.mmsi IN (SELECT unnest(?)) AND visit_type = 'port_call' LIMIT 15",
            [list(found)],
        ).fetchall()
    )

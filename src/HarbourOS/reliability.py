"""Check a random sample of the latest port calls against an independent map.

    uv run python -m HarbourOS.reliability                              # the pipeline
    uv run python -m HarbourOS.reliability --no-save --json out.json    # a preview

The confidence score says how well a ship's own signals agree with each other,
which is not the same as being right. This check asks a second, independent
source instead: OpenStreetMap's map of quays, piers, harbours, port areas,
marinas and ferry terminals. Every pipeline run picks 100 random port calls
that began in the last six hours of data and asks two things of each:

1. Was the stop within 300 m of something OpenStreetMap maps as a place ships
   moor?
2. Did the ship stay put? At least 90% of its GPS positions during the stop
   must lie within 300 m of the stop's centre. This uses positions only, not
   the speed or the crew-typed status the stop was detected from.

A second, separate witness is the official record. Larger ships report each
voyage to the authorities (SafeSeaNet), and Kystverket publishes those reports
in its open Kystdatahuset API (ships of 45 m and longer, without a login). A
stop also counts as confirmed when the ship reported a voyage to or from a
place within 2 km of it, with an estimated arrival or departure within 12
hours of the stop. The places are looked up in Kystverket's location register
(the kystverket_locations seed). Neither witness is used to detect port calls:
the pipeline decides "at a berth" from Kystverket's register, not from
OpenStreetMap, and never reads the voyage reports.

A stop that passes is "confirmed". A stop with fewer than two positions
during the stop is "too little data". The headline on the website counts it as
not confirmed, so the figure can only err on the cautious side. OpenStreetMap
leaves out some quays, so a real port call can fail check 1. The reverse, a
false stop passing, would need a ship to sit still beside a mapped quay, which
is a port call by any reasonable definition.

The map is downloaded from the Overpass API at most once every 30 days into
harbour_features, as one bounding box per feature. Each run appends one row to
reliability_checks and one row per checked stop to reliability_check_stops,
and the dashboard reads both (dashboard/src/data/reliability.json.py).

With --no-save nothing is written to the warehouse: the map (if it has to be
downloaded) and the results go into temporary tables that vanish with the
connection. The dashboard preview uses that, so previews never write.
"""

import argparse
import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

from HarbourOS import osm
from HarbourOS.storage import DB_PATH, connect

SAMPLE_SIZE = 100
WINDOW_HOURS = 6
NEAR_M = 300
STILL_M = 300
STILL_SHARE = 0.9
VERDICTS = ("confirmed", "not_at_harbour", "moved", "too_little_data")
# kystdatahuset.no now redirects here, and a redirected POST turns into a GET,
# which the API refuses (405). Call the current address directly.
VOYAGES_URL = "https://kystdatahuset.kystverket.no/ws/api/voyage/for-ships/by-mmsi"
OFFICIAL_M = 2000
OFFICIAL_HOURS = 12

Voyages = Callable[[list[int], datetime, datetime], list[dict[str, Any]]]

CHECK_COLUMNS = {
    "checked_at": "TIMESTAMP",
    "seed": "BIGINT",
    "window_start": "TIMESTAMP",
    "window_end": "TIMESTAMP",
    "candidates": "INTEGER",
    "checked": "INTEGER",
    **{v: "INTEGER" for v in VERDICTS},
    "harbour_features": "INTEGER",
    "official_voyages": "INTEGER",
}
STOP_COLUMNS = {
    "checked_at": "TIMESTAMP",
    "port_call_key": "VARCHAR",
    "mmsi": "INTEGER",
    "port_locode": "VARCHAR",
    "berth_start": "TIMESTAMP",
    "berth_end": "TIMESTAMP",
    "stop_latitude": "DOUBLE",
    "stop_longitude": "DOUBLE",
    "fixes": "INTEGER",
    "still_fixes": "INTEGER",
    "harbour_kind": "VARCHAR",
    "harbour_name": "VARCHAR",
    "official_place": "VARCHAR",
    "verdict": "VARCHAR",
}


def overpass_query() -> str:
    return osm.overpass_query(osm.HARBOUR_TAGS)


def parse_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return osm.parse_features(payload, osm.HARBOUR_TAGS)


def fetch_harbour_features() -> list[dict[str, Any]]:
    return osm.fetch_features(osm.HARBOUR_TAGS)


def refresh_harbour_map(
    conn: duckdb.DuckDBPyConnection,
    save: bool = True,
    fetch: osm.Fetch = fetch_harbour_features,
) -> int:
    """Make sure harbour_features is there and under 30 days old; return its size.

    If the download fails but an older map exists, the older map is used: a
    month-old map of quays is still a good map.
    """
    return osm.refresh_layer(conn, "harbour_features", fetch, save=save, required=True)


CANDIDATES = """
    WITH latest AS (SELECT max(message_time) AS window_end FROM ais_messages_silver)
    SELECT f.port_call_key, f.mmsi, f.port_locode, f.berth_start, f.berth_end,
           CAST(f.stop_latitude AS DOUBLE), CAST(f.stop_longitude AS DOUBLE),
           latest.window_end
    FROM fact_port_call f, latest
    WHERE f.visit_type = 'port_call'
      AND f.stop_latitude IS NOT NULL
      AND f.berth_start >= latest.window_end - to_hours(CAST(? AS BIGINT))
    ORDER BY f.port_call_key
"""

EVIDENCE = """
    WITH fixes AS (
        SELECT
            c.port_call_key,
            count(m.mmsi) AS fixes,
            count(m.mmsi) FILTER (WHERE 6371000 * 2 * asin(sqrt(
                pow(sin(radians(CAST(m.latitude AS DOUBLE) - c.lat) / 2), 2)
                + cos(radians(c.lat)) * cos(radians(CAST(m.latitude AS DOUBLE)))
                * pow(sin(radians(CAST(m.longitude AS DOUBLE) - c.lon) / 2), 2)
            )) <= $still_m) AS still_fixes
        FROM checked c
        LEFT JOIN ais_messages_silver m
          ON m.mmsi = c.mmsi AND m.message_time BETWEEN c.berth_start AND c.berth_end
        GROUP BY c.port_call_key
    ),
    near AS (
        SELECT
            c.port_call_key,
            h.kind,
            h.name,
            row_number() OVER (
                PARTITION BY c.port_call_key ORDER BY h.name IS NULL, h.kind, h.osm_id
            ) AS pick
        FROM checked c
        JOIN harbour_features h
          ON c.lat BETWEEN h.min_lat - $near_deg AND h.max_lat + $near_deg
         AND c.lon BETWEEN h.min_lon - $near_deg / cos(radians(c.lat))
                       AND h.max_lon + $near_deg / cos(radians(c.lat))
    )
    SELECT c.port_call_key, f.fixes, f.still_fixes, n.kind, n.name
    FROM checked c
    JOIN fixes f USING (port_call_key)
    LEFT JOIN near n ON n.port_call_key = c.port_call_key AND n.pick = 1
"""


def fetch_voyages(mmsis: list[int], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """The voyages these ships reported between start and end (UTC)."""
    response = requests.post(
        VOYAGES_URL,
        json={
            "mmsiIds": mmsis,
            "startTime": start.isoformat(timespec="seconds"),
            "endTime": end.isoformat(timespec="seconds"),
        },
        headers=osm.OVERPASS_HEADERS,
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("data") or []


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def voyage_ends(voyages: list[dict[str, Any]]) -> list[tuple[int, str, datetime]]:
    """Each reported departure and arrival as (mmsi, place, estimated time)."""
    ends = []
    for v in voyages:
        departure = (v.get("origin"), v.get("etd"))
        arrival = (v.get("destination"), v.get("eta"))
        for place, moment in (departure, arrival):
            when = _utc(moment)
            if v.get("mmsi") and place and when:
                ends.append((int(v["mmsi"]), " ".join(place.split()), when))
    return ends


OFFICIAL = """
    SELECT c.port_call_key, min(e.place) AS place
    FROM checked c
    JOIN voyage_ends e
      ON e.mmsi = c.mmsi
     AND e.reported_at BETWEEN c.berth_start - to_hours(CAST($hours AS BIGINT))
                  AND c.berth_end + to_hours(CAST($hours AS BIGINT))
    JOIN kystverket_locations k ON lower(k.location_name) = lower(e.place)
    WHERE 6371000 * 2 * asin(sqrt(
        pow(sin(radians(k.latitude - c.lat) / 2), 2)
        + cos(radians(c.lat)) * cos(radians(k.latitude))
        * pow(sin(radians(k.longitude - c.lon) / 2), 2)
    )) <= $official_m
    GROUP BY c.port_call_key
"""


def official_records(
    conn: duckdb.DuckDBPyConnection,
    chosen: list[tuple],
    voyages: Voyages | None,
) -> tuple[dict[str, str], int | None]:
    """Which checked stops the ships' own voyage reports confirm.

    Returns {port_call_key: reported place} and how many voyages came back
    (None when the reports or the location register were unavailable).
    """
    if voyages is None or not _has_table(conn, "kystverket_locations"):
        return {}, None
    mmsis = sorted({row[1] for row in chosen})
    start = min(row[3] for row in chosen) - timedelta(hours=OFFICIAL_HOURS)
    end = max(row[4] for row in chosen) + timedelta(hours=OFFICIAL_HOURS)
    try:
        reported = voyages(mmsis, start, end)
    except (requests.RequestException, ValueError) as error:
        print(f"Official voyage reports unavailable: {error}")
        return {}, None
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE voyage_ends "
        "(mmsi INTEGER, place VARCHAR, reported_at TIMESTAMP)"
    )
    ends = voyage_ends(reported)
    if ends:
        conn.executemany("INSERT INTO voyage_ends VALUES (?, ?, ?)", ends)
    matches = conn.execute(OFFICIAL, {"hours": OFFICIAL_HOURS, "official_m": OFFICIAL_M}).fetchall()
    return dict(matches), len(reported)


def verdict(
    fixes: int, still_fixes: int, harbour_kind: str | None, official_place: str | None = None
) -> str:
    if official_place is not None:
        return "confirmed"
    if fixes < 2:
        return "too_little_data"
    if harbour_kind is None:
        return "not_at_harbour"
    if still_fixes < STILL_SHARE * fixes:
        return "moved"
    return "confirmed"


def run_check(
    conn: duckdb.DuckDBPyConnection,
    size: int = SAMPLE_SIZE,
    seed: int = 0,
    hours: int = WINDOW_HOURS,
    save: bool = True,
    harbour_features: int = 0,
    voyages: Voyages | None = fetch_voyages,
) -> dict[str, Any] | None:
    """Check `size` random recent port calls and record the results.

    Returns the run's summary row, or None when there was nothing to check.
    """
    rows = conn.execute(CANDIDATES, [hours]).fetchall()
    if not rows:
        return None
    window_end = rows[0][7]
    chosen = random.Random(seed).sample(rows, min(size, len(rows)))

    conn.execute(
        "CREATE OR REPLACE TEMP TABLE checked (port_call_key VARCHAR, mmsi INTEGER, "
        "port_locode VARCHAR, berth_start TIMESTAMP, berth_end TIMESTAMP, "
        "lat DOUBLE, lon DOUBLE)"
    )
    conn.executemany("INSERT INTO checked VALUES (?, ?, ?, ?, ?, ?, ?)", [r[:7] for r in chosen])
    evidence = {
        key: (int(fixes), int(still), kind, name)
        for key, fixes, still, kind, name in conn.execute(
            EVIDENCE,
            {"still_m": STILL_M, "near_deg": NEAR_M / osm.METRES_PER_DEGREE},
        ).fetchall()
    }

    official, official_voyages = official_records(conn, chosen, voyages)

    checked_at = osm.now()
    stops = []
    for key, mmsi, locode, berth_start, berth_end, lat, lon, _ in chosen:
        fixes, still, kind, name = evidence[key]
        stops.append(
            {
                "checked_at": checked_at,
                "port_call_key": key,
                "mmsi": mmsi,
                "port_locode": locode,
                "berth_start": berth_start,
                "berth_end": berth_end,
                "stop_latitude": lat,
                "stop_longitude": lon,
                "fixes": fixes,
                "still_fixes": still,
                "harbour_kind": kind,
                "harbour_name": name,
                "official_place": official.get(key),
                "verdict": verdict(fixes, still, kind, official.get(key)),
            }
        )
    counts = {v: sum(1 for s in stops if s["verdict"] == v) for v in VERDICTS}
    summary = {
        "checked_at": checked_at,
        "seed": seed,
        "window_start": window_end - timedelta(hours=hours),
        "window_end": window_end,
        "candidates": len(rows),
        "checked": len(stops),
        **counts,
        "harbour_features": harbour_features,
        "official_voyages": official_voyages,
    }
    _record(conn, summary, stops, save)
    return summary


def _record(
    conn: duckdb.DuckDBPyConnection, summary: dict[str, Any], stops: list[dict], save: bool
) -> None:
    temp = "" if save else "TEMP "
    conn.execute(
        f"CREATE {temp}TABLE IF NOT EXISTS reliability_checks ({osm.schema(CHECK_COLUMNS)})"
    )
    conn.execute(
        f"CREATE {temp}TABLE IF NOT EXISTS reliability_check_stops ({osm.schema(STOP_COLUMNS)})"
    )
    # Tables saved by an older version of this check lack the newer columns.
    for table, columns in (
        ("reliability_checks", CHECK_COLUMNS),
        ("reliability_check_stops", STOP_COLUMNS),
    ):
        for name, kind in columns.items():
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {kind}")
    osm.load_rows(
        conn,
        "INSERT INTO reliability_checks BY NAME SELECT * FROM {rows}",
        [summary],
        CHECK_COLUMNS,
    )
    osm.load_rows(
        conn,
        "INSERT INTO reliability_check_stops BY NAME SELECT * FROM {rows}",
        stops,
        STOP_COLUMNS,
    )


def _iso(value: Any) -> str | None:
    return value.replace(tzinfo=UTC).isoformat() if isinstance(value, datetime) else None


def _has_table(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True for a saved table or a temporary one from a --no-save run."""
    row = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ? "
        "AND (temporary OR database_name = current_database())",
        [table],
    ).fetchone()
    return bool(row and row[0])


def dashboard_summary(conn: duckdb.DuckDBPyConnection, history: int = 28) -> dict[str, Any]:
    """What the website shows: the latest check, recent ones, and the misses."""
    empty: dict[str, Any] = {"latest": None, "history": [], "unconfirmed": []}
    if not _has_table(conn, "reliability_checks"):
        return empty
    cursor = conn.execute(
        "SELECT * FROM reliability_checks ORDER BY checked_at DESC LIMIT ?", [history]
    )
    columns = [d[0] for d in cursor.description]
    runs = [dict(zip(columns, row)) for row in cursor.fetchall()]
    if not runs:
        return empty

    misses = conn.execute(
        """
        SELECT coalesce(v.vessel_name, 'MMSI ' || s.mmsi), coalesce(p.port_name, s.port_locode),
               s.verdict, s.berth_start, s.stop_latitude, s.stop_longitude, s.fixes
        FROM reliability_check_stops s
        LEFT JOIN dim_vessel v ON v.mmsi = s.mmsi
        LEFT JOIN dim_port p ON p.port_locode = s.port_locode
        WHERE s.checked_at = ? AND s.verdict <> 'confirmed'
        ORDER BY s.berth_start DESC
        """,
        [runs[0]["checked_at"]],
    ).fetchall()

    def run_json(run: dict[str, Any]) -> dict[str, Any]:
        return {k: _iso(v) if isinstance(v, datetime) else v for k, v in run.items()}

    return {
        "latest": {**run_json(runs[0]), "near_m": NEAR_M, "still_m": STILL_M},
        "history": [
            {
                "checked_at": _iso(r["checked_at"]),
                "checked": r["checked"],
                "confirmed": r["confirmed"],
            }
            for r in reversed(runs)
        ],
        "unconfirmed": [
            {
                "ship": ship,
                "port": port,
                "verdict": result,
                "berth_start": _iso(start),
                "lat": lat,
                "lon": lon,
                "fixes": fixes,
            }
            for ship, port, result, start, lat, lon, fixes in misses
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--hours", type=int, default=WINDOW_HOURS)
    parser.add_argument("--seed", type=int, default=None, help="default: the current time")
    parser.add_argument("--no-save", action="store_true", help="write nothing to the warehouse")
    parser.add_argument("--json", type=Path, help="also write the dashboard summary here")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(time.time())
    save = not args.no_save
    conn = connect(DB_PATH)
    try:
        features = refresh_harbour_map(conn, save=save)
        print(f"Harbour map: {features} quays, piers, harbours and terminals")
        summary = run_check(conn, args.size, seed, args.hours, save, features)
        if summary is None:
            print(f"No port calls began in the last {args.hours} hours; nothing to check.")
        else:
            print(
                f"Checked {summary['checked']} of {summary['candidates']} recent port calls "
                f"(seed {seed}): {summary['confirmed']} confirmed, "
                f"{summary['not_at_harbour']} not near a mapped quay, {summary['moved']} moved, "
                f"{summary['too_little_data']} too little data. "
                f"Official voyage reports: {summary['official_voyages']}."
            )
        if args.json:
            args.json.write_text(json.dumps(dashboard_summary(conn)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

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

A stop that passes both is "confirmed". A stop with fewer than two positions
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
import math
import random
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

from HarbourOS.storage import DB_PATH, connect

SAMPLE_SIZE = 100
WINDOW_HOURS = 6
NEAR_M = 300
STILL_M = 300
STILL_SHARE = 0.9
MAP_MAX_AGE_DAYS = 30
# A feature bigger than this (a whole fjord tagged as a harbour, say) is so
# large that being "near" it proves nothing, so it is left out.
MAX_FEATURE_KM = 3.0
METRES_PER_DEGREE = 111_320

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# OpenStreetMap tags for places ships moor, as (key, values); None = any value.
FEATURE_TAGS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("man_made", ("pier", "quay")),
    ("landuse", ("port",)),
    ("industrial", ("port", "shipyard")),
    ("harbour", ("yes",)),
    ("seamark:type", ("harbour", "berth", "small_craft_facility")),
    ("amenity", ("ferry_terminal",)),
    ("leisure", ("marina",)),
    ("waterway", ("dock",)),
)

VERDICTS = ("confirmed", "not_at_harbour", "moved", "too_little_data")

HARBOUR_COLUMNS = {
    "osm_id": "VARCHAR",
    "kind": "VARCHAR",
    "name": "VARCHAR",
    "min_lat": "DOUBLE",
    "min_lon": "DOUBLE",
    "max_lat": "DOUBLE",
    "max_lon": "DOUBLE",
    "fetched_at": "TIMESTAMP",
}
CHECK_COLUMNS = {
    "checked_at": "TIMESTAMP",
    "seed": "BIGINT",
    "window_start": "TIMESTAMP",
    "window_end": "TIMESTAMP",
    "candidates": "INTEGER",
    "checked": "INTEGER",
    **{v: "INTEGER" for v in VERDICTS},
    "harbour_features": "INTEGER",
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
    "verdict": "VARCHAR",
}


def _now() -> datetime:
    """UTC without a time zone, like every other timestamp in the warehouse."""
    return datetime.now(UTC).replace(tzinfo=None)


def overpass_query() -> str:
    """Every mooring place in Norway and Svalbard: points, and boxes around shapes."""
    filters = []
    for key, values in FEATURE_TAGS:
        if values is None:
            filters.append(f'["{key}"]')
        else:
            filters.append(f'["{key}"~"^({"|".join(values)})$"]')
    nodes = "".join(f"node{f}(area.norway);" for f in filters)
    shapes = "".join(f"way{f}(area.norway);relation{f}(area.norway);" for f in filters)
    return (
        '[out:json][timeout:300];area["ISO3166-1"~"^(NO|SJ)$"]->.norway;'
        f"({nodes})->.points;({shapes})->.shapes;.points out;.shapes out tags bb;"
    )


def _kind(tags: dict[str, str]) -> str:
    for key, values in FEATURE_TAGS:
        if key in tags and (values is None or tags[key] in values):
            return tags[key].replace("_", " ")
    return "harbour"


def _span_km(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    height = (max_lat - min_lat) * METRES_PER_DEGREE
    width = (max_lon - min_lon) * METRES_PER_DEGREE * math.cos(math.radians(min_lat))
    return math.hypot(height, width) / 1000


def parse_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn an Overpass answer into one bounding box per feature."""
    features = []
    for element in payload.get("elements", []):
        if "lat" in element:
            box = (element["lat"], element["lon"], element["lat"], element["lon"])
        elif "bounds" in element:
            b = element["bounds"]
            box = (b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
        elif "center" in element:
            c = element["center"]
            box = (c["lat"], c["lon"], c["lat"], c["lon"])
        else:
            continue
        if _span_km(*box) > MAX_FEATURE_KM:
            continue
        tags = element.get("tags", {})
        features.append(
            {
                "osm_id": f"{element['type']}/{element['id']}",
                "kind": _kind(tags),
                "name": tags.get("name"),
                "min_lat": box[0],
                "min_lon": box[1],
                "max_lat": box[2],
                "max_lon": box[3],
            }
        )
    return features


def fetch_harbour_features() -> list[dict[str, Any]]:
    """Download the map from the first Overpass server that answers."""
    errors = []
    for url in OVERPASS_URLS:
        try:
            response = requests.post(url, data={"data": overpass_query()}, timeout=600)
            response.raise_for_status()
            features = parse_features(response.json())
        except (requests.RequestException, ValueError) as error:
            errors.append(f"{url}: {error}")
            continue
        if features:
            return features
        errors.append(f"{url}: no features in the answer")
    raise RuntimeError("Could not download the harbour map. " + "; ".join(errors))


def _load(
    conn: duckdb.DuckDBPyConnection,
    statement: str,
    rows: list[dict[str, Any]],
    columns: dict[str, str],
) -> None:
    """Run `statement` with {rows} standing for the given rows.

    The rows go through a JSON file rather than one INSERT each: tens of
    thousands of map features would otherwise mean as many round trips to
    MotherDuck.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "rows.json"
        path.write_text("\n".join(json.dumps(row, default=str) for row in rows))
        spec = ", ".join(f"'{name}': '{kind}'" for name, kind in columns.items())
        source = f"read_json('{path}', format = 'newline_delimited', columns = {{{spec}}})"
        conn.execute(statement.format(rows=source))


def _schema(columns: dict[str, str]) -> str:
    return ", ".join(f"{name} {kind}" for name, kind in columns.items())


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_catalog = current_database() AND table_schema = 'main' "
        "AND table_name = ?",
        [table],
    ).fetchone()
    return bool(row and row[0])


def refresh_harbour_map(
    conn: duckdb.DuckDBPyConnection,
    save: bool = True,
    fetch: Callable[[], list[dict[str, Any]]] = fetch_harbour_features,
) -> int:
    """Make sure harbour_features is there and under 30 days old; return its size.

    If the download fails but an older map exists, the older map is used: a
    month-old map of quays is still a good map.
    """
    saved = _table_exists(conn, "harbour_features")
    if saved:
        fetched_at, count = conn.execute(
            "SELECT max(fetched_at), count(*) FROM main.harbour_features"
        ).fetchone() or (None, 0)
        if fetched_at and fetched_at > _now() - timedelta(days=MAP_MAX_AGE_DAYS):
            return int(count)
    try:
        features = fetch()
    except Exception as error:
        if not saved:
            raise
        print(f"Keeping the saved harbour map: {error}")
        return int(count)

    fetched_at = _now()
    temp = "" if save else "TEMP "
    _load(
        conn,
        f"CREATE OR REPLACE {temp}TABLE harbour_features AS SELECT * FROM {{rows}}",
        [{**feature, "fetched_at": fetched_at} for feature in features],
        HARBOUR_COLUMNS,
    )
    return len(features)


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


def verdict(fixes: int, still_fixes: int, harbour_kind: str | None) -> str:
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
            {"still_m": STILL_M, "near_deg": NEAR_M / METRES_PER_DEGREE},
        ).fetchall()
    }

    checked_at = _now()
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
                "verdict": verdict(fixes, still, kind),
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
    }
    _record(conn, summary, stops, save)
    return summary


def _record(
    conn: duckdb.DuckDBPyConnection, summary: dict[str, Any], stops: list[dict], save: bool
) -> None:
    temp = "" if save else "TEMP "
    conn.execute(f"CREATE {temp}TABLE IF NOT EXISTS reliability_checks ({_schema(CHECK_COLUMNS)})")
    conn.execute(
        f"CREATE {temp}TABLE IF NOT EXISTS reliability_check_stops ({_schema(STOP_COLUMNS)})"
    )
    _load(
        conn,
        "INSERT INTO reliability_checks BY NAME SELECT * FROM {rows}",
        [summary],
        CHECK_COLUMNS,
    )
    _load(
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
                f"{summary['too_little_data']} too little data."
            )
        if args.json:
            args.json.write_text(json.dumps(dashboard_summary(conn)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

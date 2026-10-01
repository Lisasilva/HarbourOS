"""Map layers from OpenStreetMap: where ships moor, and where fish farms are.

    uv run python -m HarbourOS.osm      # refresh fish_farms if over 30 days old

Two layers are kept in the warehouse, each as one bounding box per feature:

- harbour_features: quays, piers, port areas, harbours, marinas and ferry
  terminals. The reliability check (reliability.py) tests port calls against
  it.
- fish_farms: aquaculture sites. Norway's are imported into OpenStreetMap from
  the Directorate of Fisheries' register. Service boats spend hours at them,
  often within 10 km of a listed port, so without this layer those stops were
  counted as port calls. fact_port_call gives them their own visit_type.

Both come from the Overpass API and are downloaded at most once every 30 days,
since quays and farms rarely move. If a download fails, the saved layer is
kept. The pipeline runs this before dbt, which reads fish_farms; when there is
no saved layer and the download fails, an empty fish_farms table is created so
dbt still runs (no stop is then labelled as a fish farm) and the next run tries
again.
"""

import json
import math
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

from HarbourOS.storage import DB_PATH, connect

Tags = tuple[tuple[str, tuple[str, ...]], ...]
Fetch = Callable[[], list[dict[str, Any]]]

MAP_MAX_AGE_DAYS = 30
# A feature bigger than this (a whole fjord tagged as a harbour, say) is so
# large that being "near" it proves nothing, so it is left out.
MAX_FEATURE_KM = 3.0
METRES_PER_DEGREE = 111_320

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
# overpass-api.de turns away requests that don't say who is asking (HTTP 406).
OVERPASS_HEADERS = {"User-Agent": "HarbourOS (https://github.com/Lisasilva/HarbourOS)"}
# Each server gets at most 4 minutes, so a busy day costs a run at most about
# 12 minutes before it carries on with the saved layer.

# OpenStreetMap tags for each layer, as (key, accepted values).
HARBOUR_TAGS: Tags = (
    ("man_made", ("pier", "quay")),
    ("landuse", ("port",)),
    ("industrial", ("port", "shipyard")),
    ("harbour", ("yes",)),
    ("seamark:type", ("harbour", "berth", "small_craft_facility")),
    ("amenity", ("ferry_terminal",)),
    ("leisure", ("marina",)),
    ("waterway", ("dock",)),
)
FISH_FARM_TAGS: Tags = (
    ("landuse", ("aquaculture",)),
    ("seamark:type", ("marine_farm",)),
)

FEATURE_COLUMNS = {
    "osm_id": "VARCHAR",
    "kind": "VARCHAR",
    "name": "VARCHAR",
    "min_lat": "DOUBLE",
    "min_lon": "DOUBLE",
    "max_lat": "DOUBLE",
    "max_lon": "DOUBLE",
    "fetched_at": "TIMESTAMP",
}


def now() -> datetime:
    """UTC without a time zone, like every other timestamp in the warehouse."""
    return datetime.now(UTC).replace(tzinfo=None)


def overpass_query(tags: Tags) -> str:
    """Every matching feature in Norway and Svalbard: points, and boxes around shapes."""
    filters = [f'["{key}"~"^({"|".join(values)})$"]' for key, values in tags]
    nodes = "".join(f"node{f}(area.norway);" for f in filters)
    shapes = "".join(f"way{f}(area.norway);relation{f}(area.norway);" for f in filters)
    return (
        '[out:json][timeout:180];area["ISO3166-1"~"^(NO|SJ)$"]->.norway;'
        f"({nodes})->.points;({shapes})->.shapes;.points out;.shapes out tags bb;"
    )


def _kind(element_tags: dict[str, str], tags: Tags) -> str:
    for key, values in tags:
        if element_tags.get(key) in values:
            return element_tags[key].replace("_", " ")
    return tags[0][1][0].replace("_", " ")


def _span_km(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    height = (max_lat - min_lat) * METRES_PER_DEGREE
    width = (max_lon - min_lon) * METRES_PER_DEGREE * math.cos(math.radians(min_lat))
    return math.hypot(height, width) / 1000


def parse_features(payload: dict[str, Any], tags: Tags) -> list[dict[str, Any]]:
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
        element_tags = element.get("tags", {})
        features.append(
            {
                "osm_id": f"{element['type']}/{element['id']}",
                "kind": _kind(element_tags, tags),
                "name": element_tags.get("name"),
                "min_lat": box[0],
                "min_lon": box[1],
                "max_lat": box[2],
                "max_lon": box[3],
            }
        )
    return features


def fetch_features(tags: Tags) -> list[dict[str, Any]]:
    """Download one layer from the first Overpass server that answers."""
    errors = []
    for url in OVERPASS_URLS:
        try:
            response = requests.post(
                url, data={"data": overpass_query(tags)}, headers=OVERPASS_HEADERS, timeout=240
            )
            response.raise_for_status()
            features = parse_features(response.json(), tags)
        except (requests.RequestException, ValueError) as error:
            errors.append(f"{url}: {error}")
            continue
        if features:
            return features
        errors.append(f"{url}: no features in the answer")
    raise RuntimeError("Could not download from OpenStreetMap. " + "; ".join(errors))


def load_rows(
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


def schema(columns: dict[str, str]) -> str:
    return ", ".join(f"{name} {kind}" for name, kind in columns.items())


def table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_catalog = current_database() AND table_schema = 'main' "
        "AND table_name = ?",
        [table],
    ).fetchone()
    return bool(row and row[0])


def refresh_layer(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    fetch: Fetch,
    save: bool = True,
    required: bool = True,
) -> int:
    """Make sure `table` is there and under 30 days old; return its size.

    If the download fails, a saved layer is kept, however old. With no saved
    layer, a failure raises when the layer is `required`, and otherwise leaves
    an empty table (with no fetch date, so the next run tries again). With
    save=False the new layer goes into a temporary table instead.
    """
    saved = table_exists(conn, table)
    count = 0
    if saved:
        fetched_at, count = conn.execute(
            f"SELECT max(fetched_at), count(*) FROM main.{table}"
        ).fetchone() or (None, 0)
        if fetched_at and fetched_at > now() - timedelta(days=MAP_MAX_AGE_DAYS):
            return int(count)
    temp = "" if save else "TEMP "
    try:
        features = fetch()
    except Exception as error:
        if saved:
            print(f"Keeping the saved {table}: {error}")
            return int(count)
        if required:
            raise
        print(f"No {table} yet, so starting with an empty one: {error}")
        conn.execute(f"CREATE {temp}TABLE {table} ({schema(FEATURE_COLUMNS)})")
        return 0

    fetched = now()
    load_rows(
        conn,
        f"CREATE OR REPLACE {temp}TABLE {table} AS SELECT * FROM {{rows}}",
        [{**feature, "fetched_at": fetched} for feature in features],
        FEATURE_COLUMNS,
    )
    return len(features)


def refresh_fish_farms(
    conn: duckdb.DuckDBPyConnection,
    fetch: Fetch = lambda: fetch_features(FISH_FARM_TAGS),
) -> int:
    return refresh_layer(conn, "fish_farms", fetch, required=False)


def main() -> None:
    # The harbour map is refreshed by the reliability check itself, which may
    # fail without stopping the pipeline; this step runs before dbt.
    conn = connect(DB_PATH)
    try:
        print(f"Fish farms: {refresh_fish_farms(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

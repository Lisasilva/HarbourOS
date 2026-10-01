"""OpenStreetMap layers: the fish-farm list and its fallbacks."""

import duckdb
import pytest

from HarbourOS.osm import (
    FISH_FARM_TAGS,
    overpass_query,
    parse_features,
    refresh_fish_farms,
    refresh_layer,
)

FARM = {
    "osm_id": "way/556359848",
    "kind": "aquaculture",
    "name": "Hogsneset N",
    "min_lat": 63.0990,
    "min_lon": 7.6690,
    "max_lat": 63.1005,
    "max_lon": 7.6715,
}


def broken():
    raise RuntimeError("Overpass is down")


def test_fish_farms_start_empty_when_the_first_download_fails():
    conn = duckdb.connect()
    assert refresh_fish_farms(conn, fetch=broken) == 0
    assert conn.execute("SELECT count(*) FROM fish_farms").fetchone() == (0,)
    # No fetch date, so the next run tries again.
    assert refresh_fish_farms(conn, fetch=lambda: [FARM]) == 1
    assert conn.execute("SELECT name FROM fish_farms").fetchone() == ("Hogsneset N",)


def test_a_saved_list_survives_a_failed_download():
    conn = duckdb.connect()
    refresh_fish_farms(conn, fetch=lambda: [FARM])
    conn.execute("UPDATE fish_farms SET fetched_at = fetched_at - INTERVAL 40 DAY")
    assert refresh_fish_farms(conn, fetch=broken) == 1


def test_a_required_layer_raises_without_any_saved_copy():
    conn = duckdb.connect()
    with pytest.raises(RuntimeError):
        refresh_layer(conn, "harbour_features", broken, required=True)


def test_fish_farm_query_and_parsing():
    query = overpass_query(FISH_FARM_TAGS)
    assert 'way["landuse"~"^(aquaculture)$"](area.norway);' in query
    assert 'node["seamark:type"~"^(marine_farm)$"](area.norway);' in query

    payload = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"seamark:type": "marine_farm", "name": "Ospøy Ø"},
                "bounds": {"minlat": 59.866, "minlon": 5.227, "maxlat": 59.867, "maxlon": 5.229},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 63.1,
                "lon": 7.67,
                "tags": {"landuse": "aquaculture"},
            },
        ]
    }
    farms = parse_features(payload, FISH_FARM_TAGS)
    assert [(f["kind"], f["name"]) for f in farms] == [
        ("marine farm", "Ospøy Ø"),
        ("aquaculture", None),
    ]

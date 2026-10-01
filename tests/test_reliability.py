"""The reliability check: recent port calls tested against a map of quays."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from HarbourOS.reliability import (
    dashboard_summary,
    overpass_query,
    parse_features,
    refresh_harbour_map,
    run_check,
)

LATEST = datetime(2026, 10, 1, 12, 0)
PIER = {
    "osm_id": "way/1",
    "kind": "pier",
    "name": "Bekhuskaien",
    "min_lat": 58.9720,
    "min_lon": 5.7445,
    "max_lat": 58.9728,
    "max_lon": 5.7452,
}


def warehouse(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(tmp_path / "gold.duckdb"))
    conn.execute(
        """
        CREATE TABLE fact_port_call (
            port_call_key VARCHAR, mmsi INTEGER, port_locode VARCHAR, visit_type VARCHAR,
            berth_start TIMESTAMP, berth_end TIMESTAMP,
            stop_latitude DOUBLE, stop_longitude DOUBLE
        );
        CREATE TABLE ais_messages_silver (mmsi INTEGER, latitude DECIMAL(10, 6),
            longitude DECIMAL(10, 6), message_time TIMESTAMP);
        CREATE TABLE dim_vessel (mmsi INTEGER, vessel_name VARCHAR);
        CREATE TABLE dim_port (port_locode VARCHAR, port_name VARCHAR);
        INSERT INTO dim_port VALUES ('NOSVG', 'Stavanger');
        INSERT INTO dim_vessel VALUES (2, 'FAR AWAY');
        """
    )
    start = LATEST - timedelta(hours=3)
    end = LATEST - timedelta(hours=1)

    def stop(
        key: str,
        mmsi: int,
        lat: float,
        lon: float,
        positions: list[tuple[float, float]],
        visit_type: str = "port_call",
        begins: datetime = start,
    ) -> None:
        conn.execute(
            "INSERT INTO fact_port_call VALUES (?, ?, 'NOSVG', ?, ?, ?, ?, ?)",
            [key, mmsi, visit_type, begins, begins + (end - start), lat, lon],
        )
        for n, (plat, plon) in enumerate(positions):
            conn.execute(
                "INSERT INTO ais_messages_silver VALUES (?, ?, ?, ?)",
                [mmsi, plat, plon, begins + timedelta(minutes=10 * n)],
            )

    beside_pier = (58.9724, 5.7440)  # about 30 m west of the pier
    still = [beside_pier] * 6
    stop("confirmed", 1, *beside_pier, still)
    stop("far", 2, 59.5, 6.5, [(59.5, 6.5)] * 6)
    stop("moved", 3, *beside_pier, [beside_pier] * 3 + [(58.99, 5.76)] * 3)
    stop("sparse", 4, *beside_pier, [beside_pier])
    stop("at_sea", 5, *beside_pier, still, visit_type="at_sea")
    stop("old", 6, *beside_pier, still, begins=LATEST - timedelta(hours=30))
    conn.execute("INSERT INTO ais_messages_silver VALUES (9, 60, 5, ?)", [LATEST])
    return conn


def with_map(conn: duckdb.DuckDBPyConnection, save: bool = True) -> int:
    return refresh_harbour_map(conn, save=save, fetch=lambda: [PIER])


def test_each_recent_port_call_gets_a_verdict(tmp_path):
    conn = warehouse(tmp_path)
    with_map(conn)
    summary = run_check(conn, size=100, seed=1, hours=6)

    assert summary is not None
    assert summary["candidates"] == 4  # not the at-sea stop, not the old one
    verdicts = dict(
        conn.execute("SELECT port_call_key, verdict FROM reliability_check_stops").fetchall()
    )
    assert verdicts == {
        "confirmed": "confirmed",
        "far": "not_at_harbour",
        "moved": "moved",
        "sparse": "too_little_data",
    }
    assert (
        summary["confirmed"],
        summary["not_at_harbour"],
        summary["moved"],
        summary["too_little_data"],
    ) == (1, 1, 1, 1)


def test_the_sample_is_random_but_repeatable(tmp_path):
    conn = warehouse(tmp_path)
    with_map(conn)
    run_check(conn, size=2, seed=7, hours=6)
    run_check(conn, size=2, seed=7, hours=6)
    runs = conn.execute(
        "SELECT checked_at, list(port_call_key ORDER BY port_call_key) "
        "FROM reliability_check_stops GROUP BY checked_at"
    ).fetchall()
    assert len(runs) == 2 and runs[0][1] == runs[1][1] and len(runs[0][1]) == 2


def test_dashboard_summary_lists_the_misses(tmp_path):
    conn = warehouse(tmp_path)
    assert dashboard_summary(conn) == {"latest": None, "history": [], "unconfirmed": []}
    with_map(conn)
    run_check(conn, size=100, seed=1, hours=6, harbour_features=1)

    result = dashboard_summary(conn)
    assert result["latest"]["checked"] == 4 and result["latest"]["confirmed"] == 1
    assert result["latest"]["checked_at"].endswith("+00:00")
    assert result["history"][-1]["confirmed"] == 1
    ships = {m["ship"]: m["verdict"] for m in result["unconfirmed"]}
    assert ships == {"FAR AWAY": "not_at_harbour", "MMSI 3": "moved", "MMSI 4": "too_little_data"}
    assert all(m["port"] == "Stavanger" for m in result["unconfirmed"])


def test_no_save_writes_nothing_to_the_warehouse(tmp_path):
    conn = warehouse(tmp_path)
    with_map(conn, save=False)
    run_check(conn, size=100, seed=1, hours=6, save=False)
    assert dashboard_summary(conn)["latest"]["checked"] == 4
    saved = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM duckdb_tables() WHERE NOT temporary"
        ).fetchall()
    }
    assert not saved & {"harbour_features", "reliability_checks", "reliability_check_stops"}


def test_nothing_recent_means_nothing_recorded(tmp_path):
    conn = warehouse(tmp_path)
    conn.execute("DELETE FROM fact_port_call WHERE visit_type = 'port_call'")
    with_map(conn)
    assert run_check(conn, size=100, seed=1, hours=6) is None


def test_the_map_is_downloaded_only_when_old_or_missing(tmp_path):
    conn = warehouse(tmp_path)
    assert with_map(conn) == 1

    def must_not_fetch():
        raise AssertionError("the saved map is fresh")

    assert refresh_harbour_map(conn, fetch=must_not_fetch) == 1

    conn.execute("UPDATE harbour_features SET fetched_at = fetched_at - INTERVAL 40 DAY")

    def broken():
        raise RuntimeError("Overpass is down")

    assert refresh_harbour_map(conn, fetch=broken) == 1  # keeps the old map
    assert refresh_harbour_map(conn, fetch=lambda: [PIER, {**PIER, "osm_id": "way/2"}]) == 2


def test_without_any_map_a_failed_download_is_an_error(tmp_path):
    conn = warehouse(tmp_path)

    def broken():
        raise RuntimeError("Overpass is down")

    with pytest.raises(RuntimeError):
        refresh_harbour_map(conn, fetch=broken)


def test_parse_features_keeps_points_and_small_shapes():
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 58.97,
                "lon": 5.74,
                "tags": {"amenity": "ferry_terminal", "name": "Fiskepirterminalen"},
            },
            {
                "type": "way",
                "id": 2,
                "tags": {"man_made": "pier"},
                "bounds": {
                    "minlat": 58.9708,
                    "minlon": 5.7291,
                    "maxlat": 58.9709,
                    "maxlon": 5.7298,
                },
            },
            {
                "type": "relation",
                "id": 3,
                "tags": {"harbour": "yes"},
                "bounds": {"minlat": 58.0, "minlon": 5.0, "maxlat": 59.0, "maxlon": 6.0},
            },
            {"type": "way", "id": 4, "tags": {"man_made": "pier"}},
        ]
    }
    features = parse_features(payload)
    assert [(f["osm_id"], f["kind"]) for f in features] == [
        ("node/1", "ferry terminal"),
        ("way/2", "pier"),
    ]
    assert features[0]["name"] == "Fiskepirterminalen"


def test_overpass_query_covers_norway_and_svalbard():
    query = overpass_query()
    assert 'area["ISO3166-1"~"^(NO|SJ)$"]' in query
    assert 'way["man_made"~"^(pier|quay)$"](area.norway);' in query
    assert ".shapes out tags bb;" in query

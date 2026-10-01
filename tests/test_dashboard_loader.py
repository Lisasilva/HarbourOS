"""Tests for the dashboard's data loader, in particular the in_port_now flag.

The loader (dashboard/src/data/port_calls.csv.py) has a dot in its filename
-- Observable's convention for a loader that produces port_calls.csv -- so it
can't be imported the normal way. The tests read its source instead.
"""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from HarbourOS.storage import initialize_bronze_table, insert_ais_snapshots
from HarbourOS.transform import (
    run_port_calls_transform,
    run_silver_transform,
    run_state_periods_transform,
)

LOADER_PATH = Path(__file__).parent.parent / "dashboard" / "src" / "data" / "port_calls.csv.py"


def _reading(mmsi: int, minute: int, speed: float, msgtime_offset=timedelta()) -> dict:
    start = datetime(2026, 9, 26, 8, 0)
    return {
        "mmsi": mmsi,
        "name": f"SHIP {mmsi}",
        "latitude": 60.39,
        "longitude": 5.32,
        "speedOverGround": speed,
        "courseOverGround": 90.0,
        "trueHeading": 90.0,
        "rateOfTurn": 0.0,
        "shipType": 70,
        "navigationalStatus": 5 if speed < 0.5 else 0,
        "stream": "terra",
        "msgtime": (start + timedelta(minutes=minute) + msgtime_offset).isoformat(),
    }


def _load_query() -> str:
    """Pull the QUERY string and its recency threshold out of the loader
    module without running its top-level connect()/stdout side effects."""
    # The loader has no `if __name__ == "__main__"` guard, so importing it
    # would connect and write to stdout. Read the source instead.
    source = LOADER_PATH.read_text()
    namespace: dict = {}
    # Strip the two lines that run at import time (the `with connect()...`
    # block) so only the QUERY constant is evaluated.
    lines = source.splitlines()
    body_start = next(i for i, line in enumerate(lines) if line.startswith("with connect()"))
    exec(compile("\n".join(lines[:body_start]), str(LOADER_PATH), "exec"), namespace)
    return namespace["QUERY"], namespace["IN_PORT_RECENCY_HOURS"]


def test_a_visit_with_no_observed_departure_and_recent_data_is_in_port_now(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "loader.duckdb"
    initialize_bronze_table(db_path=db_path)
    # Ship A: still stopped, last seen at the freshest timestamp in the data.
    rows_a = [_reading(257000001, m, 10.0) for m in range(0, 30, 10)] + [
        _reading(257000001, m, 0.0) for m in range(30, 60, 10)
    ]
    # Ship B: same shape, but its last sighting is old (simulating a ship
    # that has gone quiet, not one we still believe is in port).
    rows_b = [_reading(257000002, m, 10.0, timedelta(days=-10)) for m in range(0, 30, 10)] + [
        _reading(257000002, m, 0.0, timedelta(days=-10)) for m in range(30, 60, 10)
    ]
    insert_ais_snapshots([(datetime(2026, 9, 26, 11, 0), rows_a + rows_b)], db_path=db_path)
    run_silver_transform(db_path=db_path)
    run_state_periods_transform(db_path=db_path)
    run_port_calls_transform(db_path=db_path)

    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE fact_port_call AS "
        "SELECT *, NULL AS port_locode, 'port_call' AS visit_type, NULL AS berth_name, "
        "NULL AS anchorage_name, "
        "NULL AS nearest_port_km, NULL AS stop_latitude, NULL AS stop_longitude "
        "FROM port_call_events"
    )
    conn.execute(
        "CREATE TABLE dim_vessel AS "
        "SELECT DISTINCT mmsi, name AS vessel_name, 'cargo' AS ship_category "
        "FROM ais_messages_silver"
    )
    conn.execute(
        "CREATE TABLE dim_port "
        "(port_locode VARCHAR, port_name VARCHAR, latitude DOUBLE, longitude DOUBLE)"
    )
    conn.close()

    query, recency_hours = _load_query()
    assert recency_hours >= 1

    conn = duckdb.connect(str(db_path))
    result = conn.sql(query).df().set_index("mmsi")["in_port_now"]
    conn.close()

    assert (
        bool(result[257000001]) is True
    ), "recently-seen, departure-unobserved visit should read in_port_now"
    assert (
        bool(result[257000002]) is False
    ), "a visit last seen long ago should not read in_port_now"

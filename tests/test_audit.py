"""Tests for the data-quality audit: every check must run on a real warehouse."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from HarbourOS.audit import CHECKS, run_audit
from HarbourOS.storage import initialize_bronze_table, insert_ais_snapshots
from HarbourOS.transform import (
    run_port_calls_transform,
    run_silver_transform,
    run_state_periods_transform,
)


def _reading(minute: int, speed: float, lat: float = 60.39, lon: float = 5.32) -> dict:
    start = datetime(2026, 9, 26, 8, 0)
    return {
        "mmsi": 257898600,
        "name": "TEST SHIP",
        "latitude": lat,
        "longitude": lon,
        "speedOverGround": speed,
        "courseOverGround": 90.0,
        "trueHeading": 90.0,
        "rateOfTurn": 0.0,
        "shipType": 70,
        "navigationalStatus": 5 if speed < 0.5 else 0,
        "stream": "terra",
        "msgtime": (start + timedelta(minutes=minute)).isoformat(),
    }


def _build_warehouse(db_path: Path) -> None:
    """Bronze to port calls through the real transforms, plus a stand-in Gold.

    dbt isn't run here (its profile points at MotherDuck), so the Gold tables
    the audit reads are recreated with the columns the dbt models produce.
    """
    initialize_bronze_table(db_path=db_path)
    # Sail in, sit in Bergen for an hour, sail out; then one teleporting reading.
    readings = (
        [_reading(m, 10.0) for m in range(0, 30, 10)]
        + [_reading(m, 0.0) for m in range(30, 100, 10)]
        + [_reading(m, 10.0) for m in range(100, 130, 10)]
        + [_reading(131, 10.0, lat=70.0, lon=25.0)]
    )
    received = datetime(2026, 9, 26, 11, 0)
    insert_ais_snapshots([(received, readings)], db_path=db_path)
    run_silver_transform(db_path=db_path)
    run_state_periods_transform(db_path=db_path)
    run_port_calls_transform(db_path=db_path)

    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE dim_port AS
        SELECT 'NOBGO' AS port_locode, 'Bergen' AS port_name, 60.4 AS latitude, 5.32 AS longitude
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_vessel AS
        SELECT mmsi, arg_max(name, message_time) AS vessel_name,
               arg_max(ship_type, message_time) AS ship_type
        FROM ais_messages_silver GROUP BY mmsi
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_port_call AS
        SELECT e.*, 'NOBGO' AS port_locode, 1.1 AS nearest_port_km,
               60.39 AS stop_latitude, 5.32 AS stop_longitude
        FROM port_call_events AS e
        """
    )
    conn.close()


def test_every_check_runs_on_a_real_warehouse(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.duckdb"
    _build_warehouse(db_path)

    report = run_audit(db_path)

    assert "Check failed" not in report
    assert report.count("### ") == len(CHECKS)


def test_the_audit_sees_the_port_call_and_the_teleport(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.duckdb"
    _build_warehouse(db_path)

    report = run_audit(db_path)

    assert "| Port calls (Gold fact) | 1 |" in report
    assert "| Bergen | NOBGO | 1 | 1.1 |" in report
    # One jump of ~1,100 km in a minute, on one ship.
    assert "| 1 | 1 | 7.692 |" in report


def test_a_broken_check_does_not_hide_the_rest(tmp_path: Path) -> None:
    # An empty warehouse: every check fails, and each failure is reported.
    db_path = tmp_path / "empty.duckdb"
    duckdb.connect(str(db_path)).close()

    report = run_audit(db_path)

    assert report.count("Check failed") == len(CHECKS)


def test_checks_only_read() -> None:
    for _, title, _, sql in CHECKS:
        words = sql.upper().split()
        assert words[0] in ("SELECT", "WITH"), title
        for verb in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "COPY"):
            assert verb not in words, f"{title} contains {verb}"

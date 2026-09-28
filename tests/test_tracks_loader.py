"""Tests for the dashboard's route loader (dashboard/src/data/tracks.parquet.py).

The loader is run the way Observable runs it: as a subprocess whose stdout
becomes data/tracks.parquet.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

LOADER_PATH = Path(__file__).parent.parent / "dashboard" / "src" / "data" / "tracks.parquet.py"
START = datetime(2026, 9, 28, 8, 0)


def _run_loader(tmp_path: Path, slots: list[tuple[int, int, float, float]]) -> list[tuple]:
    """slots: (mmsi, minutes after START, latitude, longitude)."""
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE fct_vessel_track (mmsi INTEGER, slot_start TIMESTAMP, "
        "message_time TIMESTAMP, latitude DOUBLE, longitude DOUBLE, "
        "speed_over_ground DOUBLE, course_over_ground DOUBLE)"
    )
    for mmsi, minute, lat, lon in slots:
        t = START + timedelta(minutes=minute)
        conn.execute(
            "INSERT INTO fct_vessel_track VALUES (?, ?, ?, ?, ?, 0, 0)",
            [mmsi, t, t, lat, lon],
        )
    conn.close()

    out = tmp_path / "tracks.parquet"
    with out.open("wb") as f:
        subprocess.run(
            [sys.executable, str(LOADER_PATH)],
            env={**os.environ, "HARBOUROS_DB": str(db_path)},
            stdout=f,
            check=True,
        )
    return duckdb.sql(f"select mmsi, t, after_gap from '{out}' order by mmsi, t").fetchall()


def _minute(row: tuple) -> int:
    return int((row[1] - START.replace(tzinfo=UTC).timestamp()) // 60)


def test_a_ship_lying_still_keeps_only_its_first_and_last_point(tmp_path: Path) -> None:
    rows = _run_loader(tmp_path, [(1, m, 60.39, 5.32) for m in range(0, 120, 10)])

    assert [_minute(r) for r in rows] == [0, 110]


def test_a_moving_ship_keeps_every_point(tmp_path: Path) -> None:
    rows = _run_loader(tmp_path, [(1, m, 60.0 + m / 100, 5.0) for m in range(0, 60, 10)])

    assert [_minute(r) for r in rows] == [0, 10, 20, 30, 40, 50]


def test_the_points_either_side_of_a_gap_are_kept_and_marked(tmp_path: Path) -> None:
    minutes = [0, 10, 20, 30, 120, 130, 140]
    rows = _run_loader(tmp_path, [(1, m, 60.39, 5.32) for m in minutes])

    assert [_minute(r) for r in rows] == [0, 30, 120, 140]
    assert [r[2] for r in rows] == [True, False, True, False]

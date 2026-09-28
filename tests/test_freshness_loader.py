"""Tests for the dashboard's freshness loader (dashboard/src/data/freshness.json.py).

The loader is a script, so it is run the way Observable runs it: as a
subprocess whose stdout becomes data/freshness.json.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

LOADER_PATH = Path(__file__).parent.parent / "dashboard" / "src" / "data" / "freshness.json.py"


def _run_loader(db_path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(LOADER_PATH)],
        env={**os.environ, "HARBOUROS_DB": str(db_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_latest_reading_is_the_newest_silver_timestamp_in_utc(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE ais_messages_silver (message_time TIMESTAMP)")
    conn.execute(
        "INSERT INTO ais_messages_silver VALUES "
        "('2026-09-28 11:40:00'), ('2026-09-28 11:50:02'), ('2026-09-27 08:00:00')"
    )
    conn.close()

    freshness = _run_loader(db_path)

    assert freshness["latest_reading"] == "2026-09-28T11:50:02+00:00"
    assert freshness["built_at"].endswith("+00:00")


def test_an_empty_warehouse_reports_no_reading(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE ais_messages_silver (message_time TIMESTAMP)")
    conn.close()

    assert _run_loader(db_path)["latest_reading"] is None

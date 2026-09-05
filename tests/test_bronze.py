"""Tests for the Bronze layer storage functions."""

from pathlib import Path

import duckdb
import pytest
from HarbourOS.storage import initialize_bronze_table, insert_ais_message


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """A temporary, throwaway database file -- unique per test, deleted automatically after."""
    return tmp_path / "test_bronze.duckdb"


def test_initialize_bronze_table_creates_table(test_db_path):
    initialize_bronze_table(db_path=test_db_path)

    conn = duckdb.connect(str(test_db_path))
    tables = [row[0] for row in conn.sql("SHOW TABLES").fetchall()]
    conn.close()

    assert "ais_messages_bronze" in tables


def test_initialize_bronze_table_has_expected_columns(test_db_path):
    initialize_bronze_table(db_path=test_db_path)

    conn = duckdb.connect(str(test_db_path))
    columns = [row[0] for row in conn.sql("DESCRIBE ais_messages_bronze").fetchall()]
    conn.close()

    assert "mmsi" in columns
    assert "latitude" in columns
    assert "msgtime" in columns


def test_insert_ais_message_stores_a_row(test_db_path):
    initialize_bronze_table(db_path=test_db_path)

    sample_message = {
        "mmsi": 257898600,
        "name": "TEST SHIP",
        "latitude": 60.0,
        "longitude": 5.0,
        "speedOverGround": 10.0,
        "courseOverGround": 90.0,
        "trueHeading": 90.0,
        "rateOfTurn": 0.0,
        "shipType": 70,
        "navigationalStatus": 0,
        "stream": "terra",
        "msgtime": "2026-08-29 12:00:00",
    }
    insert_ais_message(sample_message, db_path=test_db_path)

    conn = duckdb.connect(str(test_db_path))
    count = conn.sql("SELECT COUNT(*) FROM ais_messages_bronze").fetchone()[0]
    conn.close()

    assert count == 1


def test_insert_ais_message_handles_missing_optional_fields(test_db_path):
    """Real AIS messages don't always report every field -- missing ones
    should become NULL, not crash the pipeline."""
    initialize_bronze_table(db_path=test_db_path)

    minimal_message = {
        "mmsi": 257898600,
        "msgtime": "2026-08-29 12:00:00",
    }
    insert_ais_message(minimal_message, db_path=test_db_path)

    conn = duckdb.connect(str(test_db_path))
    row = conn.sql("SELECT mmsi, name, latitude FROM ais_messages_bronze").fetchone()
    conn.close()

    assert row[0] == 257898600
    assert row[1] is None
    assert row[2] is None

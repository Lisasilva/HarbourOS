"""Tests for the Silver layer: deduplication, validation, and quarantine tagging."""

from pathlib import Path

import duckdb
import pytest
from HarbourOS.storage import initialize_bronze_table, insert_ais_message
from HarbourOS.transform import run_silver_transform


def query(db_path: Path, sql: str) -> list:
    """Run a read-only query against a test database and return all rows."""
    conn = duckdb.connect(str(db_path))
    rows = conn.sql(sql).fetchall()
    conn.close()
    return rows


def count(db_path: Path, table: str) -> int:
    """Row count for a single table."""
    return query(db_path, f"SELECT COUNT(*) FROM {table}")[0][0]


def make_message(**overrides) -> dict:
    """A valid AIS message. Pass keyword arguments to break specific fields."""
    message = {
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
    message.update(overrides)
    return message


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """A throwaway Bronze table seeded with known-good and known-bad rows.

    2 rows should survive into Silver; 5 should be quarantined.
    """
    db_path = tmp_path / "test_silver.duckdb"
    initialize_bronze_table(db_path=db_path)

    rows = [
        make_message(mmsi=257898600, name="GOOD SHIP A"),
        make_message(
            mmsi=259000000,
            name="GOOD SHIP B",
            latitude=59.9,
            longitude=10.7,
            speedOverGround=0.0,
        ),
        # Exact duplicate of GOOD SHIP A -- same mmsi, same msgtime.
        make_message(mmsi=257898600, name="GOOD SHIP A"),
        make_message(mmsi=257000001, name="BAD LATITUDE", latitude=999.0),
        make_message(mmsi=257000002, name="BAD SPEED", speedOverGround=500.0),
        # Mirrors the real test transponder found in live BarentsWatch data.
        make_message(mmsi=1111, name="TEST TRANSPONDER"),
        make_message(mmsi=257000003, name="FUTURE CLOCK", msgtime="2099-01-01 00:00:00"),
    ]
    for row in rows:
        insert_ais_message(row, db_path=db_path)

    return db_path


def test_valid_rows_land_in_silver(seeded_db):
    run_silver_transform(db_path=seeded_db)
    rows = query(seeded_db, "SELECT mmsi FROM ais_messages_silver")
    assert sorted(row[0] for row in rows) == [257898600, 259000000]


def test_every_bronze_row_is_accounted_for(seeded_db):
    """The core data-quality guarantee: nothing is ever silently dropped."""
    run_silver_transform(db_path=seeded_db)
    bronze = count(seeded_db, "ais_messages_bronze")
    silver = count(seeded_db, "ais_messages_silver")
    quarantine = count(seeded_db, "ais_messages_quarantine")
    assert silver + quarantine == bronze


def test_duplicate_message_is_deduplicated(seeded_db):
    run_silver_transform(db_path=seeded_db)
    kept = count(seeded_db, "ais_messages_silver")
    assert (
        query(
            seeded_db,
            "SELECT COUNT(*) FROM ais_messages_silver WHERE mmsi = 257898600",
        )[
            0
        ][0]
        == 1
    )
    assert (
        query(
            seeded_db,
            "SELECT COUNT(*) FROM ais_messages_quarantine " "WHERE rejection_reason = 'duplicate'",
        )[0][0]
        == 1
    )
    assert kept == 2


def test_each_invalid_row_gets_the_correct_rejection_reason(seeded_db):
    run_silver_transform(db_path=seeded_db)
    reasons = dict(
        query(
            seeded_db,
            "SELECT mmsi, rejection_reason FROM ais_messages_quarantine "
            "WHERE rejection_reason != 'duplicate'",
        )
    )
    assert reasons == {
        257000001: "invalid_latitude",
        257000002: "invalid_speed",
        1111: "invalid_mmsi_range",
        257000003: "future_timestamp",
    }


def test_silver_columns_are_standardized_to_snake_case(seeded_db):
    run_silver_transform(db_path=seeded_db)
    columns = [row[0] for row in query(seeded_db, "DESCRIBE ais_messages_silver")]
    assert "speed_over_ground" in columns
    assert "message_time" in columns
    assert "speedOverGround" not in columns


def test_row_with_missing_optional_fields_is_not_silently_dropped(tmp_path: Path):
    """Real AIS messages don't always report speed or position.

    Such a row must still land in exactly one of Silver or Quarantine --
    it may be rejected, but it may not vanish.
    """
    db_path = tmp_path / "sparse.duckdb"
    initialize_bronze_table(db_path=db_path)
    insert_ais_message({"mmsi": 257898600, "msgtime": "2026-08-29 12:00:00"}, db_path=db_path)

    run_silver_transform(db_path=db_path)

    silver = count(db_path, "ais_messages_silver")
    quarantine = count(db_path, "ais_messages_quarantine")
    assert silver + quarantine == 1

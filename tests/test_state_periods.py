"""End-to-end: Silver readings become rows in the state-period table."""
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from HarbourOS.storage import initialize_bronze_table, insert_ais_message
from HarbourOS.transform import run_silver_transform, run_state_periods_transform

START = datetime(2026, 9, 4, 8, 0)


@pytest.fixture
def journey_db(tmp_path: Path) -> Path:
    """A throwaway database holding one ship's complete arrival journey."""
    db_path = tmp_path / "state_periods.duckdb"
    initialize_bronze_table(db_path=db_path)

    minute = 0
    for count, speed, status in [(10, 12.0, 0), (5, 1.5, 0), (10, 0.0, 5)]:
        for _ in range(count):
            insert_ais_message(
                {
                    "mmsi": 257000000,
                    "name": "TEST SHIP",
                    "latitude": 60.0,
                    "longitude": 5.0,
                    "speedOverGround": speed,
                    "navigationalStatus": status,
                    "msgtime": (START + timedelta(minutes=minute)).isoformat(sep=" "),
                },
                db_path=db_path,
            )
            minute += 1

    return db_path


def test_state_periods_table_records_the_journey(journey_db):
    run_silver_transform(db_path=journey_db)
    run_state_periods_transform(db_path=journey_db)

    conn = duckdb.connect(str(journey_db))
    states = [
        row[0]
        for row in conn.sql("SELECT state FROM ship_state_periods ORDER BY start_time").fetchall()
    ]
    conn.close()

    assert states == ["at_sea", "approach", "berthed"]


def test_every_silver_reading_lands_in_a_state_period(journey_db):
    """Same guarantee as Bronze -> Silver: nothing vanishes."""
    run_silver_transform(db_path=journey_db)
    run_state_periods_transform(db_path=journey_db)

    conn = duckdb.connect(str(journey_db))
    silver = conn.sql("SELECT COUNT(*) FROM ais_messages_silver").fetchone()[0]
    covered = conn.sql("SELECT SUM(n_readings) FROM ship_state_periods").fetchone()[0]
    conn.close()

    assert covered == silver

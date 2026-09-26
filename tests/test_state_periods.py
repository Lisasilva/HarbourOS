"""End-to-end: Silver readings become rows in the state-period table."""

import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from HarbourOS.storage import initialize_bronze_table, insert_ais_message, insert_ais_messages
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


def _random_track(rng: random.Random, n: int) -> list[dict]:
    """One ship's readings: jittery speeds, sometimes long silences."""
    time = START
    readings = []
    for _ in range(n):
        time += timedelta(minutes=rng.choice([1, 2, 5, 10, 10, 10, 45, 300]))
        speed, status = rng.choice(
            [(12.0, 0), (11.0, 5), (1.5, 0), (0.0, 5), (0.0, 1), (0.1, 0), (0.0, None)]
        )
        readings.append(
            {
                "mmsi": 257000001,
                "name": "RANDOM SHIP",
                "latitude": 60.0,
                "longitude": 5.0,
                "speedOverGround": speed,
                "navigationalStatus": status,
                "msgtime": time.isoformat(sep=" "),
            }
        )
    return readings


def _stored_periods(db_path: Path) -> list[tuple]:
    conn = duckdb.connect(str(db_path))
    rows = conn.sql(
        "SELECT state, start_time, end_time, n_readings, confidence, note "
        "FROM ship_state_periods ORDER BY start_time"
    ).fetchall()
    conn.close()
    return rows


@pytest.mark.parametrize("seed", range(12))
def test_rebuilding_only_the_tail_matches_a_full_rebuild(tmp_path: Path, seed: int):
    """Feeding readings in many small runs must give exactly the periods that
    one run over the whole history gives -- including when a late batch
    carries readings older than ones already processed."""
    rng = random.Random(seed)
    track = _random_track(rng, 120)

    batches: list[list[dict]] = []
    remaining = list(track)
    while remaining:
        size = rng.randint(1, 15)
        batches.append(remaining[:size])
        remaining = remaining[size:]
    late = rng.randrange(len(batches))
    batches.append(batches.pop(late))  # a backfill arriving after newer data

    incremental = tmp_path / "incremental.duckdb"
    initialize_bronze_table(db_path=incremental)
    for batch in batches:
        insert_ais_messages(batch, db_path=incremental)
        run_silver_transform(db_path=incremental)
        run_state_periods_transform(db_path=incremental)

    full = tmp_path / "full.duckdb"
    initialize_bronze_table(db_path=full)
    insert_ais_messages(track, db_path=full)
    run_silver_transform(db_path=full)
    run_state_periods_transform(db_path=full)

    assert _stored_periods(incremental) == _stored_periods(full)


def test_a_rebuilt_tail_still_knows_the_ship_was_berthed_before(tmp_path: Path):
    """A slow stretch after berthing is 'departed' -- even when the rebuild
    starts at that stretch and the berthed period lies outside it."""
    db_path = tmp_path / "departure.duckdb"
    initialize_bronze_table(db_path=db_path)

    def readings(first_minute: int, count: int, speed: float, status: int) -> list[dict]:
        return [
            {
                "mmsi": 257000002,
                "name": "LEAVING SHIP",
                "latitude": 60.0,
                "longitude": 5.0,
                "speedOverGround": speed,
                "navigationalStatus": status,
                "msgtime": (START + timedelta(minutes=first_minute + i)).isoformat(sep=" "),
            }
            for i in range(count)
        ]

    insert_ais_messages(readings(0, 5, 0.0, 5) + readings(5, 5, 1.5, 0), db_path=db_path)
    run_silver_transform(db_path=db_path)
    run_state_periods_transform(db_path=db_path)

    insert_ais_messages(readings(10, 3, 1.5, 0), db_path=db_path)
    run_silver_transform(db_path=db_path)
    run_state_periods_transform(db_path=db_path)

    assert [row[0] for row in _stored_periods(db_path)] == ["berthed", "departed"]

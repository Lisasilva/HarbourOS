"""Tests for the steady-beat collector and its single-upload Bronze write."""

from datetime import datetime
from pathlib import Path

import duckdb

from HarbourOS.collect import collect
from HarbourOS.storage import initialize_bronze_table, insert_ais_snapshots


class FakeClock:
    """Time that only moves when the collector sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def position(mmsi: int, msgtime: str) -> dict:
    return {"mmsi": mmsi, "latitude": 60.0, "longitude": 5.0, "msgtime": msgtime}


def test_polls_on_a_steady_beat_until_time_is_up():
    clock = FakeClock()
    polls = []

    def fetch() -> list[dict]:
        polls.append(clock.now)
        return [position(257000000, f"2026-09-26 08:{len(polls):02d}:00")]

    snapshots = collect(minutes=30, every_minutes=10, fetch=fetch, sleep=clock.sleep, clock=clock)

    assert polls == [0.0, 600.0, 1200.0]
    assert len(snapshots) == 3


def test_repeats_of_an_unchanged_ship_are_dropped():
    clock = FakeClock()
    answers = iter(
        [
            [position(1, "2026-09-26 08:00:00"), position(2, "2026-09-26 08:00:00")],
            [position(1, "2026-09-26 08:00:00"), position(2, "2026-09-26 08:10:00")],
        ]
    )

    snapshots = collect(
        minutes=20, every_minutes=10, fetch=lambda: next(answers), sleep=clock.sleep, clock=clock
    )

    assert [len(messages) for _, messages in snapshots] == [2, 1]
    assert snapshots[1][1][0]["mmsi"] == 2


def test_a_failed_poll_does_not_lose_the_others():
    clock = FakeClock()
    calls = iter([RuntimeError("token expired"), [position(1, "2026-09-26 08:10:00")]])

    def fetch() -> list[dict]:
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    snapshots = collect(minutes=20, every_minutes=10, fetch=fetch, sleep=clock.sleep, clock=clock)

    assert [len(messages) for _, messages in snapshots] == [1]


def test_snapshots_land_in_bronze_with_one_received_at_per_poll(tmp_path: Path):
    db_path = tmp_path / "bronze.duckdb"
    initialize_bronze_table(db_path=db_path)
    first, second = datetime(2026, 9, 26, 8, 0), datetime(2026, 9, 26, 8, 10)

    stored = insert_ais_snapshots(
        [
            (first, [position(1, "2026-09-26T07:59:00+00:00"), position(2, "2026-09-26 07:58:00")]),
            (second, [position(1, "2026-09-26 08:09:00")]),
        ],
        db_path=db_path,
    )

    conn = duckdb.connect(str(db_path))
    rows = conn.sql(
        "SELECT mmsi, msgtime, received_at FROM ais_messages_bronze ORDER BY received_at, mmsi"
    ).fetchall()
    conn.close()

    assert stored == 3
    assert rows == [
        (1, datetime(2026, 9, 26, 7, 59), first),
        (2, datetime(2026, 9, 26, 7, 58), first),
        (1, datetime(2026, 9, 26, 8, 9), second),
    ]


def test_nothing_collected_writes_nothing(tmp_path: Path):
    assert insert_ais_snapshots([], db_path=tmp_path / "unused.duckdb") == 0

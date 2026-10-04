"""Unusual behaviour: plant fake oddities in a normal week and check they're caught."""

import math
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from HarbourOS.anomalies import dashboard_summary, find_anomalies, hours_text, save

START = datetime(2026, 9, 25)
DAYS = 9
LATEST = START + timedelta(days=DAYS) - timedelta(minutes=10)
# Normal cargo ships.
FLEET = range(257_000_001, 257_000_041)
BERGEN = (60.3930, 5.3242)
OIL_FIELD = (60.8, 3.5)
OPEN_SEA = (62.5, 2.0)
# The planted oddities.
LONG_STAYER = 257_900_001
LONE_STOPPER = 257_900_002
FAST_SHIP = 257_900_003
LONE_FISHER = 257_900_004


def _call(mmsi, visit_type, start, minutes, at, locode=None, completeness="complete", km=0.5):
    return {
        "mmsi": mmsi,
        "port_locode": locode,
        "visit_type": visit_type,
        "completeness": completeness,
        "minutes_alongside": minutes,
        "berth_start": start,
        "berth_end": start + timedelta(minutes=minutes),
        "stop_latitude": at[0],
        "stop_longitude": at[1],
        "nearest_port_km": km,
    }


def _track(mmsi: int, rng: np.random.Generator, knots: float, moving: float) -> list[dict]:
    """A ship sailing back and forth along a line, every 10 minutes for the whole period."""
    rows = []
    lat, lon = 61.0 + rng.uniform(-1, 1), 4.5 + rng.uniform(-0.5, 0.5)
    heading = 1.0
    for slot in range(DAYS * 144):
        moving_now = (slot % 144) / 144 < moving
        speed = knots * rng.uniform(0.9, 1.1) if moving_now else rng.uniform(0, 0.3)
        lat += heading * speed * 1.852 / 6 / 111.32
        if slot % 36 == 0:
            heading = -heading
        rows.append(
            {
                "mmsi": mmsi,
                "message_time": START + timedelta(minutes=10 * slot),
                "latitude": lat,
                "longitude": lon,
                "speed_over_ground": speed,
            }
        )
    return rows


@pytest.fixture()
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    rng = np.random.default_rng(7)
    vessels = [(m, f"Cargo {m}", "cargo") for m in FLEET]
    vessels += [(LONG_STAYER, "Long Stayer", "cargo"), (LONE_STOPPER, "Lone Stopper", "cargo")]
    vessels += [(FAST_SHIP, "Fast Ship", "cargo"), (LONE_FISHER, "Lone Fisher", "fishing")]

    calls = []
    # A normal week in Bergen: cargo ships stay about 7 hours.
    for i in range(60):
        start = START + timedelta(hours=3 * i)
        minutes = round(420 * math.exp(rng.normal(0, 0.3)))
        calls.append(_call(FLEET[i % 40], "port_call", start, minutes, BERGEN, "NOBGO"))
    # ... and one that stays five days.
    calls.append(
        _call(LONG_STAYER, "port_call", START + timedelta(days=2), 5 * 1440, BERGEN, "NOBGO")
    )
    # Many ships wait at the same oil field.
    for i in range(12):
        spot = (OIL_FIELD[0] + rng.uniform(-0.01, 0.01), OIL_FIELD[1] + rng.uniform(-0.01, 0.01))
        calls.append(_call(FLEET[i], "at_sea", START + timedelta(hours=10 * i), 240, spot, km=60))
    # One cargo ship stops alone in open sea; a fishing boat does the same.
    calls.append(_call(LONE_STOPPER, "at_sea", START + timedelta(days=5), 180, OPEN_SEA, km=150))
    calls.append(_call(LONE_FISHER, "at_sea", START + timedelta(days=5), 180, (63.5, 3.0), km=150))

    track = []
    for m in FLEET:
        track += _track(m, rng, knots=rng.uniform(10, 15), moving=rng.uniform(0.3, 0.7))
    track += _track(FAST_SHIP, rng, knots=12, moving=0.5)
    # On day 4 the fast ship reports 65 knots, and its positions jump to match.
    for row in track:
        if row["mmsi"] == FAST_SHIP and START + timedelta(days=4, hours=6) <= row[
            "message_time"
        ] < START + timedelta(days=4, hours=8):
            row["speed_over_ground"] = 65.0
            row["latitude"] += 0.2 * (row["message_time"].minute // 10)

    db = duckdb.connect(str(tmp_path / "gold.duckdb"))
    frames = {
        "fact_port_call": pd.DataFrame(calls),
        "dim_vessel": pd.DataFrame(vessels, columns=["mmsi", "vessel_name", "ship_category"]),
        "dim_port": pd.DataFrame([("NOBGO", "Bergen")], columns=["port_locode", "port_name"]),
        "fct_vessel_track": pd.DataFrame(track),
    }
    for name, frame in frames.items():
        db.register("frame", frame)
        db.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
        db.unregister("frame")
    return db


def test_the_planted_oddities_are_caught(conn: duckdb.DuckDBPyConnection) -> None:
    flags = find_anomalies(conn)
    caught = set(zip(flags["mmsi"], flags["kind"], strict=True))

    assert (LONG_STAYER, "long_stay") in caught
    assert (LONE_STOPPER, "odd_stop") in caught
    assert (FAST_SHIP, "impossible_jump") in caught
    assert (FAST_SHIP, "unusual_day") in caught


def test_normal_behaviour_is_left_alone(conn: duckdb.DuckDBPyConnection) -> None:
    flags = find_anomalies(conn)

    # Fishing boats stop at sea for a living; ships at a shared oil field are normal.
    assert LONE_FISHER not in set(flags["mmsi"])
    assert set(flags[flags["kind"] == "odd_stop"]["mmsi"]) == {LONE_STOPPER}
    assert set(flags[flags["kind"].isin(["long_stay", "short_stay"])]["mmsi"]) == {LONG_STAYER}
    assert set(flags[flags["kind"] == "impossible_jump"]["mmsi"]) == {FAST_SHIP}


def test_reasons_are_plain_sentences(conn: duckdb.DuckDBPyConnection) -> None:
    flags = find_anomalies(conn).set_index(["mmsi", "kind"])

    long_stay = flags.loc[(LONG_STAYER, "long_stay"), "reason"]
    assert long_stay.startswith("Stayed 5.0 days in Bergen; cargo ships at Bergen usually stay")
    assert "open sea" in flags.loc[(LONE_STOPPER, "odd_stop"), "reason"]
    assert "knots" in flags.loc[(FAST_SHIP, "impossible_jump"), "reason"]
    assert flags.loc[(FAST_SHIP, "unusual_day"), "reason"].startswith("Reached 65 knots")


def test_the_dashboard_summary_names_the_ships(conn: duckdb.DuckDBPyConnection) -> None:
    save(conn, find_anomalies(conn), "vessel_anomalies")
    summary = dashboard_summary(conn)

    assert summary["counts"]["long_stay"] == 1
    assert "Long Stayer" in {flag["ship"] for flag in summary["flags"]}


def test_no_table_yet_means_an_empty_summary(tmp_path: Path) -> None:
    assert dashboard_summary(duckdb.connect(str(tmp_path / "empty.duckdb"))) == {
        "counts": {},
        "flags": [],
    }


def test_hours_text() -> None:
    assert hours_text(25) == "25 minutes"
    assert hours_text(420) == "7 hours"
    assert hours_text(5 * 1440) == "5.0 days"


def test_a_ships_own_routine_is_not_unusual(conn: duckdb.DuckDBPyConnection) -> None:
    # The long stayer turns out to stay five days every time.
    for week in range(1, 4):
        conn.execute(
            """
            INSERT INTO fact_port_call
            SELECT * REPLACE (berth_start - INTERVAL (? * 7) DAY AS berth_start,
                              berth_end - INTERVAL (? * 7) DAY AS berth_end)
            FROM fact_port_call WHERE mmsi = ? AND visit_type = 'port_call'
            LIMIT 1
            """,
            [week, week, LONG_STAYER],
        )
    flags = find_anomalies(conn)

    assert LONG_STAYER not in set(flags[flags["kind"] == "long_stay"]["mmsi"])


def test_aircraft_and_beacons_are_not_ships(conn: duckdb.DuckDBPyConnection) -> None:
    # A search-and-rescue helicopter (MMSI 111...) flies at 120 knots.
    conn.execute("UPDATE fct_vessel_track SET mmsi = 111257001 WHERE mmsi = ?", [FAST_SHIP])
    assert 111257001 not in set(find_anomalies(conn)["mmsi"])

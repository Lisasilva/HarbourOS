"""The accuracy-check sample: random, repeatable, with each ship's nearby track."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from HarbourOS.accuracy_sample import draw_sample, render_page

START = datetime(2026, 9, 30, 8, 0)


def warehouse(tmp_path: Path, calls: int) -> Path:
    db_path = tmp_path / "gold.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE fact_port_call (
            port_call_key VARCHAR, mmsi INTEGER, port_locode VARCHAR, visit_type VARCHAR,
            nearest_port_km DOUBLE, arrival_time TIMESTAMP, berth_start TIMESTAMP,
            berth_end TIMESTAMP, departure_time TIMESTAMP, minutes_alongside INTEGER,
            completeness VARCHAR, confidence DOUBLE
        );
        CREATE TABLE dim_vessel (mmsi INTEGER, vessel_name VARCHAR, ship_category VARCHAR);
        CREATE TABLE dim_port (port_locode VARCHAR, port_name VARCHAR,
                               latitude DOUBLE, longitude DOUBLE);
        CREATE TABLE ais_messages_silver (mmsi INTEGER, latitude DECIMAL(10, 6),
            longitude DECIMAL(10, 6), speed_over_ground DECIMAL(10, 2), message_time TIMESTAMP);
        INSERT INTO dim_port VALUES ('NOBGO', 'Bergen', 60.39, 5.32);
        """
    )
    for n in range(calls):
        mmsi = 257000000 + n
        berth_start = START + timedelta(hours=n)
        berth_end = berth_start + timedelta(hours=2)
        conn.execute(
            "INSERT INTO fact_port_call VALUES (?, ?, 'NOBGO', 'port_call', 0.4, ?, ?, ?, ?,"
            " 120, 'complete', 0.9)",
            [f"key{n:03d}", mmsi, berth_start, berth_start, berth_end, berth_end],
        )
        conn.execute("INSERT INTO dim_vessel VALUES (?, ?, 'Cargo')", [mmsi, f"SHIP {n}"])
        for minutes in (-300, -60, 30, 90, 180, 600):
            conn.execute(
                "INSERT INTO ais_messages_silver VALUES (?, 60.39, 5.32, 0.1, ?)",
                [mmsi, berth_start + timedelta(minutes=minutes)],
            )
    conn.execute(
        "INSERT INTO fact_port_call VALUES ('sea', 1, NULL, 'at_sea', 40, ?, ?, ?, ?, 60,"
        " 'complete', 0.5)",
        [START, START, START, START],
    )
    conn.close()
    return db_path


def test_sample_is_random_repeatable_and_skips_stops_at_sea(tmp_path: Path):
    db_path = warehouse(tmp_path, calls=30)

    first = draw_sample(10, seed=7, db_path=db_path)
    again = draw_sample(10, seed=7, db_path=db_path)
    other = draw_sample(10, seed=8, db_path=db_path)

    assert len(first) == 10
    assert [v["key"] for v in first] == [v["key"] for v in again]
    assert [v["key"] for v in first] != [v["key"] for v in other]
    assert "sea" not in {v["key"] for v in first}


def test_track_covers_three_hours_either_side_of_the_stop(tmp_path: Path):
    db_path = warehouse(tmp_path, calls=1)

    (visit,) = draw_sample(5, seed=1, db_path=db_path)

    # Readings at -60, +30, +90 and +180 min fall in the window (stop is 0-120
    # min, plus 3 h either side); -300 and +600 are outside it.
    assert [point[3] for point in visit["track"]] == [
        "2026-09-30 07:00",
        "2026-09-30 08:30",
        "2026-09-30 09:30",
        "2026-09-30 11:00",
    ]
    assert visit["port"] == "Bergen" and visit["ship"] == "SHIP 0"


def test_page_embeds_the_data_safely():
    visits = [{"n": 1, "key": "k", "ship": "</script><b>x</b>", "track": []}]

    page = render_page(visits, seed=42)

    assert "__VISITS__" not in page and "__SEED__" not in page
    assert "</script><b>" not in page
    assert "Sample seed 42" in page

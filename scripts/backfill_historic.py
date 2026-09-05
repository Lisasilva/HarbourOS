"""One-time backfill: pull 24-hour historic tracks for ships that actually move.

Live polling only captures one snapshot per run -- not enough to see a full
port-call journey. This pulls dense, real history so the Day 4-5 state
machine has something real to work with.
"""
from pathlib import Path

import duckdb
from HarbourOS.ingestion import fetch_historic_track
from HarbourOS.storage import insert_ais_message

DB_PATH = Path("data/ais_bronze.duckdb")


def get_moving_ships(min_speed: float = 5.0) -> list[int]:
    """MMSIs that have reported a speed above min_speed at least once --
    i.e. ships that actually sail, not ones sitting still the whole time
    we happened to see them."""
    conn = duckdb.connect(str(DB_PATH))
    rows = conn.sql(
        f"""
        SELECT mmsi
        FROM ais_messages_silver
        GROUP BY mmsi
        HAVING MAX(speed_over_ground) > {min_speed}
        """
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def backfill(mmsi_list: list[int]) -> None:
    total_points = 0
    for mmsi in mmsi_list:
        track = fetch_historic_track(mmsi)
        for point in track:
            insert_ais_message(point)
        print(f"MMSI {mmsi}: {len(track)} historic points stored")
        total_points += len(track)
    print(f"\n✅ Backfill complete: {total_points} points across {len(mmsi_list)} ships")


if __name__ == "__main__":
    ships = get_moving_ships()
    print(f"Backfilling {len(ships)} ships with prior movement...\n")
    backfill(ships)

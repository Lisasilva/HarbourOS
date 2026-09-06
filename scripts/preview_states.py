"""Print derived port-call states for a ship, to eyeball against real data."""
import sys
from pathlib import Path

import duckdb
from HarbourOS.state_machine import derive_state_periods

DB_PATH = Path("data/ais_bronze.duckdb")
SINCE = "2026-09-04 00:00:00"


def load_ship(mmsi: int) -> list[dict]:
    conn = duckdb.connect(str(DB_PATH))
    rows = conn.sql(
        f"""
        SELECT message_time, speed_over_ground, navigational_status
        FROM ais_messages_silver
        WHERE mmsi = {mmsi} AND message_time >= '{SINCE}'
        ORDER BY message_time
        """
    ).fetchall()
    conn.close()
    return [
        {
            "message_time": row[0],
            "speed_over_ground": row[1],
            "navigational_status": row[2],
        }
        for row in rows
    ]


def preview(mmsi: int) -> None:
    messages = load_ship(mmsi)
    periods = derive_state_periods(messages, mmsi=mmsi)

    print(f"\n=== MMSI {mmsi} ({len(messages)} readings) ===")
    print(f"{'state':<10} {'start':<20} {'duration':>9} {'reads':>6} {'conf':>5}  note")
    for period in periods:
        duration = period.end_time - period.start_time
        print(
            f"{period.state:<10} "
            f"{period.start_time.strftime('%Y-%m-%d %H:%M'):<20} "
            f"{str(duration):>9} "
            f"{period.n_readings:>6} "
            f"{period.confidence:>5}  "
            f"{period.note}"
        )


if __name__ == "__main__":
    ships = [int(arg) for arg in sys.argv[1:]] or [630001042, 257069200]
    for ship in ships:
        preview(ship)

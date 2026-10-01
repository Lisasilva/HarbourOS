"""Build a page for checking a random sample of port calls by eye.

    uv run python -m HarbourOS.accuracy_sample --size 100 --out accuracy-check.html

Each sampled port call is shown on a map with the ship's GPS track from three
hours before the stop to three hours after, the matched port and the times the
pipeline worked out. A person answers "Correct", "Wrong" or "Can't tell" for
each, and the page downloads the answers as a CSV. That gives an accuracy
figure checked by a human rather than one the pipeline gives itself.

The sample is drawn from the last week's port calls with a fixed seed, which
is printed, so the same sample can be drawn again. Read-only: nothing is
written to the warehouse.
"""

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

from HarbourOS.storage import DB_PATH, connect

TRACK_HOURS = 3

CANDIDATES = """
    SELECT
        f.port_call_key,
        f.mmsi,
        v.vessel_name,
        v.ship_category,
        p.port_name,
        f.port_locode,
        CAST(p.latitude AS DOUBLE) AS port_lat,
        CAST(p.longitude AS DOUBLE) AS port_lon,
        f.nearest_port_km,
        f.arrival_time,
        f.berth_start,
        f.berth_end,
        f.departure_time,
        f.minutes_alongside,
        f.completeness,
        f.confidence
    FROM fact_port_call f
    LEFT JOIN dim_vessel v ON v.mmsi = f.mmsi
    LEFT JOIN dim_port p ON p.port_locode = f.port_locode
    WHERE f.visit_type = 'port_call'
      AND f.berth_start >= (SELECT max(berth_start) FROM fact_port_call) - INTERVAL 7 DAY
    ORDER BY f.port_call_key
"""

TRACKS = f"""
    SELECT
        s.port_call_key,
        CAST(m.latitude AS DOUBLE),
        CAST(m.longitude AS DOUBLE),
        CAST(m.speed_over_ground AS DOUBLE),
        m.message_time
    FROM sample s
    JOIN ais_messages_silver m
      ON m.mmsi = s.mmsi
     AND m.message_time BETWEEN s.berth_start - INTERVAL {TRACK_HOURS} HOUR
                            AND s.berth_end + INTERVAL {TRACK_HOURS} HOUR
    ORDER BY s.port_call_key, m.message_time
"""


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else None


def draw_sample(size: int, seed: int, db_path: Path | str = DB_PATH) -> list[dict]:
    """Pick `size` port calls at random and attach each ship's nearby track."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(CANDIDATES)
        columns = [d[0] for d in cursor.description]
        candidates = [dict(zip(columns, row)) for row in cursor.fetchall()]
        chosen = random.Random(seed).sample(candidates, min(size, len(candidates)))

        conn.execute(
            "CREATE TEMP TABLE sample (port_call_key VARCHAR, mmsi INTEGER, "
            "berth_start TIMESTAMP, berth_end TIMESTAMP)"
        )
        if chosen:
            conn.executemany(
                "INSERT INTO sample VALUES (?, ?, ?, ?)",
                [[c["port_call_key"], c["mmsi"], c["berth_start"], c["berth_end"]] for c in chosen],
            )
        tracks: dict[str, list] = {}
        for key, lat, lon, speed, time in conn.execute(TRACKS).fetchall():
            tracks.setdefault(key, []).append([lat, lon, speed, _iso(time)])
    finally:
        conn.close()

    visits = []
    for number, call in enumerate(chosen, start=1):
        visits.append(
            {
                "n": number,
                "key": call["port_call_key"],
                "mmsi": call["mmsi"],
                "ship": call["vessel_name"] or f"MMSI {call['mmsi']}",
                "type": call["ship_category"],
                "port": call["port_name"],
                "locode": call["port_locode"],
                "port_lat": call["port_lat"],
                "port_lon": call["port_lon"],
                "port_km": call["nearest_port_km"],
                "arrival": _iso(call["arrival_time"]),
                "berth_start": _iso(call["berth_start"]),
                "berth_end": _iso(call["berth_end"]),
                "departure": _iso(call["departure_time"]),
                "minutes": call["minutes_alongside"],
                "completeness": call["completeness"],
                "confidence": call["confidence"],
                "track": tracks.get(call["port_call_key"], []),
            }
        )
    return visits


def render_page(visits: list[dict], seed: int) -> str:
    """A self-contained HTML page: one map per visit and a Correct/Wrong choice."""
    template = (Path(__file__).parent / "accuracy_check.html").read_text()
    # "</" inside the data would end the <script> block early.
    data = json.dumps(visits, default=float).replace("</", "<\\/")
    return template.replace("__SEED__", str(seed)).replace("__VISITS__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=None, help="default: today's date")
    parser.add_argument("--out", type=Path, default=Path("accuracy-check.html"))
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(datetime.now().strftime("%Y%m%d"))
    visits = draw_sample(args.size, seed)
    args.out.write_text(render_page(visits, seed))
    with_track = sum(1 for v in visits if v["track"])
    print(f"Sampled {len(visits)} port calls (seed {seed}); {with_track} have a GPS track.")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()

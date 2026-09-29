"""Observable data loader: the last 3 days of each ship's route, for the map.

Served as data/tracks.parquet. The dashboard draws a ship's route when it is
clicked, and replays the last 24 hours of traffic, both from this file.

Parquet rather than CSV because it is several times smaller, and the page
downloads it whole. It is kept small in three more ways:
- 3 days rather than the 7 in fct_vessel_track;
- a ship lying still repeats the same position every 10 minutes, so only the
  points where it moved more than MIN_MOVE_KM are kept, plus the first and
  last point, and the points either side of a gap in its sightings (so the
  replay can hide a ship while it wasn't seen, instead of freezing it);
- time is whole seconds and positions are rounded to about 10 metres.
"""

import sys
import tempfile
from pathlib import Path

import duckdb

from HarbourOS.storage import connect

DAYS = 3
MIN_MOVE_KM = 0.15
# Longer than this between two sightings counts as a gap (the state machine
# uses the same 30 minutes to decide whether two sightings belong together).
GAP_MINUTES = 30

QUERY = f"""
    with recent as (
        select
            *,
            lag(latitude) over w as prev_lat,
            lag(longitude) over w as prev_lon,
            date_diff('minute', lag(slot_start) over w, slot_start) as minutes_since_prev,
            date_diff('minute', slot_start, lead(slot_start) over w) as minutes_to_next
        from fct_vessel_track
        where slot_start > (select max(slot_start) from fct_vessel_track)
            - interval {DAYS} days
        window w as (partition by mmsi order by slot_start)
    )
    select
        cast(mmsi as integer) as mmsi,
        cast(epoch(message_time) as integer) as t,
        cast(round(latitude, 4) as float) as lat,
        cast(round(longitude, 4) as float) as lon,
        cast(round(speed_over_ground, 1) as float) as sog,
        cast(round(course_over_ground) as smallint) as cog,
        coalesce(minutes_since_prev > {GAP_MINUTES}, true) as after_gap
    from recent
    where prev_lat is null
        or minutes_to_next is null
        or minutes_since_prev > {GAP_MINUTES}
        or minutes_to_next > {GAP_MINUTES}
        or 6371 * 2 * asin(sqrt(
            pow(sin(radians(latitude - prev_lat) / 2), 2)
            + cos(radians(prev_lat)) * cos(radians(latitude))
            * pow(sin(radians(longitude - prev_lon) / 2), 2)
        )) > {MIN_MOVE_KM}
    order by mmsi, t
"""

with connect() as conn:
    points = conn.execute(QUERY).df()

# The warehouse may be MotherDuck, so the Parquet file is written by a local
# DuckDB from the fetched rows.
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "tracks.parquet"
    duckdb.sql(f"copy (select * from points) to '{path}' (format parquet, compression zstd)")
    data = path.read_bytes()

print(f"tracks.parquet: {len(points):,} points, {len(data) / 1e6:.1f} MB", file=sys.stderr)
sys.stdout.buffer.write(data)

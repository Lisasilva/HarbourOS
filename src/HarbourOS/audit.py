"""Data-quality audit of Silver and Gold: a read-only report in Markdown.

Run it against the warehouse to see where the data can't be trusted yet:

    uv run python -m HarbourOS.audit

Every check is a single SELECT. Nothing is written, so it is safe to run at
any time, including while the pipeline is building. In GitHub Actions the
report also lands on the run's summary page (GITHUB_STEP_SUMMARY).

The checks answer four questions:
  1. Is the raw input believable? (quarantine reasons, teleporting ships)
  2. Are we watching often enough? (gaps between sightings of one ship)
  3. Are the port calls believable? (stay lengths, time order, overlaps)
  4. Are they in a port at all? (distance to the nearest known seaport)
"""

import os
import sys
from pathlib import Path

import duckdb

from HarbourOS.storage import DB_PATH, connect

# Great-circle distance in km between (lat1, lon1) and (lat2, lon2), in SQL.
_HAVERSINE = """
    6371 * 2 * asin(sqrt(
        pow(sin(radians({lat2} - {lat1}) / 2), 2)
        + cos(radians({lat1})) * cos(radians({lat2}))
        * pow(sin(radians({lon2} - {lon1}) / 2), 2)
    ))
"""

# Consecutive sightings of the same ship, with the time and distance between.
_STEPS = f"""
    WITH ordered AS (
        SELECT
            mmsi,
            message_time,
            CAST(latitude AS DOUBLE) AS lat,
            CAST(longitude AS DOUBLE) AS lon,
            lag(message_time) OVER w AS prev_time,
            lag(CAST(latitude AS DOUBLE)) OVER w AS prev_lat,
            lag(CAST(longitude AS DOUBLE)) OVER w AS prev_lon
        FROM ais_messages_silver
        WINDOW w AS (PARTITION BY mmsi ORDER BY message_time)
    ),
    steps AS (
        SELECT
            mmsi,
            message_time,
            date_diff('second', prev_time, message_time) / 60.0 AS gap_minutes,
            {_HAVERSINE.format(lat1="prev_lat", lon1="prev_lon", lat2="lat", lon2="lon")}
                AS km
        FROM ordered
        WHERE prev_time IS NOT NULL
    )
"""

# Broad AIS ship-type groups (ITU-R M.1371).
_SHIP_GROUP = """
    CASE
        WHEN v.ship_type = 30 THEN 'fishing'
        WHEN v.ship_type IN (31, 32, 52) THEN 'towing / tug'
        WHEN v.ship_type BETWEEN 50 AND 59 THEN 'pilot, rescue, service'
        WHEN v.ship_type BETWEEN 60 AND 69 THEN 'passenger'
        WHEN v.ship_type BETWEEN 70 AND 79 THEN 'cargo'
        WHEN v.ship_type BETWEEN 80 AND 89 THEN 'tanker'
        WHEN v.ship_type BETWEEN 36 AND 37 THEN 'leisure'
        WHEN v.ship_type IS NULL OR v.ship_type = 0 THEN 'unknown'
        ELSE 'other'
    END
"""

# (section, title, explanation, sql). Each SQL is one read-only SELECT.
CHECKS: list[tuple[str, str, str, str]] = [
    (
        "Overview",
        "What is in the warehouse",
        "Row counts per layer and the time span Silver covers.",
        """
        SELECT 'Bronze (raw)' AS layer, count(*) AS rows FROM ais_messages_bronze
        UNION ALL SELECT 'Silver (clean)', count(*) FROM ais_messages_silver
        UNION ALL SELECT 'Quarantine (rejected)', count(*) FROM ais_messages_quarantine
        UNION ALL SELECT 'State periods', count(*) FROM ship_state_periods
        UNION ALL SELECT 'Port calls (Python)', count(*) FROM port_call_events
        UNION ALL SELECT 'Port calls (Gold fact)', count(*) FROM fact_port_call
        UNION ALL SELECT 'Ships seen', count(DISTINCT mmsi) FROM ais_messages_silver
        """,
    ),
    (
        "Overview",
        "Silver time span",
        "First and last sighting, and how many separate polls fed it per day.",
        """
        SELECT
            CAST(received_at AS DATE) AS day,
            count(DISTINCT received_at) AS polls,
            count(*) AS readings,
            count(DISTINCT mmsi) AS ships,
            min(message_time) AS first_sighting,
            max(message_time) AS last_sighting
        FROM ais_messages_silver
        GROUP BY day
        ORDER BY day
        """,
    ),
    (
        "1. Raw input",
        "Why rows were rejected",
        "Every Bronze row that did not make it into Silver, by reason.",
        """
        SELECT
            rejection_reason,
            count(*) AS rows,
            round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM ais_messages_quarantine
        GROUP BY rejection_reason
        ORDER BY rows DESC
        """,
    ),
    (
        "1. Raw input",
        "Suspicious values that passed into Silver",
        "These rows are in Silver but are unlikely to be real. Norway's waters "
        "are roughly 55-82 N and 15 W-45 E.",
        """
        SELECT 'Outside Norwegian waters' AS problem, count(*) AS rows
        FROM ais_messages_silver
        WHERE latitude NOT BETWEEN 55 AND 82 OR longitude NOT BETWEEN -15 AND 45
        UNION ALL
        SELECT 'Exactly 0,0 (no GPS fix)', count(*)
        FROM ais_messages_silver WHERE latitude = 0 AND longitude = 0
        UNION ALL
        SELECT 'Reported speed over 50 knots', count(*)
        FROM ais_messages_silver WHERE speed_over_ground > 50
        UNION ALL
        SELECT 'Received over 1 day after it was sent', count(*)
        FROM ais_messages_silver
        WHERE received_at - message_time > INTERVAL 1 DAY
        """,
    ),
    (
        "1. Raw input",
        "Ships that teleport",
        "Consecutive sightings that imply moving faster than 50 knots. A few are "
        "GPS glitches; many from one ship usually means two ships share an MMSI.",
        _STEPS
        + """
        SELECT
            count(*) AS impossible_jumps,
            count(DISTINCT mmsi) AS ships_affected,
            round(100.0 * count(*) / (SELECT count(*) FROM steps), 3) AS pct_of_steps
        FROM steps
        WHERE gap_minutes > 0 AND km / (gap_minutes / 60.0) > 50 * 1.852
        """,
    ),
    (
        "2. How often we see each ship",
        "Time between consecutive sightings of the same ship",
        "The state machine only joins sightings up to 30 minutes apart. Gaps "
        "above that split a stay in two or hide it. Only the last 3 days count, "
        "so older data from before 10-minute collection does not skew it.",
        _STEPS
        + """
        SELECT
            CASE
                WHEN gap_minutes <= 11 THEN 'a. up to 11 min'
                WHEN gap_minutes <= 30 THEN 'b. 11-30 min'
                WHEN gap_minutes <= 120 THEN 'c. 30 min - 2 h (breaks a stay)'
                WHEN gap_minutes <= 720 THEN 'd. 2-12 h'
                ELSE 'e. over 12 h'
            END AS gap,
            count(*) AS steps,
            round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM steps
        WHERE message_time >= (SELECT max(message_time) FROM ais_messages_silver)
                              - INTERVAL 3 DAY
        GROUP BY gap
        ORDER BY gap
        """,
    ),
    (
        "2. How often we see each ship",
        "State periods by state",
        "A period built from a single reading is a guess, not an observation.",
        """
        SELECT
            state,
            count(*) AS periods,
            round(avg(n_readings), 1) AS avg_readings,
            round(100.0 * avg(CASE WHEN n_readings = 1 THEN 1 ELSE 0 END), 1)
                AS pct_single_reading,
            count(*) FILTER (WHERE end_time < start_time) AS ends_before_start
        FROM ship_state_periods
        GROUP BY state
        ORDER BY periods DESC
        """,
    ),
    (
        "3. Port calls",
        "How much of each visit we saw",
        "'both_unobserved' means we never saw the ship arrive or leave.",
        """
        SELECT
            completeness,
            count(*) AS port_calls,
            round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct,
            round(avg(confidence), 2) AS avg_confidence
        FROM fact_port_call
        GROUP BY completeness
        ORDER BY port_calls DESC
        """,
    ),
    (
        "3. Port calls",
        "How long ships stayed",
        "Minutes between the first and last stopped sighting. Zero-minute or "
        "multi-week stays are suspicious.",
        """
        SELECT
            CASE
                WHEN minutes_alongside < 0 THEN 'a. negative (bug)'
                WHEN minutes_alongside = 0 THEN 'b. 0 min'
                WHEN minutes_alongside < 10 THEN 'c. 1-9 min'
                WHEN minutes_alongside < 60 THEN 'd. 10-59 min'
                WHEN minutes_alongside < 360 THEN 'e. 1-6 h'
                WHEN minutes_alongside < 1440 THEN 'f. 6-24 h'
                WHEN minutes_alongside < 10080 THEN 'g. 1-7 days'
                ELSE 'h. over 7 days'
            END AS stay,
            count(*) AS port_calls,
            round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct,
            count(*) FILTER (WHERE completeness = 'complete') AS fully_observed
        FROM fact_port_call
        GROUP BY stay
        ORDER BY stay
        """,
    ),
    (
        "3. Port calls",
        "Impossible time order",
        "Each should be zero.",
        """
        SELECT 'Arrives after it stopped' AS problem, count(*) AS port_calls
        FROM fact_port_call WHERE arrival_time > berth_start
        UNION ALL
        SELECT 'Stop ends before it starts', count(*)
        FROM fact_port_call WHERE berth_end < berth_start
        UNION ALL
        SELECT 'Leaves before the stop ends', count(*)
        FROM fact_port_call WHERE departure_time < berth_end
        UNION ALL
        SELECT 'Overlaps the same ship''s next visit', count(*)
        FROM (
            SELECT berth_end, lead(berth_start) OVER (
                PARTITION BY mmsi ORDER BY berth_start
            ) AS next_start
            FROM fact_port_call
        )
        WHERE next_start < berth_end
        UNION ALL
        SELECT 'In Python table but not in Gold', count(*)
        FROM port_call_events AS e
        ANTI JOIN fact_port_call AS f USING (mmsi, berth_start)
        UNION ALL
        SELECT 'No stop position (no Silver rows inside)', count(*)
        FROM fact_port_call WHERE stop_latitude IS NULL
        """,
    ),
    (
        "4. Are they in a port?",
        "Distance to the nearest known seaport",
        "Stops further than 10 km from any UN/LOCODE seaport get no port. "
        "UN/LOCODE marks the town, not the quay, so up to ~5 km is normal.",
        """
        SELECT
            CASE
                WHEN nearest_port_km IS NULL THEN 'g. no position'
                WHEN nearest_port_km <= 2 THEN 'a. 0-2 km'
                WHEN nearest_port_km <= 5 THEN 'b. 2-5 km'
                WHEN nearest_port_km <= 10 THEN 'c. 5-10 km'
                WHEN nearest_port_km <= 25 THEN 'd. 10-25 km (no port)'
                WHEN nearest_port_km <= 50 THEN 'e. 25-50 km (no port)'
                ELSE 'f. over 50 km (at sea)'
            END AS distance,
            count(*) AS port_calls,
            round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM fact_port_call
        GROUP BY distance
        ORDER BY distance
        """,
    ),
    (
        "4. Are they in a port?",
        "Who stops away from ports",
        "Stops with no port within 10 km, by kind of ship. Fishing boats "
        "drifting on a fishing ground or ships waiting at an offshore anchorage "
        "are real stops, but not port calls.",
        f"""
        SELECT
            {_SHIP_GROUP} AS ship_kind,
            count(*) AS port_calls,
            count(*) FILTER (WHERE f.port_locode IS NULL) AS away_from_port,
            round(100.0 * count(*) FILTER (WHERE f.port_locode IS NULL)
                  / count(*), 1) AS pct_away,
            count(*) FILTER (WHERE f.port_locode IS NULL AND f.stop_type = 'anchored')
                AS away_and_anchored
        FROM fact_port_call AS f
        LEFT JOIN dim_vessel AS v USING (mmsi)
        GROUP BY ship_kind
        ORDER BY port_calls DESC
        """,
    ),
    (
        "4. Are they in a port?",
        "Farthest 'port calls' from any port",
        "Examples to look up on a map.",
        """
        SELECT
            f.mmsi,
            v.vessel_name,
            v.ship_type,
            f.stop_type,
            f.berth_start,
            f.minutes_alongside,
            round(f.stop_latitude, 3) AS lat,
            round(f.stop_longitude, 3) AS lon,
            f.nearest_port_km
        FROM fact_port_call AS f
        LEFT JOIN dim_vessel AS v USING (mmsi)
        WHERE f.nearest_port_km IS NOT NULL
        ORDER BY f.nearest_port_km DESC
        LIMIT 10
        """,
    ),
    (
        "4. Are they in a port?",
        "Busiest ports",
        "A sanity check: the big names (Bergen, Oslo, Stavanger...) should lead.",
        """
        SELECT
            p.port_name,
            f.port_locode,
            count(*) AS port_calls,
            round(median(f.nearest_port_km), 1) AS median_km
        FROM fact_port_call AS f
        JOIN dim_port AS p USING (port_locode)
        GROUP BY ALL
        ORDER BY port_calls DESC
        LIMIT 15
        """,
    ),
]


def _markdown_table(columns: list[str], rows: list[tuple]) -> str:
    """A GitHub-flavoured Markdown table."""
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    return "\n".join(lines)


def run_audit(db_path: Path | str = DB_PATH) -> str:
    """Run every check and return the report as Markdown.

    A check that fails (say, a table that doesn't exist yet) is reported in
    place, so one broken check never hides the others.
    """
    conn = connect(db_path)
    parts = ["# HarbourOS data-quality audit"]
    section = None
    try:
        for check_section, title, explanation, sql in CHECKS:
            if check_section != section:
                section = check_section
                parts.append(f"## {section}")
            parts.append(f"### {title}\n\n{explanation}")
            try:
                result = conn.sql(sql)
                parts.append(_markdown_table(result.columns, result.fetchall()))
            except duckdb.Error as error:
                parts.append(f"_Check failed: {error}_")
    finally:
        conn.close()
    return "\n\n".join(parts) + "\n"


def main() -> None:
    report = run_audit()
    sys.stdout.write(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)


if __name__ == "__main__":
    main()

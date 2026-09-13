from datetime import datetime
from pathlib import Path

import duckdb

# DuckDB database file location defined
DB_PATH = Path("data/ais_bronze.duckdb")


# ---------------------------------------------------------------------------
# Bronze
# ---------------------------------------------------------------------------


def initialize_bronze_table(db_path: Path = DB_PATH) -> None:
    """Create the Bronze layer table in DuckDB"""
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ais_messages_bronze (
            mmsi INTEGER,
            name VARCHAR,
            latitude DECIMAL(10, 6),
            longitude DECIMAL(10, 6),
            speedOverGround DECIMAL(10, 2),
            courseOverGround DECIMAL(10, 2),
            trueHeading DECIMAL(10, 2),
            rateOfTurn DECIMAL(10, 2),
            shipType INTEGER,
            navigationalStatus INTEGER,
            stream VARCHAR,
            msgtime TIMESTAMP,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.close()
    print(f"✅ Database initialized: {db_path}")


def insert_ais_message(data: dict, db_path: Path = DB_PATH) -> None:
    """Insert a single AIS message into the database"""
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO ais_messages_bronze
        (mmsi, name, latitude, longitude, speedOverGround, courseOverGround,
         trueHeading, rateOfTurn, shipType, navigationalStatus, stream, msgtime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        [
            data.get("mmsi"),
            data.get("name"),
            data.get("latitude"),
            data.get("longitude"),
            data.get("speedOverGround"),
            data.get("courseOverGround"),
            data.get("trueHeading"),
            data.get("rateOfTurn"),
            data.get("shipType"),
            data.get("navigationalStatus"),
            data.get("stream"),
            data.get("msgtime"),
        ],
    )
    conn.close()


def insert_ais_messages(messages: list[dict], db_path: Path = DB_PATH) -> int:
    """Insert a whole batch of AIS messages in a single database write.

    Every row in the batch gets the SAME received_at, set here in Python rather
    than left to the column default. That makes a batch identifiable as one unit
    of work, which is what the incremental transforms use to find rows they have
    not processed yet.
    """
    if not messages:
        return 0

    received_at = datetime.now()

    rows = [
        [
            message.get("mmsi"),
            message.get("name"),
            message.get("latitude"),
            message.get("longitude"),
            message.get("speedOverGround"),
            message.get("courseOverGround"),
            message.get("trueHeading"),
            message.get("rateOfTurn"),
            message.get("shipType"),
            message.get("navigationalStatus"),
            message.get("stream"),
            message.get("msgtime"),
            received_at,
        ]
        for message in messages
    ]

    conn = duckdb.connect(str(db_path))
    try:
        conn.executemany(
            """
            INSERT INTO ais_messages_bronze
            (mmsi, name, latitude, longitude, speedOverGround, courseOverGround,
             trueHeading, rateOfTurn, shipType, navigationalStatus, stream, msgtime,
             received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    finally:
        conn.close()

    return len(rows)


# ---------------------------------------------------------------------------
# Silver
# ---------------------------------------------------------------------------


def initialize_silver_tables(db_path: Path = DB_PATH) -> None:
    """Create empty Silver and Quarantine tables if they don't exist yet.

    Their shape is taken from Bronze with 'WHERE FALSE' rather than hand-written
    DDL, so the empty table can never disagree with the query that fills it.
    """
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ais_messages_silver AS
        SELECT
            mmsi,
            name,
            latitude,
            longitude,
            speedOverGround     AS speed_over_ground,
            courseOverGround    AS course_over_ground,
            trueHeading         AS true_heading,
            rateOfTurn          AS rate_of_turn,
            shipType            AS ship_type,
            navigationalStatus  AS navigational_status,
            stream,
            msgtime             AS message_time,
            received_at
        FROM ais_messages_bronze
        WHERE FALSE
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ais_messages_quarantine AS
        SELECT b.*, CAST(NULL AS VARCHAR) AS rejection_reason
        FROM ais_messages_bronze AS b
        WHERE FALSE
        """
    )
    conn.close()


# ---------------------------------------------------------------------------
# Progress bookkeeping for the per-vessel layers
# ---------------------------------------------------------------------------


def initialize_progress_table(db_path: Path = DB_PATH) -> None:
    """Create the table that records how far each derived layer has been built.

    One row per (layer, ship). Silver needs no such table -- its high-water mark
    is derivable, because every Bronze row produces a row somewhere. The vessel
    layers do need it, because a ship can legitimately produce no port calls at
    all, so 'no rows for this ship' is not evidence the ship was never processed.
    """
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_progress (
            layer VARCHAR,
            mmsi INTEGER,
            built_from_received_at TIMESTAMP
        )
    """
    )
    conn.close()


def record_progress(
    layer: str,
    watermarks: dict[int, datetime],
    db_path: Path = DB_PATH,
) -> None:
    """Record, for each ship just processed, the newest input it was built from."""
    if not watermarks:
        return

    conn = duckdb.connect(str(db_path))
    placeholders = ", ".join("?" for _ in watermarks)
    conn.execute(
        f"DELETE FROM derived_progress WHERE layer = ? AND mmsi IN ({placeholders})",
        [layer, *watermarks.keys()],
    )
    conn.executemany(
        """
        INSERT INTO derived_progress (layer, mmsi, built_from_received_at)
        VALUES (?, ?, ?)
        """,
        [[layer, mmsi, built_from] for mmsi, built_from in watermarks.items()],
    )
    conn.close()


# ---------------------------------------------------------------------------
# State periods
# ---------------------------------------------------------------------------


def initialize_state_periods_table(db_path: Path = DB_PATH) -> None:
    """Create the derived state-period table if it doesn't exist yet."""
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ship_state_periods (
            mmsi INTEGER,
            state VARCHAR,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            n_readings INTEGER,
            confidence DECIMAL(3, 2),
            note VARCHAR
        )
    """
    )
    conn.close()


def replace_state_periods_for_ships(
    periods: list,
    mmsi_list: list[int],
    db_path: Path = DB_PATH,
) -> None:
    """Swap in freshly derived periods for the named ships, leaving others alone."""
    if not mmsi_list:
        return

    conn = duckdb.connect(str(db_path))
    placeholders = ", ".join("?" for _ in mmsi_list)
    conn.execute(
        f"DELETE FROM ship_state_periods WHERE mmsi IN ({placeholders})",
        list(mmsi_list),
    )
    if periods:
        conn.executemany(
            """
            INSERT INTO ship_state_periods
            (mmsi, state, start_time, end_time, n_readings, confidence, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    period.mmsi,
                    period.state,
                    period.start_time,
                    period.end_time,
                    period.n_readings,
                    period.confidence,
                    period.note,
                ]
                for period in periods
            ],
        )
    conn.close()


# ---------------------------------------------------------------------------
# Port calls
# ---------------------------------------------------------------------------


def initialize_port_calls_table(db_path: Path = DB_PATH) -> None:
    """Create the port-call event table if it doesn't exist yet."""
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS port_call_events (
            mmsi INTEGER,
            stop_type VARCHAR,
            arrival_time TIMESTAMP,
            berth_start TIMESTAMP,
            berth_end TIMESTAMP,
            departure_time TIMESTAMP,
            minutes_alongside INTEGER,
            n_readings INTEGER,
            confidence DECIMAL(3, 2),
            completeness VARCHAR
        )
    """
    )
    conn.close()


def replace_port_calls_for_ships(
    calls: list,
    mmsi_list: list[int],
    db_path: Path = DB_PATH,
) -> None:
    """Swap in freshly derived port calls for the named ships, leaving others alone."""
    if not mmsi_list:
        return

    conn = duckdb.connect(str(db_path))
    placeholders = ", ".join("?" for _ in mmsi_list)
    conn.execute(
        f"DELETE FROM port_call_events WHERE mmsi IN ({placeholders})",
        list(mmsi_list),
    )
    if calls:
        conn.executemany(
            """
            INSERT INTO port_call_events
            (mmsi, stop_type, arrival_time, berth_start, berth_end, departure_time,
             minutes_alongside, n_readings, confidence, completeness)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    call.mmsi,
                    call.stop_type,
                    call.arrival_time,
                    call.berth_start,
                    call.berth_end,
                    call.departure_time,
                    call.minutes_alongside,
                    call.n_readings,
                    call.confidence,
                    call.completeness,
                ]
                for call in calls
            ],
        )
    conn.close()


if __name__ == "__main__":
    initialize_bronze_table()

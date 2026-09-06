from pathlib import Path

import duckdb

# DuckDB database file location defined
DB_PATH = Path("data/ais_bronze.duckdb")


def initialize_bronze_table(db_path: Path = DB_PATH) -> None:
    """Create the Bronze layer table in DuckDB"""
    conn = duckdb.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS ais_messages_bronze")
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


def initialize_state_periods_table(db_path: Path = DB_PATH) -> None:
    """Create the derived state-period table (full refresh, like Silver)."""
    conn = duckdb.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS ship_state_periods")
    conn.execute(
        """
        CREATE TABLE ship_state_periods (
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


def insert_state_periods(periods: list, db_path: Path = DB_PATH) -> None:
    """Bulk-insert derived state periods."""
    if not periods:
        return

    conn = duckdb.connect(str(db_path))
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


def initialize_port_calls_table(db_path: Path = DB_PATH) -> None:
    """Create the port-call event table (full refresh, like the other layers)."""
    conn = duckdb.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS port_call_events")
    conn.execute(
        """
        CREATE TABLE port_call_events (
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


def insert_port_calls(calls: list, db_path: Path = DB_PATH) -> None:
    """Bulk-insert port-call events."""
    if not calls:
        return

    conn = duckdb.connect(str(db_path))
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

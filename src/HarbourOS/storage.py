from pathlib import Path

import duckdb

# database file location defined
DB_PATH = Path("data/ais_bronze.duckdb")


def initialize_bronze_table():
    """Create the Bronze layer table in DuckDB"""

    # DuckDB database connection
    conn = duckdb.connect(str(DB_PATH))

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
    print(f"✅ Database initialized: {DB_PATH}")


def insert_ais_message(data):
    """Insert a single AIS message into the database"""

    conn = duckdb.connect(str(DB_PATH))

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


if __name__ == "__main__":
    initialize_bronze_table()

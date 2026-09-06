"""Silver layer orchestration: run SQL transforms against the Bronze table."""

from pathlib import Path

import duckdb

from HarbourOS.state_machine import derive_state_periods
from HarbourOS.storage import initialize_state_periods_table, insert_state_periods

DB_PATH = Path("data/ais_bronze.duckdb")
SQL_DIR = Path("sql")


def run_sql_file(con: duckdb.DuckDBPyConnection, filename: str) -> None:
    """Read a .sql file and execute its contents against the given connection."""
    sql = (SQL_DIR / filename).read_text()
    con.execute(sql)


def run_silver_transform(db_path: Path = DB_PATH) -> None:
    """Build the Silver and Quarantine tables from Bronze, then report the results."""
    con = duckdb.connect(str(db_path))
    bronze_count = con.sql("SELECT COUNT(*) FROM ais_messages_bronze").fetchone()[0]

    run_sql_file(con, "silver_ais_positions.sql")
    run_sql_file(con, "quarantine_ais_positions.sql")

    silver_count = con.sql("SELECT COUNT(*) FROM ais_messages_silver").fetchone()[0]
    quarantine_count = con.sql("SELECT COUNT(*) FROM ais_messages_quarantine").fetchone()[0]

    print(f"Bronze rows:      {bronze_count}")
    print(f"Silver rows:      {silver_count}")
    print(f"Quarantine rows:  {quarantine_count}")
    print(f"Accounted for:    {silver_count + quarantine_count} (should equal Bronze rows)")

    print("\nQuarantine breakdown by reason:")
    breakdown = con.sql(
        "SELECT rejection_reason, COUNT(*) AS n FROM ais_messages_quarantine "
        "GROUP BY rejection_reason ORDER BY n DESC"
    ).fetchall()
    for reason, count in breakdown:
        print(f"  {reason}: {count}")

    con.close()


def run_state_periods_transform(db_path: Path = DB_PATH) -> None:
    """Derive confidence-scored state periods for every ship in Silver."""
    con = duckdb.connect(str(db_path))
    mmsi_list = [
        row[0]
        for row in con.sql("SELECT DISTINCT mmsi FROM ais_messages_silver ORDER BY mmsi").fetchall()
    ]

    all_periods = []
    readings_seen = 0
    for mmsi in mmsi_list:
        rows = con.sql(
            f"""
            SELECT message_time, speed_over_ground, navigational_status
            FROM ais_messages_silver
            WHERE mmsi = {mmsi}
            ORDER BY message_time
            """
        ).fetchall()
        messages = [
            {
                "message_time": row[0],
                "speed_over_ground": row[1],
                "navigational_status": row[2],
            }
            for row in rows
        ]
        readings_seen += len(messages)
        all_periods.extend(derive_state_periods(messages, mmsi=mmsi))
    con.close()

    initialize_state_periods_table(db_path=db_path)
    insert_state_periods(all_periods, db_path=db_path)

    covered = sum(period.n_readings for period in all_periods)
    print(f"Ships processed:  {len(mmsi_list)}")
    print(f"Silver readings:  {readings_seen}")
    print(f"State periods:    {len(all_periods)}")
    print(f"Readings covered: {covered} (should equal Silver readings)")


if __name__ == "__main__":
    run_silver_transform()
    print()
    run_state_periods_transform()

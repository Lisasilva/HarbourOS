"""Orchestration: build each derived layer and report what happened."""

from pathlib import Path

import duckdb

from HarbourOS.port_calls import derive_port_calls
from HarbourOS.state_machine import StatePeriod, derive_state_periods
from HarbourOS.storage import (
    initialize_port_calls_table,
    initialize_silver_tables,
    initialize_state_periods_table,
    insert_port_calls,
    insert_state_periods,
)

DB_PATH = Path("data/ais_bronze.duckdb")
SQL_DIR = Path("sql")


def run_sql_file(con: duckdb.DuckDBPyConnection, filename: str) -> None:
    """Read a .sql file and execute its contents against the given connection."""
    sql = (SQL_DIR / filename).read_text()
    con.execute(sql)


def run_silver_transform(db_path: Path = DB_PATH) -> None:
    """Add newly-arrived Bronze rows to Silver and Quarantine.

    Incremental: only Bronze rows received later than the newest row already in
    Silver or Quarantine are considered. That high-water mark is read straight
    from the data, so there is no bookkeeping table to fall out of sync.
    """
    initialize_silver_tables(db_path=db_path)

    con = duckdb.connect(str(db_path))

    run_sql_file(con, "silver_stage_new_batch.sql")
    new_rows = con.sql("SELECT COUNT(*) FROM new_bronze_batch").fetchone()[0]

    run_sql_file(con, "silver_insert_accepted.sql")
    run_sql_file(con, "silver_insert_rejected.sql")

    bronze_count = con.sql("SELECT COUNT(*) FROM ais_messages_bronze").fetchone()[0]
    silver_count = con.sql("SELECT COUNT(*) FROM ais_messages_silver").fetchone()[0]
    quarantine_count = con.sql("SELECT COUNT(*) FROM ais_messages_quarantine").fetchone()[0]

    print(f"New rows this run: {new_rows}")
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

    if silver_count + quarantine_count != bronze_count:
        raise ValueError(
            "Rows went missing: "
            f"Silver {silver_count} + Quarantine {quarantine_count} "
            f"= {silver_count + quarantine_count}, but Bronze has {bronze_count}"
        )


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


def run_port_calls_transform(db_path: Path = DB_PATH) -> None:
    """Group state periods into port-call events, one row per visit."""
    con = duckdb.connect(str(db_path))
    rows = con.sql(
        """
        SELECT mmsi, state, start_time, end_time, n_readings, confidence, note
        FROM ship_state_periods
        ORDER BY mmsi, start_time
        """
    ).fetchall()
    con.close()

    by_ship: dict[int, list[StatePeriod]] = {}
    for row in rows:
        period = StatePeriod(
            mmsi=row[0],
            state=row[1],
            start_time=row[2],
            end_time=row[3],
            n_readings=row[4],
            confidence=float(row[5]),
            note=row[6],
        )
        by_ship.setdefault(period.mmsi, []).append(period)

    all_calls = []
    for periods in by_ship.values():
        all_calls.extend(derive_port_calls(periods))

    initialize_port_calls_table(db_path=db_path)
    insert_port_calls(all_calls, db_path=db_path)

    complete = sum(1 for call in all_calls if call.completeness == "complete")
    print(f"Port-call events: {len(all_calls)}")
    print(f"Fully observed:   {complete} (both arrival and departure seen)")


if __name__ == "__main__":
    run_silver_transform()
    print()
    run_state_periods_transform()
    print()
    run_port_calls_transform()

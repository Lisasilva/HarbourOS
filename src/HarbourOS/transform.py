"""Orchestration: build each derived layer and report what happened."""

from pathlib import Path

import duckdb

from HarbourOS.port_calls import derive_port_calls
from HarbourOS.state_machine import StatePeriod, derive_state_periods
from HarbourOS.storage import (
    initialize_port_calls_table,
    initialize_progress_table,
    initialize_silver_tables,
    initialize_state_periods_table,
    record_progress,
    replace_port_calls_for_ships,
    replace_state_periods_for_ships,
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
    """Rebuild state periods for the ships that have new Silver readings.

    A ship's states depend only on that ship's own readings, so one vessel is the
    smallest chunk that can be recomputed without risking a wrong answer. Ships
    with nothing new keep the periods they already have.
    """
    initialize_state_periods_table(db_path=db_path)
    initialize_progress_table(db_path=db_path)

    con = duckdb.connect(str(db_path))
    stale = con.sql(
        """
        SELECT s.mmsi, max(s.received_at) AS newest_reading
        FROM ais_messages_silver AS s
        LEFT JOIN derived_progress AS p
               ON p.mmsi = s.mmsi AND p.layer = 'state_periods'
        GROUP BY s.mmsi, p.built_from_received_at
        HAVING p.built_from_received_at IS NULL
            OR max(s.received_at) > p.built_from_received_at
        ORDER BY s.mmsi
        """
    ).fetchall()
    watermarks = {row[0]: row[1] for row in stale}

    all_periods = []
    readings_seen = 0
    for mmsi in watermarks:
        rows = con.execute(
            """
            SELECT message_time, speed_over_ground, navigational_status
            FROM ais_messages_silver
            WHERE mmsi = ?
            ORDER BY message_time
            """,
            [mmsi],
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

    replace_state_periods_for_ships(all_periods, list(watermarks), db_path=db_path)
    record_progress("state_periods", watermarks, db_path=db_path)

    con = duckdb.connect(str(db_path))
    total_periods = con.sql("SELECT COUNT(*) FROM ship_state_periods").fetchone()[0]
    covered = con.sql("SELECT COALESCE(SUM(n_readings), 0) FROM ship_state_periods").fetchone()[0]
    silver_rows = con.sql("SELECT COUNT(*) FROM ais_messages_silver").fetchone()[0]
    con.close()

    print(f"Ships rebuilt:    {len(watermarks)}")
    print(f"Readings read:    {readings_seen}")
    print(f"State periods:    {total_periods} (all ships)")
    print(f"Readings covered: {covered} (should equal Silver rows: {silver_rows})")

    if covered != silver_rows:
        raise ValueError(f"State periods cover {covered} readings but Silver holds {silver_rows}")


def run_port_calls_transform(db_path: Path = DB_PATH) -> None:
    """Rebuild port calls for the ships whose state periods have changed."""
    initialize_port_calls_table(db_path=db_path)
    initialize_progress_table(db_path=db_path)

    con = duckdb.connect(str(db_path))
    stale = con.sql(
        """
        SELECT sp.mmsi, sp.built_from_received_at
        FROM derived_progress AS sp
        LEFT JOIN derived_progress AS pc
               ON pc.mmsi = sp.mmsi AND pc.layer = 'port_calls'
        WHERE sp.layer = 'state_periods'
          AND (pc.built_from_received_at IS NULL
               OR sp.built_from_received_at > pc.built_from_received_at)
        ORDER BY sp.mmsi
        """
    ).fetchall()
    watermarks = {row[0]: row[1] for row in stale}

    rows = []
    if watermarks:
        placeholders = ", ".join("?" for _ in watermarks)
        rows = con.execute(
            f"""
            SELECT mmsi, state, start_time, end_time, n_readings, confidence, note
            FROM ship_state_periods
            WHERE mmsi IN ({placeholders})
            ORDER BY mmsi, start_time
            """,
            list(watermarks),
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

    replace_port_calls_for_ships(all_calls, list(watermarks), db_path=db_path)
    record_progress("port_calls", watermarks, db_path=db_path)

    con = duckdb.connect(str(db_path))
    total_calls = con.sql("SELECT COUNT(*) FROM port_call_events").fetchone()[0]
    complete = con.sql(
        "SELECT COUNT(*) FROM port_call_events WHERE completeness = 'complete'"
    ).fetchone()[0]
    con.close()

    print(f"Ships rebuilt:    {len(watermarks)}")
    print(f"Port-call events: {total_calls} (all ships)")
    print(f"Fully observed:   {complete}")


if __name__ == "__main__":
    run_silver_transform()
    print()
    run_state_periods_transform()
    print()
    run_port_calls_transform()

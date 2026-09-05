"""Silver layer orchestration: run SQL transforms against the Bronze table."""

from pathlib import Path

import duckdb

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


if __name__ == "__main__":
    run_silver_transform()

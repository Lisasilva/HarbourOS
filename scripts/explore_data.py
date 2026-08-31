"""Explore any table in the database - print sample rows to the terminal.

Usage:
    python scripts/explore_data.py <table_name> [row_limit]

Examples:
    python scripts/explore_data.py ais_messages_bronze
    python scripts/explore_data.py ais_messages_silver
    python scripts/explore_data.py ais_messages_quarantine 20
"""

import sys
from pathlib import Path

import duckdb

DB_PATH = Path("data/ais_bronze.duckdb")


def explore(table_name: str, limit: int = 10) -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.sql(f"SELECT * FROM {table_name} LIMIT {limit}").fetchdf()
    print(f"\n=== {table_name} (showing {len(df)} of possibly more rows) ===")
    print(df.to_string())
    con.close()


if __name__ == "__main__":
    table = sys.argv[1] if len(sys.argv) > 1 else "ais_messages_bronze"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    explore(table, limit)

"""One-off: copy the Python-owned tables from the local warehouse to MotherDuck.

Only the tables Python writes are copied. Everything dbt builds -- the seed, the
staging views, the dimensions and the fact table -- is rebuilt by 'dbt build'
afterwards, because copying something a command can regenerate is just another
thing that can silently go stale.
"""

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

LOCAL_DB = Path("data/ais_bronze.duckdb")
CLOUD_DB = "harbouros"

TABLES = [
    "ais_messages_bronze",
    "ais_messages_silver",
    "ais_messages_quarantine",
    "ship_state_periods",
    "port_call_events",
    "derived_progress",
]


def main() -> None:
    token = os.environ["MOTHERDUCK_TOKEN"]

    con = duckdb.connect(f"md:?motherduck_token={token}")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {CLOUD_DB}")
    con.execute(f"ATTACH '{LOCAL_DB}' AS local_db (READ_ONLY)")

    for table in TABLES:
        con.execute(
            f"CREATE OR REPLACE TABLE {CLOUD_DB}.main.{table} AS "
            f"SELECT * FROM local_db.main.{table}"
        )
        local_rows = con.sql(f"SELECT COUNT(*) FROM local_db.main.{table}").fetchone()[0]
        cloud_rows = con.sql(f"SELECT COUNT(*) FROM {CLOUD_DB}.main.{table}").fetchone()[0]
        verdict = "OK" if local_rows == cloud_rows else "MISMATCH"
        print(f"{table:<28} local {local_rows:>7}  ->  cloud {cloud_rows:>7}   [{verdict}]")

    con.close()


if __name__ == "__main__":
    main()

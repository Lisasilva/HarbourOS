"""Diagnostic to inspect the Bronze layer's actual schema and row count."""

import duckdb

con = duckdb.connect("data/ais_bronze.duckdb")

print("=== TABLES ===")
print(con.sql("SHOW TABLES").fetchall())

print("\n=== SCHEMA ===")
tables = con.sql("SHOW TABLES").fetchall()
for (table_name,) in tables:
    print(f"\n--- {table_name} ---")
    print(con.sql(f"DESCRIBE {table_name}").fetchdf())

print("\n=== ROW COUNTS ===")
for (table_name,) in tables:
    count = con.sql(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"{table_name}: {count} rows")

con.close()
